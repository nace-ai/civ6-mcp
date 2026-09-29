"""Era dedication (commemoration) as a Drex decision."""

import asyncio

import drex_fixtures as fx
from drex_fakes import FakeGame

from civ_mcp.drex.candidates import ActionKind, DecisionCategory
from civ_mcp.drex.enumerate import dedication_candidates
from civ_mcp.drex.executor import Executor, OutcomeStatus
from civ_mcp.drex.live import LiveObserver
from civ_mcp.drex.observation import DecisionMemory, DecisionSpec
from civ_mcp.drex.points import build_decision_point
from civ_mcp.drex.scheduler import DEDICATION_BLOCKER, Scheduler, TurnLedger


async def _no_sleep(_):
    return None


def _point(obs, core):
    spec = DecisionSpec(DecisionCategory.DEDICATION, "empire")
    inputs = asyncio.run(obs.inputs(spec, core))
    point, _ = build_decision_point(
        spec,
        core,
        inputs,
        DecisionMemory(),
        objective="o",
        decision_id="T5#1",
        max_options=255,
    )
    return point, inputs


def test_candidates_describe_the_bonus_for_the_current_age():
    cands = dedication_candidates(fx.dedications(age="Golden"))
    assert {c.params.name for c in cands} == {
        "COMMEMORATION_SCIENTIFIC",
        "COMMEMORATION_MILITARY",
    }
    assert all(c.kind is ActionKind.CHOOSE_DEDICATION for c in cands)
    assert all(c.facts["age"] == "Golden" for c in cands)
    assert all(c.facts["bonus"].startswith("golden:") for c in cands)
    normal = dedication_candidates(fx.dedications(age="Normal"))
    assert all(c.facts["bonus"].startswith("normal:") for c in normal)
    dark = dedication_candidates(fx.dedications(age="Dark"))
    assert all(c.facts["bonus"].startswith("dark:") for c in dark)


def test_active_dedications_are_not_offered_again():
    st = fx.dedications(age="Normal")
    st.active = ["COMMEMORATION_SCIENTIFIC"]
    assert [c.params.name for c in dedication_candidates(st)] == [
        "COMMEMORATION_MILITARY"
    ]


def test_scheduler_offers_dedication_when_the_blocker_stands():
    game = FakeGame()
    game.extra_blockers = [(DEDICATION_BLOCKER, "Choose a dedication")]
    core = asyncio.run(LiveObserver(game).core())
    step = Scheduler().next(core, TurnLedger(turn=5))
    assert step.category is DecisionCategory.DEDICATION and step.entity == "empire"


def test_choose_dedication_dispatches_index_and_confirms():
    game = FakeGame()
    game.extra_blockers = [(DEDICATION_BLOCKER, "Choose a dedication")]
    obs = LiveObserver(game)
    core = asyncio.run(obs.core())
    point, inputs = _point(obs, core)
    cand = next(
        c for c in point.candidates if c.params.name == "COMMEMORATION_MILITARY"
    )
    outcome = asyncio.run(
        Executor(game, sleep=_no_sleep).execute(
            cand, point, current_version=obs.version, turn=5, inputs=inputs
        )
    )
    assert outcome.status is OutcomeStatus.CONFIRMED
    assert ("choose_dedication", (cand.params.index,)) in game.calls
    assert "COMMEMORATION_MILITARY" in game.dedication_status.active
    assert DEDICATION_BLOCKER not in {b[0] for b in game.extra_blockers}


def test_precheck_rejects_a_dedication_already_active():
    game = FakeGame()
    game.extra_blockers = [(DEDICATION_BLOCKER, "x")]
    obs = LiveObserver(game)
    core = asyncio.run(obs.core())
    point, inputs = _point(obs, core)
    cand = point.candidates[0]
    game.dedication_status.active.append(cand.params.name)
    outcome = asyncio.run(
        Executor(game, sleep=_no_sleep).execute(
            cand, point, current_version="stale", turn=5, inputs=None
        )
    )
    assert outcome.status is OutcomeStatus.REJECTED and game.calls == []
