"""Religion founding (three Drex steps, one dispatch) and added beliefs."""

import asyncio

import drex_fixtures as fx
from drex_fakes import FakeGame
from test_drex_runner import PreferSelector, _fake_end_turn, _records

from civ_mcp.drex.candidates import ActionKind, DecisionCategory
from civ_mcp.drex.decision_log import DecisionLog
from civ_mcp.drex.enumerate import belief_candidates, religion_candidates
from civ_mcp.drex.executor import Executor, OutcomeStatus
from civ_mcp.drex.live import LiveObserver
from civ_mcp.drex.observation import DecisionMemory, DecisionSpec
from civ_mcp.drex.points import build_decision_point
from civ_mcp.drex.runner import RunConfig, Runner
from civ_mcp.drex.scheduler import (
    BELIEF_BLOCKER,
    RELIGION_BLOCKER,
    Scheduler,
    TurnLedger,
)


async def _no_sleep(_):
    return None


def _runner(game, tmp_path, prefixes):
    log = DecisionLog(tmp_path / "run.jsonl", run_id="t", secrets=[])
    return Runner(
        game,
        PreferSelector(prefixes=prefixes),
        log,
        RunConfig(turns=1),
        end_turn=_fake_end_turn(game),
    )


def test_religion_steps_in_order():
    st = fx.religion_founding()
    step1 = religion_candidates(st, {})
    assert {c.kind for c in step1} == {ActionKind.CHOOSE_RELIGION}
    assert {c.params.religion_type for c in step1} == {
        "RELIGION_BUDDHISM",
        "RELIGION_TAOISM",
    }
    step2 = religion_candidates(st, {"religion_type": "RELIGION_BUDDHISM"})
    assert {c.kind for c in step2} == {ActionKind.CHOOSE_FOLLOWER_BELIEF}
    assert all(c.params.belief_class == "BELIEF_CLASS_FOLLOWER" for c in step2)
    step3 = religion_candidates(
        st,
        {
            "religion_type": "RELIGION_BUDDHISM",
            "follower_belief": "BELIEF_CHORAL_MUSIC",
        },
    )
    assert {c.kind for c in step3} == {ActionKind.FOUND_RELIGION}
    assert all(c.params.follower_belief == "BELIEF_CHORAL_MUSIC" for c in step3)
    assert {c.params.founder_belief for c in step3} == {
        "BELIEF_TITHE",
        "BELIEF_CHURCH_PROPERTY",
    }


def test_no_religion_candidates_once_founded():
    st = fx.religion_founding()
    st.has_religion = True
    assert religion_candidates(st, {}) == []


def test_scheduler_offers_religion_while_the_blocker_stands():
    game = FakeGame()
    game.extra_blockers = [(RELIGION_BLOCKER, "Found a religion")]
    core = asyncio.run(LiveObserver(game).core())
    step = Scheduler().next(core, TurnLedger(turn=5))
    assert step.category is DecisionCategory.RELIGION


def test_runner_stores_partial_choices_then_founds(tmp_path):
    game = FakeGame()
    game.extra_blockers = [(RELIGION_BLOCKER, "Found a religion")]
    runner = _runner(
        game,
        tmp_path,
        (
            "religion:RELIGION_BUDDHISM",
            "follower:BELIEF_CHORAL_MUSIC",
            "found:BELIEF_TITHE",
            "skip:",
            "research:",
            "produce:",
        ),
    )
    asyncio.run(runner.run())
    found = [a for m, a in game.calls if m == "found_religion"]
    assert found == [("RELIGION_BUDDHISM", "BELIEF_CHORAL_MUSIC", "BELIEF_TITHE")]
    assert game.religion_status.has_religion is True
    steps = [r["category"] for r in _records(tmp_path) if r["type"] == "decision"]
    assert steps.count("religion") == 3
    assert RELIGION_BLOCKER not in {b[0] for b in game.extra_blockers}


def test_partial_religion_choice_is_dropped_on_new_turn():
    ledger = TurnLedger(turn=5)
    ledger.religion_partial["religion_type"] = "RELIGION_BUDDHISM"
    fresh = TurnLedger(turn=6, full_observed=True)
    assert fresh.religion_partial == {}
    # and the scheduler clears a stale partial when the blocker is gone
    game = FakeGame()
    core = asyncio.run(LiveObserver(game).core())
    Scheduler().next(core, ledger)
    assert ledger.religion_partial == {}


def test_belief_candidates_span_classes_and_add_belief_confirms():
    game = FakeGame()
    game.religion_status.has_religion = True
    game.extra_blockers = [(BELIEF_BLOCKER, "Choose a belief")]
    obs = LiveObserver(game)
    core = asyncio.run(obs.core())
    spec = Scheduler().next(core, TurnLedger(turn=5))
    assert spec.category is DecisionCategory.BELIEF
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
    classes = {c.params.belief_class for c in point.candidates}
    assert {
        "BELIEF_CLASS_FOLLOWER",
        "BELIEF_CLASS_FOUNDER",
        "BELIEF_CLASS_ENHANCER",
    } <= classes
    cand = next(c for c in point.candidates if c.params.belief_type == "BELIEF_TITHE")
    outcome = asyncio.run(
        Executor(game, sleep=_no_sleep).execute(
            cand, point, current_version=obs.version, turn=5, inputs=inputs
        )
    )
    assert outcome.status is OutcomeStatus.CONFIRMED
    assert ("add_belief", ("BELIEF_TITHE",)) in game.calls
    assert belief_candidates(fx.religion_founding()) != []


def test_found_religion_precheck_rejects_when_already_founded():
    game = FakeGame()
    game.extra_blockers = [(RELIGION_BLOCKER, "x")]
    obs = LiveObserver(game)
    core = asyncio.run(obs.core())
    spec = DecisionSpec(DecisionCategory.RELIGION, "empire")
    inputs = asyncio.run(obs.inputs(spec, core))
    inputs.religion_partial = {
        "religion_type": "RELIGION_BUDDHISM",
        "follower_belief": "BELIEF_CHORAL_MUSIC",
    }
    point, _ = build_decision_point(
        spec,
        core,
        inputs,
        DecisionMemory(),
        objective="o",
        decision_id="T5#1",
        max_options=255,
    )
    cand = point.candidates[0]
    assert cand.kind is ActionKind.FOUND_RELIGION
    game.religion_status.has_religion = True
    outcome = asyncio.run(
        Executor(game, sleep=_no_sleep).execute(
            cand, point, current_version="stale", turn=5, inputs=None
        )
    )
    assert outcome.status is OutcomeStatus.REJECTED and game.calls == []
