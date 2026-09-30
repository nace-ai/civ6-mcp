"""Lua queries must survive rulesets and states that lack optional APIs.

Base-ruleset games have no loyalty (GetCulturalIdentity) and the GameCore
Lua state has no UnitManager; an unguarded call aborts the whole query
("function expected instead of nil"), which silently dropped every
foreign-policy decision and post-turn snapshot on 2026-09-30.
"""

import re

from civ_mcp import lua as lq


def _guarded(src: str, call: str) -> bool:
    return all(
        "pcall" in line
        for line in src.splitlines()
        if call in line and not line.strip().startswith("--")
    )


def test_diplomacy_query_guards_loyalty_api():
    assert _guarded(lq.build_diplomacy_query(), "GetCulturalIdentity")


def test_units_query_guards_ui_only_unit_manager():
    src = lq.build_units_query()
    # the ranged line-of-sight probe was the one bare call; the others sit in pcall blocks
    los_lines = [
        l for l in src.splitlines() if "RANGE_ATTACK" in l and "UnitManager" in l
    ]
    assert los_lines and all("pcall" in l for l in los_lines)


def test_lua_sources_still_render():
    for builder in (lq.build_diplomacy_query, lq.build_units_query):
        assert re.search(r"\bprint\(", builder())
