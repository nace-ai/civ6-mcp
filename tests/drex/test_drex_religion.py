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
        "BELIEF_STEWARDSHIP",
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
    assert classes == {"BELIEF_CLASS_ENHANCER"}  # founder/follower are already held
    cand = next(
        c for c in point.candidates if c.params.belief_type == "BELIEF_MISSIONARY_ZEAL"
    )
    outcome = asyncio.run(
        Executor(game, sleep=_no_sleep).execute(
            cand, point, current_version=obs.version, turn=5, inputs=inputs
        )
    )
    assert outcome.status is OutcomeStatus.CONFIRMED
    assert ("add_belief", ("BELIEF_MISSIONARY_ZEAL",)) in game.calls
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


# --------------------------------------------------- review fixes (I1, I2)
def test_failed_founding_clears_partial_choices_so_drex_repicks(tmp_path):
    game = FakeGame()
    game.extra_blockers = [(RELIGION_BLOCKER, "Found a religion")]
    orig = game.found_religion
    attempts = {"n": 0}

    async def flaky(*a):
        attempts["n"] += 1
        if attempts["n"] == 1:
            await game._record_only("found_religion", *a)
            return "ERR:BELIEF_TAKEN|another civ took it"
        return await orig(*a)

    game.found_religion = flaky
    runner = _runner(
        game,
        tmp_path,
        (
            "religion:RELIGION_BUDDHISM",
            "follower:BELIEF_CHORAL_MUSIC",
            "found:BELIEF_TITHE",
            "found:BELIEF_CHURCH_PROPERTY",
            "skip:",
            "research:",
            "produce:",
        ),
    )
    runner._sleep = _no_sleep
    runner.max_loop_iterations = 60
    result = asyncio.run(runner.run())
    assert result.stop_reason == "turn_budget_reached"
    assert game.religion_status.has_religion is True
    assert attempts["n"] == 2  # re-picked and founded within the same turn


def test_found_religion_verifies_by_readback_not_by_print():
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

    async def silent(*a):
        await game._record_only("found_religion", *a)
        return "RELIGION_FOUNDED|Buddhism|x|y"  # the engine silently ignored it

    game.found_religion = silent
    outcome = asyncio.run(
        Executor(game, sleep=_no_sleep).execute(
            cand, point, current_version=obs.version, turn=5, inputs=inputs
        )
    )
    assert outcome.status is not OutcomeStatus.CONFIRMED


def test_belief_candidates_after_founding_offer_only_addable_classes():
    st = fx.religion_founding()
    st.has_religion = True
    classes = {c.params.belief_class for c in belief_candidates(st)}
    assert "BELIEF_CLASS_FOUNDER" not in classes
    assert "BELIEF_CLASS_FOLLOWER" not in classes
    assert "BELIEF_CLASS_ENHANCER" in classes


def test_add_belief_verifies_by_readback_not_by_print():
    game = FakeGame()
    game.religion_status.has_religion = True
    game.extra_blockers = [(BELIEF_BLOCKER, "x")]
    obs = LiveObserver(game)
    core = asyncio.run(obs.core())
    spec = DecisionSpec(DecisionCategory.BELIEF, "empire")
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
    cand = point.candidates[0]

    async def silent(belief_type):
        await game._record_only("add_belief", belief_type)
        return "BELIEF_ADDED|x"

    game.add_belief = silent
    outcome = asyncio.run(
        Executor(game, sleep=_no_sleep).execute(
            cand, point, current_version=obs.version, turn=5, inputs=inputs
        )
    )
    assert outcome.status is not OutcomeStatus.CONFIRMED
