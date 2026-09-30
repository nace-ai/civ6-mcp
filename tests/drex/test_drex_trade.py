"""Idle traders start trade routes chosen by Drex."""

import asyncio

import drex_fixtures as fx
from drex_fakes import FakeGame

from civ_mcp.drex.candidates import ActionKind, DecisionCategory
from civ_mcp.drex.enumerate import trade_route_candidates
from civ_mcp.drex.executor import Executor, OutcomeStatus
from civ_mcp.drex.live import LiveObserver
from civ_mcp.drex.observation import DecisionMemory, DecisionSpec
from civ_mcp.drex.points import build_decision_point


async def _no_sleep(_):
    return None


def _trader_game():
    game = FakeGame()
    game.units[fx.TRADER_ID] = fx.trader()
    game.spaces[fx.TRADER_IDX] = fx.trader_space()
    game.trade_status = fx.trade_status(capacity=2, active=0)
    game.trade_destinations = fx.trade_destinations()
    return game


def _point(obs, core):
    spec = DecisionSpec(DecisionCategory.UNIT, f"unit:{fx.TRADER_ID}")
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


def test_trade_route_candidates_one_per_destination():
    cands = trade_route_candidates(
        fx.trader(), fx.trader_space(), fx.trade_status(2, 0), fx.trade_destinations()
    )
    assert len(cands) == 2 and all(c.kind is ActionKind.MAKE_TRADE_ROUTE for c in cands)
    kabul = next(c for c in cands if c.params.city_name == "Kabul")
    assert kabul.facts["city_state"] is True and kabul.facts["quest"] is True
    assert kabul.params.unit.unit_index == fx.TRADER_IDX
    assert "distance" in kabul.facts


def test_no_trade_route_candidates_without_capacity():
    cands = trade_route_candidates(
        fx.trader(), fx.trader_space(), fx.trade_status(1, 1), fx.trade_destinations()
    )
    assert cands == []


def test_trader_on_route_is_not_offered_a_new_route():
    status = fx.trade_status(2, 1, on_route=True)
    cands = trade_route_candidates(
        fx.trader(), fx.trader_space(), status, fx.trade_destinations()
    )
    assert cands == []


def test_live_inputs_read_status_and_destinations_for_a_trader_only():
    game = _trader_game()
    obs = LiveObserver(game)
    core = asyncio.run(obs.core())
    rt = game.conn.roundtrips
    spec = DecisionSpec(DecisionCategory.UNIT, f"unit:{fx.TRADER_ID}")
    inputs = asyncio.run(obs.inputs(spec, core))
    assert inputs.trade_status is not None and inputs.trade_destinations
    trader_rt = game.conn.roundtrips - rt
    rt = game.conn.roundtrips
    warrior = DecisionSpec(DecisionCategory.UNIT, f"unit:{fx.WARRIOR_ID}")
    inputs = asyncio.run(obs.inputs(warrior, core))
    assert inputs.trade_status is None
    # a trader costs exactly two more reads: route status and destinations
    assert trader_rt - (game.conn.roundtrips - rt) == 2


def test_unit_decision_for_a_trader_includes_routes_and_normal_orders():
    game = _trader_game()
    obs = LiveObserver(game)
    core = asyncio.run(obs.core())
    point, _ = _point(obs, core)
    kinds = {c.kind for c in point.candidates}
    assert ActionKind.MAKE_TRADE_ROUTE in kinds and ActionKind.SKIP_UNIT in kinds


def test_make_trade_route_dispatches_and_confirms():
    game = _trader_game()
    obs = LiveObserver(game)
    core = asyncio.run(obs.core())
    point, inputs = _point(obs, core)
    cand = next(
        c
        for c in point.candidates
        if c.kind is ActionKind.MAKE_TRADE_ROUTE and c.params.city_name == "Kabul"
    )
    outcome = asyncio.run(
        Executor(game, sleep=_no_sleep).execute(
            cand, point, current_version=obs.version, turn=5, inputs=inputs
        )
    )
    assert outcome.status is OutcomeStatus.CONFIRMED
    assert (
        "make_trade_route",
        (fx.TRADER_IDX, cand.params.target_x, cand.params.target_y),
    ) in game.calls
    assert game.trade_status.active_count == 1


def test_trade_route_precheck_rejects_a_destination_that_vanished():
    game = _trader_game()
    obs = LiveObserver(game)
    core = asyncio.run(obs.core())
    point, _ = _point(obs, core)
    cand = next(c for c in point.candidates if c.kind is ActionKind.MAKE_TRADE_ROUTE)
    game.trade_destinations = []
    outcome = asyncio.run(
        Executor(game, sleep=_no_sleep).execute(
            cand, point, current_version="stale", turn=5, inputs=None
        )
    )
    assert outcome.status is OutcomeStatus.REJECTED and not any(
        m == "make_trade_route" for m, _ in game.calls
    )
