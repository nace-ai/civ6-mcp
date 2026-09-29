"""Typed per-unit action space and small verification queries (parsers)."""

from civ_mcp.lua.action_space import (
    build_eligibility_query,
    build_unit_action_space_query,
    parse_available_governments,
    parse_eligibility,
    parse_progress_types,
    parse_unit_action_space,
    parse_unit_state,
    parse_wonder_types,
)

SPACE_LINES = [
    "UNIT|131073|1|UNIT_WARRIOR|10,12|2.0|0|0|1|0|0|0|100/100",
    "REACH|11,12|TERRAIN_GRASS|none|none|0|1|0|visible|0|0|1",
    "REACH|12,12|TERRAIN_PLAINS|FEATURE_FOREST|RESOURCE_DEER:RESOURCECLASS_BONUS|1|0|-2|revealed|0|0|2",
    "REACH|10,11|TERRAIN_GRASS|none|none|0|0|-1|visible|1|0|1",
    "TARGET|9,12|MELEE|63|Barbarian|UNIT_WARRIOR|70/100|1|20",
    "---END---",
]


class TestUnitActionSpace:
    def test_parses_unit_header(self):
        space = parse_unit_action_space(SPACE_LINES)
        assert space is not None
        assert (space.unit_id, space.unit_index) == (131073, 1)
        assert space.unit_type == "UNIT_WARRIOR"
        assert (space.x, space.y) == (10, 12)
        assert space.moves_remaining == 2.0
        assert space.is_civilian is False
        assert space.can_found is False
        assert space.can_fortify is True
        assert space.can_heal is False
        assert (space.hp, space.max_hp) == (100, 100)

    def test_parses_reachable_tiles_with_visibility(self):
        space = parse_unit_action_space(SPACE_LINES)
        by_xy = {(t.x, t.y): t for t in space.reachable}
        grass = by_xy[(11, 12)]
        assert grass.visibility == "visible"
        assert grass.is_river is True
        assert grass.owner_id == 0
        forest = by_xy[(12, 12)]
        assert forest.feature == "FEATURE_FOREST"
        assert forest.resource == "RESOURCE_DEER"
        assert forest.is_hills is True
        assert forest.visibility == "revealed"
        assert forest.owner_id == -2
        assert by_xy[(10, 11)].own_stack_conflict is True

    def test_parses_typed_attack_targets(self):
        (target,) = parse_unit_action_space(SPACE_LINES).targets
        assert (target.x, target.y) == (9, 12)
        assert target.attack_type == "MELEE"
        assert target.owner_id == 63
        assert target.unit_type == "UNIT_WARRIOR"
        assert (target.hp, target.max_hp) == (70, 100)
        assert target.distance == 1

    def test_missing_unit_returns_none(self):
        assert parse_unit_action_space(["ERR:UNIT_NOT_FOUND", "---END---"]) is None

    def test_malformed_lines_are_dropped(self):
        space = parse_unit_action_space(
            SPACE_LINES[:1] + ["REACH|garbage", "TARGET|1,2|MELEE"] + ["---END---"]
        )
        assert space is not None
        assert space.reachable == [] and space.targets == []

    def test_query_filters_targets_by_current_visibility_and_uses_hex_adjacency(self):
        lua = build_unit_action_space_query(7)
        assert "UnitManager.GetUnit(me, 7)" in lua
        assert "GetReachableMovement" in lua
        assert "Map.GetAdjacentPlot" in lua
        assert "IsVisible" in lua


class TestSmallQueries:
    def test_wonder_types(self):
        assert parse_wonder_types(
            ["WONDER|BUILDING_PYRAMIDS", "WONDER|BUILDING_STONEHENGE", "---END---"]
        ) == {"BUILDING_PYRAMIDS", "BUILDING_STONEHENGE"}

    def test_progress_types(self):
        p = parse_progress_types(["PROGRESS|TECHNOLOGY_POTTERY|NONE"])
        assert p.research_type == "TECHNOLOGY_POTTERY"
        assert p.civic_type is None

    def test_eligibility(self):
        assert parse_eligibility(["ELIGIBLE|1|engine"]) == (True, "engine")
        assert parse_eligibility(["ELIGIBLE|0|completed"]) == (False, "completed")
        assert parse_eligibility([]) == (False, "no_response")

    def test_eligibility_query_rejects_unknown_kind(self):
        import pytest

        with pytest.raises(ValueError):
            build_eligibility_query("religion", "X")

    def test_unit_state(self):
        s = parse_unit_state(["STATE|10|12|0.0|1|100|0"])
        assert (
            s.x,
            s.y,
            s.moves_remaining,
            s.fortify_turns,
            s.hp,
            s.build_charges,
        ) == (
            10,
            12,
            0.0,
            1,
            100,
            0,
        )
        assert parse_unit_state(["STATE|GONE"]) is None

    def test_available_governments(self):
        govs = parse_available_governments(
            [
                "GOV|GOVERNMENT_CHIEFDOM|1|AVAILABLE|Chiefdom|SLOT_MILITARY,SLOT_ECONOMIC|+1 CS",
                "GOV|GOVERNMENT_TRIBE|0|CURRENT|Tribe||",
                "---END---",
            ]
        )
        assert [g.government_type for g in govs] == [
            "GOVERNMENT_CHIEFDOM",
            "GOVERNMENT_TRIBE",
        ]
        assert govs[0].slots == ["SLOT_MILITARY", "SLOT_ECONOMIC"]
        assert govs[1].is_current is True
