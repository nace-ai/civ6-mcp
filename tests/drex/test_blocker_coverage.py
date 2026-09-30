"""Every end-turn blocker type the engine can raise is classified."""

from pathlib import Path

from civ_mcp.drex.scheduler import (
    HOUSEKEEPING_BLOCKERS,
    PHASE_LATER_BLOCKERS,
    SUPPORTED_BLOCKERS,
)
from civ_mcp.lua.drex_queries import (
    build_end_turn_blocking_types_query,
    parse_end_turn_blocking_types,
)

FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "fixtures"
    / "drex"
    / "end_turn_blocking_types.txt"
)


def _names() -> set[str]:
    names = {
        ln.split("|")[0]
        for ln in FIXTURE.read_text().splitlines()
        if ln and not ln.startswith("#")
    }
    names.discard("NO_ENDTURN_BLOCKING")
    return names


def test_every_engine_blocker_type_is_classified():
    classified = SUPPORTED_BLOCKERS | HOUSEKEEPING_BLOCKERS | PHASE_LATER_BLOCKERS
    unclassified = sorted(_names() - classified)
    assert unclassified == [], unclassified
    assert not (SUPPORTED_BLOCKERS & PHASE_LATER_BLOCKERS)
    assert not (SUPPORTED_BLOCKERS & HOUSEKEEPING_BLOCKERS)


def test_live_fixture_holds_no_unknown_types_and_sources_list_is_classified():
    expected = {
        "UNITS",
        "UNIT_NEEDS_ORDERS",
        "STACKED_UNITS",
        "PRODUCTION",
        "RESEARCH",
        "CIVIC",
        "FILL_CIVIC_SLOT",
        "CONSIDER_GOVERNMENT_CHANGE",
        "GIVE_INFLUENCE_TOKEN",
        "PANTHEON",
        "RELIGION",
        "BELIEF",
        "UNIT_PROMOTION",
        "GOVERNOR_APPOINTMENT",
        "GOVERNOR_IDLE",
        "GOVERNOR_OPPORTUNITY",
        "GOVERNOR_PROMOTION",
        "COMMEMORATION_AVAILABLE",
        "CLAIM_GREAT_PERSON",
        "CONSIDER_RAZE_CITY",
        "CONSIDER_DISLOYAL_CITY",
        "SPY_CHOOSE_ESCAPE_ROUTE",
        "SPY_CHOOSE_DRAGNET_PRIORITY",
        "ARTIFACT",
        "EMERGENCY_NEEDS_ATTENTION",
        "CITY_RANGE_ATTACK",
        "DISTRICT_RANGE_ATTACK",
        "WORLD_CONGRESS_SESSION",
        "WORLD_CONGRESS_SPECIAL_SESSION",
        "WORLD_CONGRESS_LOOK",
    }
    names = {n.removeprefix("ENDTURN_BLOCKING_") for n in _names()}
    # The live dump is the Base ruleset (21 types); every live type must be one
    # the classification knows, and every known type must be classified.
    unknown = sorted(names - expected)
    assert unknown == [], unknown
    from civ_mcp.drex.scheduler import (
        HOUSEKEEPING_BLOCKERS,
        PHASE_LATER_BLOCKERS,
        SUPPORTED_BLOCKERS,
    )

    classified = {
        n.removeprefix("ENDTURN_BLOCKING_")
        for n in SUPPORTED_BLOCKERS | HOUSEKEEPING_BLOCKERS | PHASE_LATER_BLOCKERS
    }
    assert expected <= classified, sorted(expected - classified)


def test_blocking_types_query_and_parser():
    lua = build_end_turn_blocking_types_query()
    assert "EndTurnBlockingTypes" in lua and "BT|" in lua
    parsed = parse_end_turn_blocking_types(
        ["BT|ENDTURN_BLOCKING_UNITS|1", "BT|NO_ENDTURN_BLOCKING|0", "noise"]
    )
    assert parsed == {"ENDTURN_BLOCKING_UNITS": 1, "NO_ENDTURN_BLOCKING": 0}
