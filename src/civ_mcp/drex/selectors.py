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


class SelectionPaused(Exception):
    """No valid decision within the retry bound; the run must pause."""

    def __init__(self, reason: str, attempts: list[dict[str, Any]] | None = None):
        super().__init__(reason)
        self.attempts = attempts or []


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
    name = "drex"

    def __init__(
        self,
        client: ChoiceClient,
        *,
        max_retries: int = 2,
        backoff_s: float = 1.0,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    ):
        self._client = client
        self._max_retries = max_retries
        self._backoff_s = backoff_s
        self._sleep = sleep

    async def choose(self, point: DecisionPoint) -> SelectionResult:
        request = build_request(point)
        attempts: list[dict[str, Any]] = []
        for n in range(1 + self._max_retries):
            wait = self._backoff_s * (2**n)
            try:
                answer = await self._client.choose(**request)
                decision = resolve_choice(point, answer, selector=self.name)
            except DecisionError as e:
                attempts.append(
                    {"attempt": n + 1, "ok": False, "error": f"answer: {e}"}
                )
            except DrexError as e:
                attempts.append(
                    {"attempt": n + 1, "ok": False, "error": f"{type(e).__name__}: {e}"}
                )
                if not e.retryable:
                    raise SelectionPaused(f"{type(e).__name__}: {e}", attempts)
                retry_after = getattr(e, "retry_after_s", None)
                if retry_after:
                    wait = min(max(wait, retry_after), 30.0)
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
            if n < self._max_retries:
                await self._sleep(wait)
        raise SelectionPaused(
            f"no valid answer after {len(attempts)} attempts: {attempts[-1]['error']}",
            attempts,
        )


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
            raise SelectionPaused(f"no recorded answer for {point.decision_id}")
        try:
            if isinstance(rec, str):
                decision = validate_selection(point, rec, selector=self.name)
            else:
                decision = resolve_choice(point, rec, selector=self.name)
        except DecisionError as e:
            raise SelectionPaused(f"recorded answer invalid: {e}") from e
        return SelectionResult(decision)


async def select(point: DecisionPoint, selector: Selector) -> SelectionResult:
    if len(point.candidates) == 1:
        if point.forced_rule is None:
            raise SelectionPaused(
                f"{point.decision_id}: one candidate left after controller filtering "
                f"of {point.legal_count} legal options; not executed without a choice"
            )
        return SelectionResult(forced_decision(point))
    return await selector.choose(point)
