"""Core queries must run on the base game, where Rise and Fall / Gathering Storm
APIs (eras, diplomatic favor, loyalty) are absent: every such call is guarded.

Verified live 2026-09-29 on a base-game-only install: unguarded
``p:GetFavor()`` and ``Game.GetEras()`` raised "function expected instead of nil".
"""

import pytest

from civ_mcp import lua as lq

EXPANSION_ONLY_CALLS = ("GetFavor()", "Game.GetEras()", "GetCulturalIdentity()")


@pytest.mark.parametrize(
    "name,lua",
    [
        ("overview", lq.build_overview_query()),
        ("tech_civics", lq.build_tech_civics_query()),
        ("cities", lq.build_cities_query()),
    ],
    ids=["overview", "tech_civics", "cities"],
)
def test_expansion_only_calls_are_pcall_guarded(name, lua):
    unguarded = [
        line.strip()
        for line in lua.splitlines()
        if any(call in line for call in EXPANSION_ONLY_CALLS) and "pcall" not in line
    ]
    assert unguarded == [], f"{name}: {unguarded}"
