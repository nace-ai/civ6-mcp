"""Candidate enumeration from existing typed observations."""

import drex_fixtures as fx

from civ_mcp.drex.candidates import (
    ActionKind,
    AttackParams,
    ImproveParams,
    MoveParams,
    ProductionParams,
    UnitOrderParams,
)
from civ_mcp.drex.enumerate import (
    civic_candidates,
    deal_candidates,
    diplomacy_candidates,
    envoy_candidates,
    government_candidates,
    pantheon_candidates,
    policy_candidates,
    production_candidates,
    research_candidates,
    shortlist,
    unit_candidates,
)


def _ids(cands):
    return sorted(c.candidate_id for c in cands)


class TestResearchAndCivics:
    def test_research_candidates_carry_tech_types(self):
        cands = research_candidates(fx.tech_status())
        assert _ids(cands) == [
            "research:TECHNOLOGY_ANIMAL_HUSBANDRY",
            "research:TECHNOLOGY_MINING",
            "research:TECHNOLOGY_POTTERY",
        ]
        mining = next(c for c in cands if c.params.tech_type == "TECHNOLOGY_MINING")
        assert mining.label == "Mining"
        assert mining.facts["turns"] == 4
        assert mining.facts["boosted"] is True

    def test_civic_candidates_carry_civic_types(self):
        (c,) = civic_candidates(fx.tech_status())
        assert c.kind is ActionKind.SET_CIVIC
        assert c.params.civic_type == "CIVIC_CODE_OF_LAWS"


class TestProduction:
    def test_placement_dependent_items_are_excluded_with_reasons(self):
        cands, excluded = production_candidates(
            fx.capital(), fx.production_options(), fx.WONDERS
        )
        reasons = {e.option: e.reason for e in excluded}
        assert (
            "DISTRICT_HOLY_SITE" in reasons
            and "placement" in reasons["DISTRICT_HOLY_SITE"]
        )
        assert (
            "BUILDING_PYRAMIDS" in reasons and "wonder" in reasons["BUILDING_PYRAMIDS"]
        )
        names = {c.params.item_name for c in cands}
        assert "DISTRICT_HOLY_SITE" not in names and "BUILDING_PYRAMIDS" not in names

    def test_supported_items_map_to_typed_production(self):
        cands, _ = production_candidates(
            fx.capital(), fx.production_options(), fx.WONDERS
        )
        by_name = {c.params.item_name: c.params for c in cands}
        assert by_name["UNIT_SETTLER"] == ProductionParams(
            fx.CAPITAL_ID, "UNIT", "UNIT_SETTLER"
        )
        assert by_name["BUILDING_MONUMENT"].item_type == "BUILDING"
        assert by_name["PROJECT_ENHANCE_DISTRICT_ENCAMPMENT"].item_type == "PROJECT"

    def test_district_repair_keeps_its_coordinates(self):
        cands, _ = production_candidates(
            fx.capital(), fx.production_options(), fx.WONDERS
        )
        repair = next(c for c in cands if c.params.item_name == "DISTRICT_ENCAMPMENT")
        assert (repair.params.target_x, repair.params.target_y) == (11, 11)
        assert repair.facts["repair"] is True

    def test_building_listed_twice_as_repair_is_offered_once(self):
        from civ_mcp import lua as lq

        opts = [
            lq.ProductionOption("BUILDING", "BUILDING_BARRACKS", 90, 18),
            lq.ProductionOption("BUILDING", "BUILDING_BARRACKS", 90, 9, is_repair=True),
        ]
        cands, _ = production_candidates(fx.capital(), opts, set())
        assert len(cands) == 1 and cands[0].facts["repair"] is True

    def test_unknown_category_is_excluded(self):
        from civ_mcp import lua as lq

        cands, excluded = production_candidates(
            fx.capital(), [lq.ProductionOption("ALIEN", "X", 1, 1)], set()
        )
        assert cands == [] and excluded[0].option == "X"


class TestUnits:
    def test_warrior_moves_exclude_conflicts_and_visible_foreign_tiles(self):
        cands, excluded = unit_candidates(fx.warrior_space(), fx.warrior(), me=fx.ME)
        moves = [c for c in cands if c.kind is ActionKind.MOVE_UNIT]
        assert sorted((c.params.to_x, c.params.to_y) for c in moves) == [
            (11, 12),
            (11, 13),
            (12, 13),
        ]
        excluded_options = {e.option for e in excluded}
        assert "move to (10,11)" in excluded_options
        assert "move to (9,11)" in excluded_options

    def test_moves_use_unit_index_and_composite_id_separately(self):
        cands, _ = unit_candidates(fx.warrior_space(), fx.warrior(), me=fx.ME)
        move = next(c for c in cands if c.kind is ActionKind.MOVE_UNIT)
        assert isinstance(move.params, MoveParams)
        assert move.params.unit.unit_id == fx.WARRIOR_ID
        assert move.params.unit.unit_index == fx.WARRIOR_IDX

    def test_attack_candidates_are_typed(self):
        cands, _ = unit_candidates(fx.warrior_space(), fx.warrior(), me=fx.ME)
        (attack,) = [c for c in cands if c.kind is ActionKind.ATTACK]
        assert isinstance(attack.params, AttackParams)
        assert (attack.params.target_x, attack.params.target_y) == (9, 12)
        assert attack.params.attack_type == "MELEE"
        assert attack.params.target_owner_id == 63

    def test_fortify_only_when_eligible_and_not_already_fortified(self):
        kinds = {
            c.kind for c in unit_candidates(fx.warrior_space(), fx.warrior(), me=0)[0]
        }
        assert ActionKind.FORTIFY_UNIT in kinds and ActionKind.SKIP_UNIT in kinds
        kinds = {
            c.kind
            for c in unit_candidates(
                fx.warrior_space(fortify_turns=2), fx.warrior(), me=0
            )[0]
        }
        assert ActionKind.FORTIFY_UNIT not in kinds

    def test_settler_can_found(self):
        cands, _ = unit_candidates(fx.settler_space(), fx.settler(), me=fx.ME)
        (found,) = [c for c in cands if c.kind is ActionKind.FOUND_CITY]
        assert (
            found.params == UnitOrderParams(unit=found.params.unit)
            and found.params.unit.unit_index == fx.SETTLER_IDX
        )
        assert ActionKind.FORTIFY_UNIT not in {c.kind for c in cands}

    def test_builder_improvements_come_from_its_own_tile(self):
        unit = fx.builder(valid=("IMPROVEMENT_FARM", "IMPROVEMENT_MINE", "BUILD_ROUTE"))
        cands, excluded = unit_candidates(fx.builder_space(), unit, me=fx.ME)
        improves = [c for c in cands if c.kind is ActionKind.IMPROVE_TILE]
        assert sorted(c.params.improvement_type for c in improves) == [
            "IMPROVEMENT_FARM",
            "IMPROVEMENT_MINE",
        ]
        assert all(isinstance(c.params, ImproveParams) for c in improves)
        assert "BUILD_ROUTE" in {e.option for e in excluded}

    def test_improvements_dropped_if_unit_moved_since_units_query(self):
        unit = fx.builder()
        unit.x, unit.y = 5, 5
        cands, _ = unit_candidates(fx.builder_space(), unit, me=fx.ME)
        assert not [c for c in cands if c.kind is ActionKind.IMPROVE_TILE]

    def test_unit_without_moves_has_no_candidates(self):
        space = fx.warrior_space()
        space.moves_remaining = 0.0
        assert unit_candidates(space, fx.warrior(), me=fx.ME)[0] == []


class TestReactiveAndGovernance:
    def test_diplomacy_offers_verified_response_vocabulary(self):
        cands = diplomacy_candidates(fx.session())
        assert _ids(cands) == ["diplomacy:1:NEGATIVE", "diplomacy:1:POSITIVE"]

    def test_informational_sessions_are_not_decisions(self):
        assert diplomacy_candidates(fx.session(is_at_war=True)) == []
        assert diplomacy_candidates(fx.session(buttons="GOODBYE")) == []
        assert diplomacy_candidates(fx.session(deal_summary="They offer: Gold")) == []

    def test_deal_accept_or_reject(self):
        assert _ids(deal_candidates(fx.deal())) == ["deal:1:accept", "deal:1:reject"]

    def test_policies_match_slot_type_and_skip_slotted(self):
        status = fx.policies(slot_policy="POLICY_DISCIPLINE")
        econ = policy_candidates(status, status.slots[1])
        assert _ids(econ) == ["policy:1:POLICY_URBAN_PLANNING"]
        status = fx.policies()
        mil = policy_candidates(status, status.slots[0])
        assert _ids(mil) == ["policy:0:POLICY_DISCIPLINE", "policy:0:POLICY_SURVEY"]

    def test_envoys_only_to_eligible_city_states(self):
        assert _ids(envoy_candidates(fx.envoys())) == ["envoy:20", "envoy:21"]
        assert envoy_candidates(fx.envoys(tokens=0)) == []

    def test_government_keep_or_adopt(self):
        cands = government_candidates(fx.governments(), current_type="NONE")
        assert _ids(cands) == ["government:GOVERNMENT_CHIEFDOM", "government:keep"]

    def test_pantheon_beliefs(self):
        assert len(pantheon_candidates(fx.pantheon())) == 2
        assert pantheon_candidates(fx.pantheon(has=True)) == []


def test_shortlist_keeps_non_moves_then_nearest_moves():
    cands, _ = unit_candidates(fx.warrior_space(), fx.warrior(), me=fx.ME)
    kept, dropped = shortlist(cands, limit=4)
    kinds = [c.kind for c in kept]
    assert ActionKind.ATTACK in kinds and ActionKind.SKIP_UNIT in kinds
    assert ActionKind.FORTIFY_UNIT in kinds
    assert len(kept) == 4
    (move,) = [c for c in kept if c.kind is ActionKind.MOVE_UNIT]
    assert move.facts["distance"] == 1
    assert len(dropped) == 2 and all("limit" in e.reason for e in dropped)


def test_envoy_label_falls_back_when_name_is_empty():
    status = fx.envoys()
    status.city_states[0].name = ""
    assert "City-state 20" in {c.label for c in envoy_candidates(status)}


def test_revealed_destination_omits_possibly_stale_feature():
    space = fx.warrior_space()
    fogged = next(t for t in space.reachable if t.visibility == "revealed")
    fogged.feature = "FEATURE_FOREST"
    cands, _ = unit_candidates(space, fx.warrior(), me=fx.ME)
    move = next(
        c
        for c in cands
        if c.kind is ActionKind.MOVE_UNIT
        and (c.params.to_x, c.params.to_y) == (fogged.x, fogged.y)
    )
    assert move.facts["feature"] is None
