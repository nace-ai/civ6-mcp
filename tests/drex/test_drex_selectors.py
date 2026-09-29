"""Selectors: bounded retries then pause; never a silent fallback."""

import asyncio

import pytest

from civ_mcp.drex.candidates import (
    ActionKind,
    Candidate,
    DecisionCategory,
    DecisionPoint,
    ResearchParams,
)
from civ_mcp.drex.client import DrexAuthError, DrexUnavailable
from civ_mcp.drex.decision import ChoiceAnswer
from civ_mcp.drex.selectors import (
    DrexSelector,
    RandomSelector,
    ReplaySelector,
    SelectionPaused,
    select,
)


def _point(techs=("TECHNOLOGY_POTTERY", "TECHNOLOGY_MINING")) -> DecisionPoint:
    cands = [
        Candidate.create(
            ActionKind.SET_RESEARCH,
            ResearchParams(tech_type=t),
            label=t.split("_", 1)[1].title(),
            facts={"turns": 4} if "POTTERY" in t else {},
        )
        for t in techs
    ]
    return DecisionPoint.create(
        decision_id="T3#2",
        category=DecisionCategory.RESEARCH,
        entity="empire",
        observation_version="v1",
        question="Which technology should the empire research next?",
        candidates=cands,
        context={"turn": 3, "objective": "expand"},
    )


GOOD = ChoiceAnswer(
    probabilities=(("Pottery", 0.8), ("Mining", 0.2)),
    choice="Pottery",
    confidence=0.6,
    model="drex-1.1",
    usage={"input_tokens": 50, "output_tokens": 2},
)
MISSING_OPTION = ChoiceAnswer(probabilities=(("Pottery", 1.0),), choice="Pottery")


class _ScriptedClient:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    async def choose(self, **kwargs):
        self.calls.append(kwargs)
        r = self.results.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


async def _no_sleep(_):
    return None


def _drex(client, retries=2) -> DrexSelector:
    return DrexSelector(client, max_retries=retries, backoff_s=0.0, sleep=_no_sleep)


def test_request_sends_labels_as_options_and_context_as_state():
    client = _ScriptedClient(GOOD)
    asyncio.run(_drex(client).choose(_point()))
    call = client.calls[0]
    assert call["state"] == {"turn": 3, "objective": "expand"}
    assert call["instructions"] == "Which technology should the empire research next?"
    assert call["options"] == {"Mining": None, "Pottery": {"turns": 4}}


def test_valid_answer_yields_decision_on_stored_candidate():
    result = asyncio.run(_drex(_ScriptedClient(GOOD)).choose(_point()))
    assert result.decision.candidate_id == "research:TECHNOLOGY_POTTERY"
    assert result.decision.model == "drex-1.1"
    assert result.decision.usage == {"input_tokens": 50, "output_tokens": 2}
    assert len(result.attempts) == 1


def test_transient_failure_is_retried_within_bound():
    client = _ScriptedClient(DrexUnavailable("HTTP 529"), GOOD)
    result = asyncio.run(_drex(client).choose(_point()))
    assert result.decision.candidate_id == "research:TECHNOLOGY_POTTERY"
    assert [a["ok"] for a in result.attempts] == [False, True]


def test_exhausted_retries_pause_without_a_decision():
    client = _ScriptedClient(*(DrexUnavailable("timeout") for _ in range(3)))
    with pytest.raises(SelectionPaused) as exc:
        asyncio.run(_drex(client, retries=2).choose(_point()))
    assert len(client.calls) == 3
    assert len(exc.value.attempts) == 3


def test_invalid_answers_are_retried_then_pause():
    client = _ScriptedClient(MISSING_OPTION, MISSING_OPTION)
    with pytest.raises(SelectionPaused, match="missing"):
        asyncio.run(_drex(client, retries=1).choose(_point()))
    assert len(client.calls) == 2


def test_non_retryable_error_pauses_immediately():
    client = _ScriptedClient(DrexAuthError("HTTP 401"), GOOD)
    with pytest.raises(SelectionPaused):
        asyncio.run(_drex(client).choose(_point()))
    assert len(client.calls) == 1


def test_random_baseline_is_labeled_and_order_invariant():
    a = asyncio.run(RandomSelector(seed=7).choose(_point()))
    b = asyncio.run(
        RandomSelector(seed=7).choose(
            _point(("TECHNOLOGY_MINING", "TECHNOLOGY_POTTERY"))
        )
    )
    assert a.decision.selector == "random-baseline"
    assert a.decision.candidate_id == b.decision.candidate_id


def test_single_candidate_is_forced_without_consulting_selector():
    client = _ScriptedClient()
    result = asyncio.run(select(_point(("TECHNOLOGY_POTTERY",)), _drex(client)))
    assert result.decision.rule == "forced_single_candidate"
    assert client.calls == []


def test_replay_revalidates_recorded_answers():
    ok = asyncio.run(ReplaySelector({"T3#2": GOOD}).choose(_point()))
    assert ok.decision.selector == "replay"
    with pytest.raises(SelectionPaused):
        asyncio.run(ReplaySelector({"T3#2": MISSING_OPTION}).choose(_point()))
    with pytest.raises(SelectionPaused):
        asyncio.run(ReplaySelector({}).choose(_point()))
