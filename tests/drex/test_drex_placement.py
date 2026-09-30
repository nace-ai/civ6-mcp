"""Districts and wonders with a placement are production candidates."""

import asyncio

import drex_fixtures as fx
from drex_fakes import FakeGame

from civ_mcp.drex.candidates import DecisionCategory
from civ_mcp.drex.enumerate import production_candidates, shortlist
from civ_mcp.drex.executor import Executor, OutcomeStatus
from civ_mcp.drex.live import LiveObserver
from civ_mcp.drex.observation import DecisionMemory, DecisionSpec
from civ_mcp.drex.points import build_decision_point
from civ_mcp.lua.batch import ERROR_MARK, SECTION_MARK
from civ_mcp.lua.drex_queries import build_placement_batch, parse_placement_batch


async def _no_sleep(_):
    return None


def test_placement_batch_has_one_section_per_item_and_one_sentinel():
    lua = build_placement_batch(
        fx.CAPITAL_ID, ["DISTRICT_CAMPUS"], ["BUILDING_PYRAMIDS"]
    )
    assert lua.count("---END---") == 1
    assert f"{SECTION_MARK}district:DISTRICT_CAMPUS" in lua
    assert f"{SECTION_MARK}wonder:BUILDING_PYRAMIDS" in lua


def test_parse_placement_batch_ranks_by_score_and_keeps_errors():
    lines = [
        f"{SECTION_MARK}district:DISTRICT_CAMPUS",
        "DPLOT|12,13|1|0|0|0|0|1|Grass",
        "DPLOT|11,12|3|0|0|0|0|3|Plains Hills",
        f"{SECTION_MARK}district:DISTRICT_HOLY_SITE",
        "ERR:NO_VALID_PLOT|No valid tile for DISTRICT_HOLY_SITE",
        f"{SECTION_MARK}wonder:BUILDING_PYRAMIDS",
        "WPLOT|10,13|TERRAIN_DESERT|none|false|false|none|none|2",
        f"{SECTION_MARK}wonder:BUILDING_STONEHENGE",
        f"{ERROR_MARK}wonder:BUILDING_STONEHENGE|attempt to index nil",
    ]
    placements, errors = parse_placement_batch(lines)
    assert [(p.x, p.y) for p in placements["DISTRICT_CAMPUS"]] == [(11, 12), (12, 13)]
    assert placements["DISTRICT_CAMPUS"][0].score == 3
    assert "science:3" in placements["DISTRICT_CAMPUS"][0].note
    assert placements["BUILDING_PYRAMIDS"][0].score == -2  # negated displacement
    assert "No valid tile" in errors["DISTRICT_HOLY_SITE"]
    assert "BUILDING_STONEHENGE" in errors


def test_districts_and_wonders_become_candidates_with_a_tile():
    cands, excl = production_candidates(
        fx.capital(), fx.production_options(), fx.WONDERS, placements=fx.placements()
    )
    holy = [c for c in cands if c.params.item_name == "DISTRICT_HOLY_SITE"]
    assert len(holy) == 3 and all(c.params.target_x is not None for c in holy)
    assert holy[0].facts["score"] >= holy[-1].facts["score"]
    pyramids = [c for c in cands if c.params.item_name == "BUILDING_PYRAMIDS"]
    assert len(pyramids) == 2  # only two tiles returned
    assert not any("requires placement" in e.reason for e in excl)


def test_without_placements_behaviour_is_unchanged():
    cands, excl = production_candidates(
        fx.capital(), fx.production_options(), fx.WONDERS
    )
    assert not any(c.params.item_name == "DISTRICT_HOLY_SITE" for c in cands)
    assert any("requires placement" in e.reason for e in excl)


def test_advisor_error_excludes_the_item_with_its_reason():
    cands, excl = production_candidates(
        fx.capital(),
        fx.production_options(),
        fx.WONDERS,
        placements={},
        placement_errors={"DISTRICT_HOLY_SITE": "NO_VALID_PLOT|No valid tile"},
    )
    assert not any(c.params.item_name == "DISTRICT_HOLY_SITE" for c in cands)
    assert any(
        e.option == "DISTRICT_HOLY_SITE" and "No valid tile" in e.reason for e in excl
    )
    assert any(
        e.option == "BUILDING_PYRAMIDS" and "no valid tile" in e.reason for e in excl
    )


def test_placement_shortlist_drops_worst_tiles_first():
    cands, _ = production_candidates(
        fx.capital(), fx.production_options(), fx.WONDERS, placements=fx.placements()
    )
    kept, cut = shortlist(cands, limit=len(cands) - 2)
    cut_ids = {e.option for e in cut}
    placed = [c for c in cands if "score" in c.facts]
    worst = min(placed, key=lambda c: c.facts["score"])
    assert worst.candidate_id in cut_ids
    assert all("score" in c.facts for c in cands if c.candidate_id in cut_ids)
    assert len(kept) == len(cands) - 2


def test_live_inputs_read_placements_in_one_round_trip():
    game = FakeGame()
    game.placements = {fx.CAPITAL_ID: fx.placements()}
    obs = LiveObserver(game)
    core = asyncio.run(obs.core())
    rt = game.conn.roundtrips
    spec = DecisionSpec(DecisionCategory.PRODUCTION, f"city:{fx.CAPITAL_ID}")
    inputs = asyncio.run(obs.inputs(spec, core))
    assert inputs.placements and "DISTRICT_HOLY_SITE" in inputs.placements
    assert game.conn.roundtrips - rt == 2  # options + one placement batch


def test_placement_precheck_accepts_the_item_and_repairs_keep_their_tile():
    game = FakeGame()
    game.placements = {fx.CAPITAL_ID: fx.placements()}
    obs = LiveObserver(game)
    core = asyncio.run(obs.core())
    spec = DecisionSpec(DecisionCategory.PRODUCTION, f"city:{fx.CAPITAL_ID}")
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
    cand = next(
        c for c in point.candidates if c.params.item_name == "DISTRICT_HOLY_SITE"
    )
    outcome = asyncio.run(
        Executor(game, sleep=_no_sleep).execute(
            cand, point, current_version=obs.version, turn=5, inputs=inputs
        )
    )
    assert outcome.status is OutcomeStatus.CONFIRMED
    assert (
        "set_city_production",
        (
            fx.CAPITAL_ID,
            "DISTRICT",
            "DISTRICT_HOLY_SITE",
            cand.params.target_x,
            cand.params.target_y,
        ),
    ) in game.calls
    # a repair whose tile moved is still rejected
    repair = next(
        c for c in point.candidates if c.params.item_name == "DISTRICT_ENCAMPMENT"
    )
    assert repair.params.target_x is not None
    for o in game.production[fx.CAPITAL_ID]:
        if o.item_name == "DISTRICT_ENCAMPMENT":
            o.repair_x, o.repair_y = 99, 99
    game.cities[fx.CAPITAL_ID].currently_building = "nothing"
    out = asyncio.run(
        Executor(game, sleep=_no_sleep).execute(
            repair, point, current_version="stale", turn=5, inputs=None
        )
    )
    assert out.status is OutcomeStatus.REJECTED


# ------------------------------------------------------- review fixes
def test_wonder_tiles_rank_lowest_displacement_first_and_name_the_resource():
    lines = [
        f"{SECTION_MARK}wonder:BUILDING_PYRAMIDS",
        "WPLOT|10,13|TERRAIN_DESERT|FEATURE_NONE|false|false|RESOURCE_IRON|IMPROVEMENT_MINE|15",
        "WPLOT|11,13|TERRAIN_DESERT_HILLS|none|true|false|none|none|0",
    ]
    placements, _ = parse_placement_batch(lines)
    tiles = placements["BUILDING_PYRAMIDS"]
    assert [(t.x, t.y) for t in tiles] == [
        (11, 13),
        (10, 13),
    ]  # least destructive first
    assert tiles[0].score > tiles[1].score  # one scale for shortlist: higher = better
    assert (
        "Iron" in tiles[1].note and "Mine" in tiles[1].note and "river" in tiles[0].note
    )
    assert "TERRAIN_" not in tiles[0].note


def test_batch_leaves_no_bare_semicolons_after_stripping_sentinels():
    from civ_mcp.lua.batch import build_batch

    lua = build_batch(
        [("a", 'if x then print("ERR:X"); print("---END---"); return end')]
    )
    assert "; ;" not in lua and ";;" not in lua
