"""Refresh only what the last action could have changed."""

import asyncio

from drex_fakes import FakeGame

from civ_mcp.drex.candidates import ActionKind
from civ_mcp.drex.live import LiveObserver
from civ_mcp.drex.refresh import FULL, refresh_parts


def test_unit_orders_refresh_units_and_blockers_only():
    for k in (
        ActionKind.MOVE_UNIT,
        ActionKind.FORTIFY_UNIT,
        ActionKind.HEAL_UNIT,
        ActionKind.SKIP_UNIT,
        ActionKind.IMPROVE_TILE,
    ):
        assert refresh_parts(k) == frozenset({"units", "blockers", "popup"}), k


def test_refresh_parts_for_found_city_includes_cities():
    assert {"units", "cities", "blockers"} <= refresh_parts(ActionKind.FOUND_CITY)
    assert {"units", "cities", "blockers"} <= refresh_parts(ActionKind.ATTACK)


def test_production_refreshes_cities_research_refreshes_progress():
    assert refresh_parts(ActionKind.SET_PRODUCTION) == frozenset(
        {"cities", "blockers", "popup"}
    )
    assert refresh_parts(ActionKind.SET_RESEARCH) == frozenset(
        {"progress", "blockers", "popup"}
    )
    assert refresh_parts(ActionKind.SET_CIVIC) == frozenset(
        {"progress", "blockers", "popup"}
    )


def test_diplomacy_and_deals_refresh_sessions_deals_and_overview():
    for k in (ActionKind.DIPLOMACY_RESPOND, ActionKind.DEAL_RESPOND):
        assert refresh_parts(k) == frozenset(
            {"sessions", "deals", "overview", "blockers", "popup"}
        )


def test_every_action_kind_has_a_refresh_rule():
    for k in ActionKind:
        parts = refresh_parts(k)
        assert parts and parts <= FULL and "blockers" in parts, k


def test_refresh_replaces_only_requested_parts_and_bumps_version():
    game = FakeGame()
    obs = LiveObserver(game)
    core = asyncio.run(obs.core())
    game.units[list(game.units)[0]].moves_remaining = 0
    game.cities[list(game.cities)[0]].population = 99
    fresh = asyncio.run(obs.refresh(core, frozenset({"units", "blockers", "popup"})))
    assert fresh.version != core.version
    assert fresh.units[0].moves_remaining == 0
    assert fresh.cities[0].population == core.cities[0].population  # not refreshed
    assert game.query_counts["get_units"] == 2


def test_captured_city_refreshes_cities_units_overview():
    assert refresh_parts(ActionKind.RESOLVE_CAPTURED_CITY) == frozenset(
        {"cities", "units", "overview", "blockers", "popup"}
    )


def test_escape_route_refreshes_units():
    assert refresh_parts(ActionKind.CHOOSE_ESCAPE_ROUTE) == frozenset(
        {"units", "blockers", "popup"}
    )


def test_artifact_refreshes_units_and_overview():
    assert refresh_parts(ActionKind.CHOOSE_ARTIFACT_PLAYER) == frozenset(
        {"units", "overview", "blockers", "popup"}
    )


def test_upgrade_refreshes_units_and_overview():
    assert refresh_parts(ActionKind.UPGRADE_UNIT) == frozenset(
        {"units", "overview", "blockers", "popup"}
    )
