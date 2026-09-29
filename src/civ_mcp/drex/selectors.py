"""Selectors turn a DecisionPoint into a validated Decision.

Only ``DrexSelector`` is used for real play. ``RandomSelector`` is an
explicitly labeled baseline over the same candidates, and ``ReplaySelector``
re-validates recorded answers offline.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from civ_mcp.drex.candidates import DecisionPoint
from civ_mcp.drex.client import DrexError
from civ_mcp.drex.decision import (
    ChoiceAnswer,
    Decision,
    DecisionError,
    forced_decision,
    resolve_choice,
    validate_selection,
)


class ControllerFilteringError(Exception):
    """Exactly one candidate survived controller filtering with no forced rule.

    A controller bug, not an API state: the decision is dropped, never
    executed without a choice."""


class ReplayRejected(Exception):
    """Offline replay: the recorded answer is missing or invalid."""


@dataclass
class SelectionResult:
    decision: Decision
    request: dict[str, Any] | None = None
    attempts: list[dict[str, Any]] = field(default_factory=list)


class Selector(Protocol):
    name: str

    async def choose(self, point: DecisionPoint) -> SelectionResult: ...


class ChoiceClient(Protocol):
    async def choose(
        self,
        *,
        state: Any,
        instructions: str | Mapping[str, Any],
        options: Mapping[str, Any],
    ) -> ChoiceAnswer: ...


def _fact_value(value: Any) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (list, tuple)):
        return ", ".join(_fact_value(v) for v in value)
    if isinstance(value, dict):
        return ", ".join(f"{k} {_fact_value(v)}" for k, v in value.items())
    return str(value)


def describe_facts(facts: Mapping[str, Any]) -> str | None:
    """Drex accepts only a string or null per option, so facts become one
    ``key: value; ...`` line in their stored order; empty values are omitted."""
    parts = [
        f"{k}: {_fact_value(v)}"
        for k, v in facts.items()
        if v is not None and v != "" and v != [] and v != {}
    ]
    return "; ".join(parts) or None


def build_request(point: DecisionPoint) -> dict[str, Any]:
    return {
        "state": dict(point.context),
        "instructions": point.question,
        "options": {c.label: describe_facts(c.facts) for c in point.candidates},
    }


class DrexSelector:
    """Asks Drex until it answers with a valid choice.

    Transient failures (429/5xx/529, timeouts, transport errors, malformed
    bodies or answers) retry with exponential backoff capped at
    ``max_backoff_s``. Non-retryable failures (bad key, rejected request,
    model mismatch) wait ``max_backoff_s`` and, when a ``refresh_client``
    callable is given, rebuild the client so a rotated key or changed base URL
    is picked up without a restart. Nothing is ever decided without Drex.
    """

    name = "drex"

    def __init__(
        self,
        client: ChoiceClient,
        *,
        backoff_s: float = 1.0,
        max_backoff_s: float = 60.0,
        warn_after: int = 2,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
        on_wait: Callable[[dict[str, Any]], None] | None = None,
        refresh_client: Callable[[], Awaitable[ChoiceClient]] | None = None,
    ):
        self._client = client
        self._backoff_s = backoff_s
        self._max_backoff_s = max_backoff_s
        self._warn_after = warn_after
        self._sleep = sleep
        self._on_wait = on_wait
        self._refresh_client = refresh_client

    def _wait_for(self, n: int) -> float:
        return min(self._backoff_s * (2**n), self._max_backoff_s)

    async def choose(self, point: DecisionPoint) -> SelectionResult:
        request = build_request(point)
        attempts: list[dict[str, Any]] = []
        n = 0
        while True:
            try:
                answer = await self._client.choose(**request)
                decision = resolve_choice(point, answer, selector=self.name)
            except DecisionError as e:
                wait, retryable = self._wait_for(n), True
                err, cls = f"answer: {e}", "DecisionError"
            except DrexError as e:
                retryable = bool(getattr(e, "retryable", False))
                retry_after = getattr(e, "retry_after_s", None) or 0.0
                if retryable:
                    wait = min(max(self._wait_for(n), retry_after), self._max_backoff_s)
                else:
                    wait = self._max_backoff_s
                err, cls = f"{type(e).__name__}: {e}", type(e).__name__
            else:
                attempts.append(
                    {
                        "attempt": n + 1,
                        "ok": True,
                        "latency_ms": answer.latency_ms,
                        "request_id": answer.request_id,
                    }
                )
                return SelectionResult(decision, request, attempts)
            n += 1
            attempts.append({"attempt": n, "ok": False, "error": err})
            if len(attempts) > 50:
                attempts = attempts[-50:]  # bounded record on long outages
            if self._on_wait is not None:
                self._on_wait(
                    {
                        "attempt": n,
                        "error": err,
                        "error_class": cls,
                        "sleep_s": wait,
                        "retryable": retryable,
                        "warn": n >= self._warn_after,
                    }
                )
            await self._sleep(wait)
            if not retryable and self._refresh_client is not None:
                self._client = await self._refresh_client()


class RandomSelector:
    """Offline/baseline only: uniform over the same candidate set, seeded."""

    name = "random-baseline"

    def __init__(self, seed: int):
        self._rng = random.Random(seed)

    async def choose(self, point: DecisionPoint) -> SelectionResult:
        ids = sorted(c.candidate_id for c in point.candidates)
        pick = self._rng.choice(ids)
        decision = validate_selection(
            point, pick, selector=self.name, rule="uniform_random_seeded"
        )
        return SelectionResult(decision)


class ReplaySelector:
    """Replays recorded answers (probabilities or candidate ids) by decision id."""

    name = "replay"

    def __init__(self, recorded: Mapping[str, ChoiceAnswer | str]):
        self._recorded = dict(recorded)

    async def choose(self, point: DecisionPoint) -> SelectionResult:
        rec = self._recorded.get(point.decision_id)
        if rec is None:
            raise ReplayRejected(f"no recorded answer for {point.decision_id}")
        try:
            if isinstance(rec, str):
                decision = validate_selection(point, rec, selector=self.name)
            else:
                decision = resolve_choice(point, rec, selector=self.name)
        except DecisionError as e:
            raise ReplayRejected(f"recorded answer invalid: {e}") from e
        return SelectionResult(decision)


async def select(point: DecisionPoint, selector: Selector) -> SelectionResult:
    if len(point.candidates) == 1:
        if point.forced_rule is None:
            raise ControllerFilteringError(
                f"{point.decision_id}: one candidate left after controller filtering "
                f"of {point.legal_count} legal options; not executed without a choice"
            )
        return SelectionResult(forced_decision(point))
    return await selector.choose(point)
