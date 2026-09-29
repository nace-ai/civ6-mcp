"""Unit promotion as a Drex decision."""

import asyncio

import drex_fixtures as fx
from drex_fakes import FakeGame

from civ_mcp.drex.candidates import ActionKind, DecisionCategory
from civ_mcp.drex.enumerate import promotion_candidates
from civ_mcp.drex.executor import Executor, OutcomeStatus
from civ_mcp.drex.live import LiveObserver
from civ_mcp.drex.observation import DecisionMemory, DecisionSpec
from civ_mcp.drex.points import build_decision_point
from civ_mcp.drex.scheduler import PROMOTION_BLOCKER, Scheduler, TurnLedger
from civ_mcp.lua.drex_queries import parse_promotable_units


async def _no_sleep(_):
    return None


def _point(game, obs, core):
    spec = DecisionSpec(DecisionCategory.PROMOTION, f"unit:{fx.WARRIOR_ID}")
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


def test_promotable_units_require_the_xp_threshold():
    lines = [
        "PROMOTABLE|131073|1|UNIT_WARRIOR|30|15|1",
        "SKIP|131074|2|UNIT_SCOUT|10|15|0|below_threshold",
    ]
    units = parse_promotable_units(lines)
    assert [u.unit_id for u in units] == [131073]
    assert units[0].xp == 30 and units[0].promotion_count == 1


def test_promotion_candidates_one_per_available_promotion():
    cands = promotion_candidates(fx.promotable_warrior(), fx.warrior_promotions())
    assert [c.kind for c in cands] == [ActionKind.PROMOTE_UNIT] * 2
    assert {c.params.promotion_type for c in cands} == {
        "PROMOTION_BATTLECRY",
        "PROMOTION_TORTOISE",
    }
    assert all(c.params.unit.unit_id == fx.WARRIOR_ID for c in cands)
    assert cands[0].facts["effect"]


def test_scheduler_offers_promotion_when_the_blocker_stands():
    game = FakeGame()
    game.promotable = [fx.promotable_warrior()]
    game.extra_blockers = [(PROMOTION_BLOCKER, "Unit can be promoted")]
    core = asyncio.run(LiveObserver(game).core())
    assert core.promotable and core.promotable[0].unit_id == fx.WARRIOR_ID
    step = Scheduler().next(core, TurnLedger(turn=5))
    assert step.category is DecisionCategory.PROMOTION
    assert step.entity == f"unit:{fx.WARRIOR_ID}"


def test_promotion_is_not_read_without_the_blocker():
    game = FakeGame()
    game.promotable = [fx.promotable_warrior()]
    core = asyncio.run(LiveObserver(game).core())
    assert core.promotable == []
    assert game.query_counts["get_promotable_units"] == 0


def test_promote_dispatches_and_confirms_from_output():
    game = FakeGame()
    game.promotable = [fx.promotable_warrior()]
    game.extra_blockers = [(PROMOTION_BLOCKER, "Unit can be promoted")]
    obs = LiveObserver(game)
    core = asyncio.run(obs.core())
    point, inputs = _point(game, obs, core)
    cand = point.candidates[0]
    outcome = asyncio.run(
        Executor(game, sleep=_no_sleep).execute(
            cand, point, current_version=obs.version, turn=5, inputs=inputs
        )
    )
    assert outcome.status is OutcomeStatus.CONFIRMED
    assert ("promote_unit", (fx.WARRIOR_ID, cand.params.promotion_type)) in game.calls
    assert game.promotable == []  # the fake consumed the promotion


def test_promotion_precheck_rejects_a_unit_no_longer_promotable():
    game = FakeGame()
    game.promotable = [fx.promotable_warrior()]
    game.extra_blockers = [(PROMOTION_BLOCKER, "x")]
    obs = LiveObserver(game)
    core = asyncio.run(obs.core())
    point, inputs = _point(game, obs, core)
    game.promotable = []  # promoted by something else meanwhile
    outcome = asyncio.run(
        Executor(game, sleep=_no_sleep).execute(
            point.candidates[0],
            point,
            current_version=obs.version,
            turn=5,
            inputs=inputs,
        )
    )
    assert outcome.status is OutcomeStatus.REJECTED and game.calls == []


def test_promotion_refresh_keeps_units_up_to_date_after_promoting():
    game = FakeGame()
    game.promotable = [fx.promotable_warrior()]
    game.extra_blockers = [(PROMOTION_BLOCKER, "x")]
    obs = LiveObserver(game)
    core = asyncio.run(obs.core())
    asyncio.run(game.promote_unit(fx.WARRIOR_ID, "PROMOTION_BATTLECRY"))
    fresh = asyncio.run(obs.refresh(core, frozenset({"units", "blockers", "popup"})))
    assert fresh.promotable == [] and PROMOTION_BLOCKER not in fresh.blocker_types()
