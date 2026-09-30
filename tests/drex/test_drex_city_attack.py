"""City (and district) ranged attack: attack a target or hold fire."""

import asyncio

import drex_fixtures as fx
from drex_fakes import FakeGame

from civ_mcp.drex.candidates import ActionKind, DecisionCategory
from civ_mcp.drex.enumerate import city_attack_candidates
from civ_mcp.drex.executor import Executor, OutcomeStatus
from civ_mcp.drex.live import LiveObserver
from civ_mcp.drex.observation import DecisionMemory, DecisionSpec
from civ_mcp.drex.points import build_decision_point
from civ_mcp.drex.scheduler import CITY_ATTACK_BLOCKERS, Scheduler, TurnLedger
from civ_mcp.lua.drex_queries import parse_city_attack_targets

BLOCKER = "ENDTURN_BLOCKING_CITY_RANGE_ATTACK"


async def _no_sleep(_):
    return None


def _point(obs, core):
    spec = DecisionSpec(DecisionCategory.CITY_ATTACK, f"city:{fx.CAPITAL_ID}")
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


def test_parse_city_attack_targets():
    lines = [
        "TARGET|11|12|UNIT_WARRIOR|63|80|100",
        "TARGET|12|12|UNIT_SLINGER|63|30|100",
    ]
    t = parse_city_attack_targets(lines)
    assert [(x.x, x.y) for x in t] == [(11, 12), (12, 12)]
    assert t[0].unit_type == "UNIT_WARRIOR" and t[0].owner_id == 63
    assert t[1].hp == 30 and t[1].max_hp == 100


def test_city_attack_candidates_include_each_target_and_hold_fire():
    cands = city_attack_candidates(fx.capital(), fx.city_targets())
    kinds = [c.kind for c in cands]
    assert kinds.count(ActionKind.CITY_ATTACK) == 2
    assert kinds.count(ActionKind.HOLD_FIRE) == 1
    atk = next(c for c in cands if c.kind is ActionKind.CITY_ATTACK)
    assert atk.params.city_id == fx.CAPITAL_ID
    assert atk.facts["unit"] == "UNIT_WARRIOR" and atk.facts["hp"] == "80/100"


def test_scheduler_offers_a_city_attack_per_city_for_both_blocker_types():
    for b in sorted(CITY_ATTACK_BLOCKERS):
        game = FakeGame()
        game.city_targets = {fx.CAPITAL_ID: fx.city_targets()}
        game.extra_blockers = [(b, "City can attack")]
        core = asyncio.run(LiveObserver(game).core())
        step = Scheduler().next(core, TurnLedger(turn=5))
        assert step.category is DecisionCategory.CITY_ATTACK, b
        assert step.entity == f"city:{fx.CAPITAL_ID}"


def test_city_attack_without_targets_is_forced_hold_fire():
    game = FakeGame()
    game.city_targets = {fx.CAPITAL_ID: []}
    game.extra_blockers = [(BLOCKER, "x")]
    obs = LiveObserver(game)
    core = asyncio.run(obs.core())
    point, _ = _point(obs, core)
    assert [c.kind for c in point.candidates] == [ActionKind.HOLD_FIRE]
    assert point.forced_rule == "forced_single_candidate"


def test_city_attack_dispatches_and_reports_confirmed():
    game = FakeGame()
    game.city_targets = {fx.CAPITAL_ID: fx.city_targets()}
    game.extra_blockers = [(BLOCKER, "x")]
    obs = LiveObserver(game)
    core = asyncio.run(obs.core())
    point, inputs = _point(obs, core)
    cand = next(c for c in point.candidates if c.kind is ActionKind.CITY_ATTACK)
    outcome = asyncio.run(
        Executor(game, sleep=_no_sleep).execute(
            cand, point, current_version=obs.version, turn=5, inputs=inputs
        )
    )
    assert outcome.status is OutcomeStatus.CONFIRMED
    assert (
        "city_attack",
        (fx.CAPITAL_ID, cand.params.target_x, cand.params.target_y),
    ) in game.calls


def test_hold_fire_confirms_without_dispatch_and_city_is_resolved():
    game = FakeGame()
    game.city_targets = {fx.CAPITAL_ID: fx.city_targets()}
    game.extra_blockers = [(BLOCKER, "x")]
    obs = LiveObserver(game)
    core = asyncio.run(obs.core())
    point, inputs = _point(obs, core)
    hold = next(c for c in point.candidates if c.kind is ActionKind.HOLD_FIRE)
    outcome = asyncio.run(
        Executor(game, sleep=_no_sleep).execute(
            hold, point, current_version=obs.version, turn=5, inputs=inputs
        )
    )
    assert outcome.status is OutcomeStatus.CONFIRMED and game.calls == []
    s = Scheduler()
    ledger = TurnLedger(turn=5)
    spec = s.next(core, ledger)
    s.note(ledger, spec, ActionKind.HOLD_FIRE, outcome)
    assert (
        getattr(s.next(core, ledger), "category", None)
        is not DecisionCategory.CITY_ATTACK
    )


def test_precheck_rejects_a_target_that_left():
    game = FakeGame()
    game.city_targets = {fx.CAPITAL_ID: fx.city_targets()}
    game.extra_blockers = [(BLOCKER, "x")]
    obs = LiveObserver(game)
    core = asyncio.run(obs.core())
    point, inputs = _point(obs, core)
    cand = next(c for c in point.candidates if c.kind is ActionKind.CITY_ATTACK)
    game.city_targets = {fx.CAPITAL_ID: []}
    outcome = asyncio.run(
        Executor(game, sleep=_no_sleep).execute(
            cand, point, current_version="stale", turn=5, inputs=None
        )
    )
    assert outcome.status is OutcomeStatus.REJECTED and game.calls == []


# --------------------------------------------------- review fixes (I4)
def test_hold_fire_then_standing_blocker_is_dismissed_as_housekeeping(tmp_path):
    from test_drex_runner import PreferSelector, _fake_end_turn, _records

    from civ_mcp.drex.decision_log import DecisionLog
    from civ_mcp.drex.runner import RunConfig, Runner

    game = FakeGame()
    game.city_targets = {fx.CAPITAL_ID: fx.city_targets()}
    game.extra_blockers = [(BLOCKER, "City can attack")]
    game.sticky_blockers = {BLOCKER}  # the engine keeps flagging after hold fire
    log = DecisionLog(tmp_path / "run.jsonl", run_id="t", secrets=[])
    runner = Runner(
        game,
        PreferSelector(
            prefixes=(f"hold_fire:{fx.CAPITAL_ID}", "skip:", "research:", "produce:")
        ),
        log,
        RunConfig(turns=1),
        end_turn=_fake_end_turn(game),
    )
    runner._sleep = _no_sleep
    runner.max_loop_iterations = 60
    result = asyncio.run(runner.run())
    assert result.stop_reason == "turn_budget_reached"
    assert not any(m == "city_attack" for m, _ in game.calls)
    hk = [
        r
        for r in _records(tmp_path)
        if r["type"] == "housekeeping" and r["action"] == "blocker_dismissed"
    ]
    assert hk and BLOCKER in hk[0]["blockers"]


# ------------------------------------ proactive check (no blocker in Base ruleset)
def test_each_city_gets_a_ranged_attack_check_once_per_turn_without_a_blocker():
    from civ_mcp.drex.executor import ActionOutcome

    game = FakeGame()
    game.city_targets = {fx.CAPITAL_ID: fx.city_targets()}
    core = asyncio.run(LiveObserver(game).core())
    s = Scheduler()
    ledger = TurnLedger(turn=5)
    seen = []
    for _ in range(30):
        step = s.next(core, ledger)
        cat = getattr(step, "category", None)
        if cat is None:
            break
        seen.append(cat)
        kind = ActionKind.HOLD_FIRE if cat is DecisionCategory.CITY_ATTACK else None
        s.note(ledger, step, kind, ActionOutcome(OutcomeStatus.CONFIRMED, "x", False))
    assert seen.count(DecisionCategory.CITY_ATTACK) == 1
    assert seen.index(DecisionCategory.CITY_ATTACK) > seen.index(
        DecisionCategory.PRODUCTION
    )
    assert seen.index(DecisionCategory.CITY_ATTACK) < seen.index(DecisionCategory.UNIT)


def test_proactive_check_with_no_targets_costs_no_drex_call(tmp_path):
    from test_drex_runner import PreferSelector, _fake_end_turn, _records

    from civ_mcp.drex.decision_log import DecisionLog
    from civ_mcp.drex.runner import RunConfig, Runner

    game = FakeGame()
    game.city_targets = {fx.CAPITAL_ID: []}
    sel = PreferSelector()
    log = DecisionLog(tmp_path / "run.jsonl", run_id="t", secrets=[])
    runner = Runner(game, sel, log, RunConfig(turns=1), end_turn=_fake_end_turn(game))
    asyncio.run(runner.run())
    atk = [
        r
        for r in _records(tmp_path)
        if r["type"] == "decision" and r["category"] == "city_attack"
    ]
    assert len(atk) == 1
    assert atk[0]["decision"]["rule"] == "forced_single_candidate"
    assert atk[0]["dispatch"]["method"] is None
    assert not any(p.category is DecisionCategory.CITY_ATTACK for p in sel.points)


def test_attack_blocker_appearing_after_the_check_reopens_the_cities_once():
    from civ_mcp.drex.executor import ActionOutcome
    from civ_mcp.drex.observation import Blocker

    game = FakeGame()
    game.city_targets = {fx.CAPITAL_ID: []}
    core = asyncio.run(LiveObserver(game).core())
    s = Scheduler()
    ledger = TurnLedger(turn=5)
    spec = DecisionSpec(DecisionCategory.CITY_ATTACK, f"city:{fx.CAPITAL_ID}")
    # the proactive check ran and held fire (no targets yet)
    s.note(
        ledger,
        spec,
        ActionKind.HOLD_FIRE,
        ActionOutcome(OutcomeStatus.CONFIRMED, "x", False),
    )
    assert s.stale_blockers(core, ledger) == []
    # later this turn the engine raises the attack blocker (a war started)
    import dataclasses

    core2 = dataclasses.replace(core, blockers=[Blocker(BLOCKER, "City can attack")])
    step = s.next(core2, ledger)
    assert step.category is DecisionCategory.CITY_ATTACK  # re-opened once
    assert s.stale_blockers(core2, ledger) == []  # not stale while re-opened
    s.note(
        ledger,
        step,
        ActionKind.HOLD_FIRE,
        ActionOutcome(OutcomeStatus.CONFIRMED, "x", False),
    )
    assert s.stale_blockers(core2, ledger) == [BLOCKER]  # now genuinely stale
    assert (
        getattr(s.next(core2, ledger), "category", None)
        is not DecisionCategory.CITY_ATTACK
    )
