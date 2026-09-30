"""Unit upgrade as one of a unit's orders (Drex decides, treasury permitting)."""

import asyncio

import drex_fixtures as fx
from drex_fakes import FakeGame

from civ_mcp.drex.candidates import ActionKind, DecisionCategory
from civ_mcp.drex.enumerate import unit_candidates
from civ_mcp.drex.executor import Executor, OutcomeStatus
from civ_mcp.drex.live import LiveObserver
from civ_mcp.drex.observation import DecisionMemory, DecisionSpec
from civ_mcp.drex.points import build_decision_point
from civ_mcp.drex.scheduler import Scheduler, TurnLedger


async def _no_sleep(_):
    return None


def _upgradeable_warrior():
    w = fx.warrior()
    w.can_upgrade, w.upgrade_target, w.upgrade_cost = True, "UNIT_SWORDSMAN", 90
    return w


def _point(game):
    obs = LiveObserver(game)
    core = asyncio.run(obs.core())
    spec = DecisionSpec(DecisionCategory.UNIT, f"unit:{fx.WARRIOR_ID}")
    inputs = asyncio.run(obs.inputs(spec, core))
    point, excluded = build_decision_point(
        spec,
        core,
        inputs,
        DecisionMemory(),
        objective="o",
        decision_id="T5#1",
        max_options=255,
    )
    return obs, core, point, inputs, excluded


def test_upgrade_offered_when_affordable():
    cands, _ = unit_candidates(
        fx.warrior_space(), _upgradeable_warrior(), me=0, gold=120
    )
    up = [c for c in cands if c.kind is ActionKind.UPGRADE_UNIT]
    assert len(up) == 1
    assert up[0].candidate_id == f"upgrade:{fx.WARRIOR_ID}"
    assert "Swordsman" in up[0].label and "90" in up[0].label
    assert up[0].facts["cost"] == 90 and up[0].facts["treasury"] == 120


def test_upgrade_excluded_when_unaffordable_with_reason():
    cands, excl = unit_candidates(
        fx.warrior_space(), _upgradeable_warrior(), me=0, gold=40
    )
    assert not [c for c in cands if c.kind is ActionKind.UPGRADE_UNIT]
    assert any("costs 90 gold" in e.reason for e in excl)


def test_no_upgrade_candidate_for_a_unit_without_one():
    cands, _ = unit_candidates(fx.warrior_space(), fx.warrior(), me=0, gold=999)
    assert not [c for c in cands if c.kind is ActionKind.UPGRADE_UNIT]


def test_upgrade_dispatches_confirms_and_spends_gold():
    game = FakeGame()
    game.units[fx.WARRIOR_ID] = _upgradeable_warrior()
    game.gold = 200
    obs, core, point, inputs, _ = _point(game)
    cand = next(c for c in point.candidates if c.kind is ActionKind.UPGRADE_UNIT)
    outcome = asyncio.run(
        Executor(game, sleep=_no_sleep).execute(
            cand, point, current_version=obs.version, turn=5, inputs=inputs
        )
    )
    assert outcome.status is OutcomeStatus.CONFIRMED
    assert ("upgrade_unit", (fx.WARRIOR_ID,)) in game.calls
    assert game.gold == 110
    assert game.units[fx.WARRIOR_ID].unit_type == "UNIT_SWORDSMAN"
    s = Scheduler()
    ledger = TurnLedger(turn=5)
    step = DecisionSpec(DecisionCategory.UNIT, f"unit:{fx.WARRIOR_ID}")
    s.note(ledger, step, cand.kind, outcome, cand.candidate_id)
    assert not s._open(ledger, step)  # an upgrade ends the unit's turn


def test_upgrade_refused_by_engine_is_rejected():
    game = FakeGame()
    game.units[fx.WARRIOR_ID] = _upgradeable_warrior()
    game.gold = 200
    obs, core, point, inputs, _ = _point(game)
    cand = next(c for c in point.candidates if c.kind is ActionKind.UPGRADE_UNIT)
    game.gold = 10  # spent since the observation: the engine refuses
    outcome = asyncio.run(
        Executor(game, sleep=_no_sleep).execute(
            cand, point, current_version=obs.version, turn=5, inputs=inputs
        )
    )
    assert outcome.status is OutcomeStatus.REJECTED
    assert "upgrade_refused" in outcome.reason
