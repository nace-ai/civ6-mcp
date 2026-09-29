"""Map a selector's answer onto exactly one stored candidate, or refuse.

Selection rule for probability answers: take the maximum-probability options
(within ``TIE_EPSILON``). If the service's reported ``choice`` is one of them,
select it; if the service reported a choice that is not a maximum, the answer
is inconsistent and rejected. With no reported choice, ties go to the lowest
``candidate_id``. Scores are the service's option probabilities, not
game-winning probabilities.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from civ_mcp.drex.candidates import DecisionPoint

TIE_EPSILON = 1e-9
# Probabilities may arrive rounded (e.g. to 3 decimals), so the allowed
# deviation of their sum from 1 grows with the number of options.
SUM_TOLERANCE_BASE = 0.01
SUM_TOLERANCE_PER_OPTION = 0.0005


class DecisionError(Exception):
    """Base class: the answer must not be executed."""


class MalformedChoice(DecisionError):
    pass


class UnknownCandidate(DecisionError):
    pass


class StaleDecision(DecisionError):
    pass


@dataclass(frozen=True)
class ChoiceAnswer:
    """Protocol-neutral answer: option label -> probability, as returned."""

    probabilities: tuple[tuple[str, Any], ...]
    choice: str | None
    confidence: float | None = None
    model: str | None = None
    usage: Mapping[str, int] | None = field(default=None, compare=False, hash=False)
    request_id: str | None = None
    latency_ms: float | None = None


@dataclass(frozen=True)
class Decision:
    decision_id: str
    candidate_id: str
    selector: str
    rule: str
    scores: Mapping[str, float] = field(default_factory=dict, compare=False, hash=False)
    reported_choice: str | None = None
    confidence: float | None = None
    model: str | None = None
    usage: Mapping[str, int] | None = field(default=None, compare=False, hash=False)
    request_id: str | None = None
    latency_ms: float | None = None

    def to_record(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "candidate_id": self.candidate_id,
            "selector": self.selector,
            "rule": self.rule,
            "scores": dict(self.scores),
            "reported_choice": self.reported_choice,
            "confidence": self.confidence,
            "model": self.model,
            "usage": dict(self.usage) if self.usage else None,
            "request_id": self.request_id,
            "latency_ms": self.latency_ms,
        }


def _probability(label: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MalformedChoice(f"probability for {label!r} is not a number: {value!r}")
    p = float(value)
    if not math.isfinite(p) or p < 0.0 or p > 1.0:
        raise MalformedChoice(f"probability for {label!r} out of range: {value!r}")
    return p


def resolve_choice(
    point: DecisionPoint, answer: ChoiceAnswer, *, selector: str = "drex"
) -> Decision:
    labels = point.label_to_id
    seen: dict[str, float] = {}
    for label, value in answer.probabilities:
        if label in seen:
            raise MalformedChoice(f"duplicate option in answer: {label!r}")
        seen[label] = _probability(label, value)

    unknown = sorted(set(seen) - set(labels))
    if unknown:
        raise MalformedChoice(f"unknown options in answer: {unknown}")
    missing = sorted(set(labels) - set(seen))
    if missing:
        raise MalformedChoice(f"missing options in answer: {missing}")

    total = sum(seen.values())
    tolerance = SUM_TOLERANCE_BASE + SUM_TOLERANCE_PER_OPTION * len(seen)
    if abs(total - 1.0) > tolerance:
        raise MalformedChoice(f"probabilities sum to {total:.4f}, expected 1")

    best = max(seen.values())
    maxima = sorted(labels[lbl] for lbl, p in seen.items() if best - p <= TIE_EPSILON)

    reported_id: str | None = None
    if answer.choice is not None:
        if answer.choice not in labels:
            raise MalformedChoice(
                f"reported choice is not an offered option: {answer.choice!r}"
            )
        reported_id = labels[answer.choice]
        if reported_id not in maxima:
            raise MalformedChoice(
                f"reported choice {answer.choice!r} is not a maximum-probability option"
            )
        selected, rule = reported_id, "reported_choice_is_maximum"
    else:
        selected, rule = maxima[0], "argmax_lowest_candidate_id"

    return Decision(
        decision_id=point.decision_id,
        candidate_id=selected,
        selector=selector,
        rule=rule,
        scores={labels[lbl]: p for lbl, p in seen.items()},
        reported_choice=reported_id,
        confidence=answer.confidence,
        model=answer.model,
        usage=answer.usage,
        request_id=answer.request_id,
        latency_ms=answer.latency_ms,
    )


def validate_selection(
    point: DecisionPoint, candidate_id: str, *, selector: str, rule: str = "selection"
) -> Decision:
    if point.get(candidate_id) is None:
        raise UnknownCandidate(
            f"{candidate_id!r} is not a candidate of decision {point.decision_id}"
        )
    return Decision(
        decision_id=point.decision_id,
        candidate_id=candidate_id,
        selector=selector,
        rule=rule,
    )


def forced_decision(point: DecisionPoint) -> Decision:
    """Execute the only remaining option under the point's documented rule."""
    if len(point.candidates) != 1 or point.forced_rule is None:
        raise ValueError("forced decisions require one candidate and a forced_rule")
    return validate_selection(
        point,
        point.candidates[0].candidate_id,
        selector="controller",
        rule=point.forced_rule,
    )


def ensure_current(point: DecisionPoint, current_version: str) -> None:
    if point.observation_version != current_version:
        raise StaleDecision(
            f"decision {point.decision_id} was built from observation "
            f"{point.observation_version}, current is {current_version}"
        )
