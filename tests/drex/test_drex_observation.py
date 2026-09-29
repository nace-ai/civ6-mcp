"""Model-visible context: visibility filtering, privileged-data exclusion, memory."""

import json

import drex_fixtures as fx

from civ_mcp import lua as lq
from civ_mcp.drex.candidates import DecisionCategory
from civ_mcp.drex.observation import (
    Blocker,
    CoreObservation,
    DecisionInputs,
    DecisionMemory,
    DecisionSpec,
    Fact,
    build_context,
    empire_summary,
    visible_tiles,
)
from civ_mcp.drex.serialize import from_jsonable, to_jsonable


def _core(**kw):
    base = dict(
        version="rome:42:T5:1",
        civ="rome",
        seed=42,
        local_player_id=fx.ME,
        overview=fx.overview(),
        tech=fx.tech_status(),
        progress=lq.ProgressTypes(research_type=None, civic_type="CIVIC_CODE_OF_LAWS"),
        cities=[fx.capital()],
        units=[fx.warrior(), fx.settler(), fx.builder()],
        diplomacy_sessions=[],
        pending_deals=[],
        blockers=[Blocker("ENDTURN_BLOCKING_RESEARCH", "Choose research")],
    )
    base.update(kw)
    return CoreObservation(**base)


class TestVisibility:
    def test_unexplored_tiles_are_removed(self):
        coords = {
            tuple(t["xy"]) for t in visible_tiles(fx.tiles_around_warrior(), me=fx.ME)
        }
        assert (8, 12) not in coords

    def test_fogged_tiles_drop_units_owner_and_yields(self):
        fogged = next(
            t
            for t in visible_tiles(fx.tiles_around_warrior(), me=fx.ME)
            if t["xy"] == [12, 13]
        )
        assert fogged["visibility"] == "revealed"
        assert "foreign_units" not in fogged
        assert "owner" not in fogged
        assert "yields" not in fogged
        assert fogged["resource"] == "Horses"

    def test_visible_tiles_keep_visible_foreign_units(self):
        tile = next(
            t
            for t in visible_tiles(fx.tiles_around_warrior(), me=fx.ME)
            if t["xy"] == [9, 12]
        )
        assert tile["foreign_units"] == ["Barbarian WARRIOR"]

    def test_empire_summary_excludes_privileged_rival_and_map_data(self):
        summary = empire_summary(_core())
        text = json.dumps(summary)
        assert "Egypt" not in text
        assert "900" not in text
        assert summary["current_research"] == "None"
        assert summary["gold"] == 35

    def test_context_never_contains_hidden_tiles(self):
        inputs = DecisionInputs(
            unit=fx.warrior(),
            action_space=fx.warrior_space(),
            nearby_tiles=fx.tiles_around_warrior(),
        )
        ctx = build_context(
            DecisionSpec(DecisionCategory.UNIT, f"unit:{fx.WARRIOR_ID}"),
            _core(),
            inputs,
            DecisionMemory(),
            objective="Expand and develop.",
        )
        text = json.dumps(ctx)
        assert "SPEARMAN" not in text
        assert "DESERT" not in text.upper()
        assert ctx["objective"] == "Expand and develop."
        assert ctx["subject"]["unit"] == "Warrior"


class TestMemory:
    def test_memory_is_bounded_structured_facts(self):
        mem = DecisionMemory(max_facts=2)
        for i in range(3):
            mem.record(
                Fact(
                    turn=i,
                    category="research",
                    subject="empire",
                    action=f"a{i}",
                    result="confirmed",
                )
            )
        assert [f["action"] for f in mem.recent()] == ["a1", "a2"]
        assert set(mem.recent()[0]) == {
            "turn",
            "category",
            "subject",
            "action",
            "result",
        }

    def test_memory_resets_for_a_different_game(self):
        mem = DecisionMemory()
        mem.bind_game(("rome", 42))
        mem.record(Fact(1, "research", "empire", "Pottery", "confirmed"))
        mem.bind_game(("egypt", 7))
        assert mem.recent() == []


class TestSerialization:
    def test_core_observation_round_trips_through_json(self):
        core = _core(diplomacy_sessions=[fx.session()], pending_deals=[fx.deal()])
        data = json.loads(json.dumps(to_jsonable(core)))
        back = from_jsonable(CoreObservation, data)
        assert back == core

    def test_decision_inputs_round_trip(self):
        inputs = DecisionInputs(
            unit=fx.warrior(),
            action_space=fx.warrior_space(),
            city=fx.capital(),
            production_options=fx.production_options(),
            wonder_types=sorted(fx.WONDERS),
            nearby_tiles=fx.tiles_around_warrior(),
            policies=fx.policies(),
            governments=fx.governments(),
            envoys=fx.envoys(),
            pantheon=fx.pantheon(),
        )
        data = json.loads(json.dumps(to_jsonable(inputs)))
        assert from_jsonable(DecisionInputs, data) == inputs


def test_fogged_tiles_drop_possibly_stale_feature_improvement_and_district():
    tiles = fx.tiles_around_warrior()
    fogged = next(t for t in tiles if t.visibility == "revealed")
    fogged.feature, fogged.improvement, fogged.district = (
        "FEATURE_FOREST",
        "IMPROVEMENT_FARM",
        "DISTRICT_CAMPUS",
    )
    tile = next(
        t for t in visible_tiles(tiles, me=fx.ME) if t["visibility"] == "revealed"
    )
    assert {"feature", "improvement", "district"}.isdisjoint(tile)
    assert tile["terrain"] == "Grass"
