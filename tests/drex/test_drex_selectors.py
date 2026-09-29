"""Selectors: Drex is waited for, never replaced; no silent fallback."""

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
    ControllerFilteringError,
    ReplayRejected,
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


def _drex(client, **kw) -> DrexSelector:
    kw.setdefault("backoff_s", 1.0)
    kw.setdefault("max_backoff_s", 60.0)
    kw.setdefault("sleep", _no_sleep)
    return DrexSelector(client, **kw)


def test_request_sends_labels_as_options_and_context_as_state():
    client = _ScriptedClient(GOOD)
    asyncio.run(_drex(client).choose(_point()))
    call = client.calls[0]
    assert call["state"] == {"turn": 3, "objective": "expand"}
    assert call["instructions"] == "Which technology should the empire research next?"
    assert call["options"] == {"Mining": None, "Pottery": "turns: 4"}


def test_option_descriptions_are_flat_deterministic_text():
    from civ_mcp.drex.selectors import describe_facts

    facts = {
        "terrain": "Grass",
        "feature": None,
        "hills": True,
        "river": False,
        "slots": ["Military", "Economic"],
        "yields": {"food": 2, "production": 1},
        "unlocks": "",
        "distance": 2,
    }
    assert describe_facts(facts) == (
        "terrain: Grass; hills: yes; river: no; slots: Military, Economic; "
        "yields: food 2, production 1; distance: 2"
    )
    assert describe_facts({}) is None
    assert describe_facts({"feature": None}) is None


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
    with pytest.raises(ReplayRejected):
        asyncio.run(ReplaySelector({"T3#2": MISSING_OPTION}).choose(_point()))
    with pytest.raises(ReplayRejected):
        asyncio.run(ReplaySelector({}).choose(_point()))


def test_single_candidate_left_by_filtering_is_not_forced():
    from civ_mcp.drex.candidates import DecisionPoint

    point = _point(("TECHNOLOGY_POTTERY",))
    point = DecisionPoint.create(
        decision_id=point.decision_id,
        category=point.category,
        entity=point.entity,
        observation_version=point.observation_version,
        question=point.question,
        candidates=list(point.candidates),
        context={},
        legal_count=2,
    )
    client = _ScriptedClient()
    with pytest.raises(ControllerFilteringError):
        asyncio.run(select(point, _drex(client)))
    assert client.calls == []


# ------------------------------------------------- waits, never pauses (A1)
def test_429_storm_ends_in_a_decision_not_a_pause():
    client = _ScriptedClient(*(DrexUnavailable("HTTP 429") for _ in range(5)), GOOD)
    waits = []
    result = asyncio.run(_drex(client, on_wait=waits.append).choose(_point()))
    assert result.decision.candidate_id == "research:TECHNOLOGY_POTTERY"
    assert len(client.calls) == 6 and len(waits) == 5
    assert [w["sleep_s"] for w in waits] == [1.0, 2.0, 4.0, 8.0, 16.0]
    assert all(w["retryable"] for w in waits)


def test_backoff_is_capped_and_honours_retry_after():
    errs = [DrexUnavailable("HTTP 429", retry_after_s=90.0)] + [
        DrexUnavailable("HTTP 529") for _ in range(7)
    ]
    client = _ScriptedClient(*errs, GOOD)
    waits = []
    asyncio.run(_drex(client, on_wait=waits.append).choose(_point()))
    assert waits[0]["sleep_s"] == 60.0  # retry-after 90 capped to max_backoff
    assert max(w["sleep_s"] for w in waits) == 60.0


def test_auth_error_refreshes_client_and_continues():
    bad = _ScriptedClient(DrexAuthError("HTTP 401: bad key"))
    good = _ScriptedClient(GOOD)
    refreshed = []

    async def refresh():
        refreshed.append(True)
        return good

    waits = []
    result = asyncio.run(
        _drex(bad, refresh_client=refresh, on_wait=waits.append).choose(_point())
    )
    assert result.decision.candidate_id == "research:TECHNOLOGY_POTTERY"
    assert refreshed == [True]
    assert waits[0]["retryable"] is False and waits[0]["sleep_s"] == 60.0


def test_every_wait_is_reported():
    client = _ScriptedClient(MISSING_OPTION, DrexUnavailable("timeout"), GOOD)
    waits = []
    asyncio.run(_drex(client, on_wait=waits.append).choose(_point()))
    assert [w["error_class"] for w in waits] == ["DecisionError", "DrexUnavailable"]
    assert all({"attempt", "error", "sleep_s", "retryable"} <= set(w) for w in waits)


def test_invalid_request_is_a_non_retryable_wait():
    from civ_mcp.drex.client import DrexRequestInvalid

    client = _ScriptedClient(
        DrexRequestInvalid("256 options exceed the limit of 255"), GOOD
    )
    waits = []
    asyncio.run(_drex(client, on_wait=waits.append).choose(_point()))
    assert len(client.calls) == 2 and waits[0]["retryable"] is False
