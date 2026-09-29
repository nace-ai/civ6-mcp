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
