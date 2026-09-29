"""Decision-point assembly: enumeration + context + exclusions per category."""

import dataclasses

import drex_fixtures as fx

from civ_mcp import lua as lq
from civ_mcp.drex.candidates import DecisionCategory
from civ_mcp.drex.observation import (
    CoreObservation,
    DecisionInputs,
    DecisionMemory,
    DecisionSpec,
)
from civ_mcp.drex.points import QUESTIONS, build_decision_point


def _core():
    return CoreObservation(
        version="rome:42:T5:3",
        civ="rome",
        seed=42,
        local_player_id=fx.ME,
        overview=fx.overview(),
        tech=fx.tech_status(),
        progress=lq.ProgressTypes(None, None),
        cities=[fx.capital()],
        units=[fx.warrior()],
    )


def _build(spec, inputs=None, failed=frozenset(), max_options=255):
    return build_decision_point(
        spec,
        _core(),
        inputs or DecisionInputs(),
        DecisionMemory(),
        objective="Grow.",
        decision_id="T5#0003",
        max_options=max_options,
        failed=failed,
    )


def test_research_point_uses_fixed_question_and_current_version():
    point, excluded = _build(DecisionSpec(DecisionCategory.RESEARCH, "empire"))
    assert point.question == QUESTIONS[DecisionCategory.RESEARCH]
    assert point.observation_version == "rome:42:T5:3"
    assert point.decision_id == "T5#0003"
    assert len(point.candidates) == 3 and excluded == []


def test_failed_candidates_are_excluded_and_logged():
    point, excluded = _build(
        DecisionSpec(DecisionCategory.RESEARCH, "empire"),
        failed=frozenset({"research:TECHNOLOGY_POTTERY"}),
    )
    assert "research:TECHNOLOGY_POTTERY" not in point.label_to_id.values()
    assert excluded[0].option == "research:TECHNOLOGY_POTTERY"


def test_unit_point_includes_subject_and_filtered_surroundings():
    inputs = DecisionInputs(
        unit=fx.warrior(),
        action_space=fx.warrior_space(),
        nearby_tiles=fx.tiles_around_warrior(),
    )
    point, excluded = _build(
        DecisionSpec(DecisionCategory.UNIT, f"unit:{fx.WARRIOR_ID}"), inputs
    )
    assert point.entity == f"unit:{fx.WARRIOR_ID}"
    assert point.context["subject"]["unit"] == "Warrior"
    assert all(t["visibility"] != "unexplored" for t in point.context["surroundings"])
    assert {e.option for e in excluded} >= {"move to (10,11)", "move to (9,11)"}


def test_production_point_logs_placement_exclusions():
    inputs = DecisionInputs(
        city=fx.capital(),
        production_options=fx.production_options(),
        wonder_types=sorted(fx.WONDERS),
    )
    point, excluded = _build(
        DecisionSpec(DecisionCategory.PRODUCTION, f"city:{fx.CAPITAL_ID}"), inputs
    )
    assert {"DISTRICT_HOLY_SITE", "BUILDING_PYRAMIDS"} <= {e.option for e in excluded}
    assert point.context["subject"]["city"] == "Roma"


def test_policy_point_targets_lowest_empty_slot_with_options():
    status = fx.policies(slot_policy="POLICY_DISCIPLINE")
    point, _ = _build(
        DecisionSpec(DecisionCategory.POLICY, "empire"), DecisionInputs(policies=status)
    )
    assert point.entity == "slot:1"
    assert set(point.label_to_id.values()) == {"policy:1:POLICY_URBAN_PLANNING"}


def test_government_point_offers_keep_and_adopt():
    point, _ = _build(
        DecisionSpec(DecisionCategory.GOVERNMENT, "empire"),
        DecisionInputs(governments=fx.governments()),
    )
    assert set(point.label_to_id.values()) == {
        "government:keep",
        "government:GOVERNMENT_CHIEFDOM",
    }


def test_no_candidates_yields_no_point():
    point, _ = _build(
        DecisionSpec(DecisionCategory.PANTHEON, "empire"),
        DecisionInputs(pantheon=fx.pantheon(has=True)),
    )
    assert point is None


def test_final_unit_decision_offers_only_turn_ending_orders():
    from civ_mcp.drex.candidates import ActionKind

    inputs = DecisionInputs(unit=fx.warrior(), action_space=fx.warrior_space())
    point, excluded = build_decision_point(
        DecisionSpec(DecisionCategory.UNIT, f"unit:{fx.WARRIOR_ID}"),
        _core(),
        inputs,
        DecisionMemory(),
        objective="Grow.",
        decision_id="T5#0009",
        max_options=255,
        exclude_kinds=frozenset({ActionKind.MOVE_UNIT}),
    )
    assert ActionKind.MOVE_UNIT not in {c.kind for c in point.candidates}
    assert any("turn-ending" in e.reason for e in excluded)


def test_option_limit_is_applied_deterministically():
    inputs = DecisionInputs(unit=fx.warrior(), action_space=fx.warrior_space())
    point, excluded = _build(
        DecisionSpec(DecisionCategory.UNIT, f"unit:{fx.WARRIOR_ID}"),
        inputs,
        max_options=4,
    )
    assert len(point.candidates) == 4
    assert any("limit" in e.reason for e in excluded)


def test_diplomacy_and_deal_points_carry_subject_context():
    point, _ = _build(
        DecisionSpec(DecisionCategory.DIPLOMACY, "player:1"),
        DecisionInputs(session=fx.session()),
    )
    assert point.context["subject"]["leader"] == "Cleopatra"
    point, _ = _build(
        DecisionSpec(DecisionCategory.DEAL, "player:1"), DecisionInputs(deal=fx.deal())
    )
    assert point.context["subject"]["they_give"][0]["item"] == "Gold per turn"


def test_envoy_point():
    point, _ = _build(
        DecisionSpec(DecisionCategory.ENVOY, "empire"),
        DecisionInputs(envoys=fx.envoys()),
    )
    assert set(point.label_to_id) == {"Kabul", "Geneva"}
    assert dataclasses.is_dataclass(point)


def test_failure_leaving_one_alternative_never_forces_it():
    point, excluded = _build(
        DecisionSpec(DecisionCategory.DEAL, "player:1"),
        DecisionInputs(deal=fx.deal()),
        failed=frozenset({"deal:1:reject"}),
    )
    assert point is None
    assert any("no untried alternative" in e.reason for e in excluded)


def test_only_legal_option_is_marked_forced_single():
    status = fx.tech_status()
    status.available_techs = status.available_techs[:1]
    core = _core()
    core.tech = status
    point, _ = build_decision_point(
        DecisionSpec(DecisionCategory.RESEARCH, "empire"),
        core,
        DecisionInputs(),
        DecisionMemory(),
        objective="Grow.",
        decision_id="T5#0004",
        max_options=255,
    )
    assert point.legal_count == 1
    assert point.forced_rule == "forced_single_candidate"


def test_turn_ending_rule_leaving_one_order_is_labeled_as_such():
    from civ_mcp.drex.candidates import ActionKind

    space = fx.warrior_space(fortify_turns=2)
    space.targets = []
    point, _ = build_decision_point(
        DecisionSpec(DecisionCategory.UNIT, f"unit:{fx.WARRIOR_ID}"),
        _core(),
        DecisionInputs(unit=fx.warrior(), action_space=space),
        DecisionMemory(),
        objective="Grow.",
        decision_id="T5#0005",
        max_options=255,
        exclude_kinds=frozenset({ActionKind.MOVE_UNIT}),
    )
    assert [c.kind for c in point.candidates] == [ActionKind.SKIP_UNIT]
    assert point.forced_rule == "forced_turn_ending_order"
