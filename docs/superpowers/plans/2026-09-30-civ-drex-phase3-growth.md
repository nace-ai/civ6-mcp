# civ-drex Phase 3: Growth — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cities can build districts and wonders (Drex picks the item and the tile), and idle traders start trade routes (Drex picks the destination). Without these the empire never builds a Campus and every trader stands still.

**Architecture:** Both are extensions of existing decision kinds, not new categories. Production candidates gain district and wonder items with a placement each, fed by one batched advisor read per production decision (the existing per-district advisor Lua run through `build_batch`, bypassing the MCP advisor budget that is meant for LLM agents). Unit candidates gain a `MAKE_TRADE_ROUTE` kind for traders with route capacity, fed by one destinations read per trader decision. Dispatch, precheck and verify follow the existing `SET_PRODUCTION` and unit patterns.

**Tech Stack:** Python 3.12, asyncio, pytest (`uv run pytest tests -q`), ruff via `uvx ruff`. Lua through FireTuner, InGame context for advisors and trade.

**Spec:** `docs/superpowers/specs/2026-09-30-civ-drex-never-stop-design.md` — Section 4 rows "District placement", "Wonder placement", "Trade route"; Section 8 phase 3.

## Global Constraints

- Drex chooses among two or more candidates; a placement is part of the candidate, never a controller choice (spec principle 1). The controller may cap how many tiles per item it offers (top 3 by the advisor's ranking), which is a shortlist rule like the existing nearest-first rule, not a decision.
- Speed budget: at most 2 round trips to execute a decision and 1 for the refresh; the placement advisor read is part of the decision's inputs (1 batched round trip per production decision that has districts or wonders to offer; 0 otherwise).
- Do not edit `src/civ_mcp/lua/{__init__,cities,overview,tech,economy,governance,map}.py`, `src/civ_mcp/server.py`, `src/civ_mcp/game_launcher.py` (other agents' uncommitted work); new Lua glue goes in `src/civ_mcp/lua/drex_queries.py`. Stage only files you changed.
- The game stays closed: no live checks; `civ-drex probe --kind placements|trade` is added for the next live session.
- Suite is 510 passing at the start; run the full suite before each commit; lint touched files with `uvx ruff format` / `uvx ruff check` (pre-existing allow-list as in Phase 2). Commits end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.

## Review Focus

1. **Advisor says "impossible".** `build_district_advisor_query` bails with `ERR:` when a district cannot be placed (no valid tile, already built); the batched read must turn that into "no candidates for this item" and exclude it with the reason, never into a candidate with a bogus tile. Pinned in Task 1 (`test_advisor_error_excludes_the_item_with_its_reason`).
2. **Placement precheck vs repair coordinates.** Today `same_target` compares the candidate's target with the option's repair coordinates; a placement candidate has a target but the option has none. The precheck must accept a placement whose item is still offered, and still reject a repair whose coordinates changed. Pinned in Task 1 (`test_placement_precheck_accepts_the_item_and_repairs_keep_their_tile`).
3. **Option-count explosion.** Many buildable districts × 3 tiles plus wonders × 3 can exceed the 255 option limit on a large city; the shortlist must drop the lowest-ranked placements first and never drop non-placement items. Pinned in Task 1 (`test_placement_shortlist_drops_worst_tiles_first`).
4. **Trader without capacity.** A trader with no free route slot must get no `MAKE_TRADE_ROUTE` candidates (the engine would refuse) but still its normal orders. Pinned in Task 2 (`test_no_trade_route_candidates_without_capacity`).
5. **Trader on a route.** A trader already travelling has moves in some turns; it must not be offered a new route (the destinations query returns none, but the candidate builder must also skip `on_route` traders). Pinned in Task 2 (`test_trader_on_route_is_not_offered_a_new_route`).

---

## File Structure

| File | Responsibility |
|---|---|
| `src/civ_mcp/lua/drex_queries.py` (modify) | `build_placement_batch`, `parse_placement_batch` over the existing advisor builders/parsers. |
| `src/civ_mcp/game_state.py` (modify) | `get_placement_options(city_id, districts, wonders)`; no advisor budget. |
| `src/civ_mcp/drex/observation.py` (modify) | `DecisionInputs.placements`, `trade_destinations`, `trade_status`. |
| `src/civ_mcp/drex/live.py` (modify) | PRODUCTION inputs gather districts/wonders and read placements; UNIT inputs for traders read capacity and destinations. |
| `src/civ_mcp/drex/enumerate.py` (modify) | `production_candidates(..., placements)`, `trade_route_candidates`, placement-aware `shortlist`. |
| `src/civ_mcp/drex/candidates.py` (modify) | `ActionKind.MAKE_TRADE_ROUTE`, `TradeRouteParams`, id `trade:<unit>:<x>,<y>`. |
| `src/civ_mcp/drex/executor.py` (modify) | Placement-aware production precheck; trade dispatch/precheck/verify. |
| `src/civ_mcp/drex/refresh.py`, `points.py`, `spectate.py` (modify) | Rules, wiring, camera focus for trade routes. |
| `src/civ_mcp/drex/cli.py` (modify) | `probe --kind placements|trade`. |
| Tests: `tests/drex/test_drex_placement.py`, `tests/drex/test_drex_trade.py` (create); `drex_fixtures.py`, `drex_fakes.py`, `test_drex_executor.py`, `test_drex_refresh.py` (modify). |
| `docs/drex.md` (modify) | Supported decisions rows, Limitations. |

Task order: 1 → 2 → 3.

---

### Task 1: District and wonder placement in production decisions

**Files:**
- Modify: `src/civ_mcp/lua/drex_queries.py`, `src/civ_mcp/game_state.py`, `src/civ_mcp/drex/observation.py`, `src/civ_mcp/drex/live.py`, `src/civ_mcp/drex/enumerate.py`, `src/civ_mcp/drex/points.py`, `src/civ_mcp/drex/executor.py`, `src/civ_mcp/drex/cli.py`
- Modify: `tests/drex/drex_fixtures.py`, `tests/drex/drex_fakes.py`, `tests/drex/test_drex_executor.py`, `tests/drex/test_drex_enumerate.py`
- Test: `tests/drex/test_drex_placement.py` (create)

**Interfaces:**
- `drex_queries.py`:

```python
@dataclass
class Placement:
    x: int; y: int; score: int; note: str   # district: total_adjacency + terrain; wonder: displacement_score + terrain/feature

def build_placement_batch(city_id: int, districts: list[str], wonders: list[str]) -> str
    # sections named f"district:{type}" / f"wonder:{name}" over lq.build_district_advisor_query / lq.build_wonder_advisor_query via civ_mcp.lua.batch.build_batch
def parse_placement_batch(lines: list[str]) -> tuple[dict[str, list[Placement]], dict[str, str]]
    # returns (placements by item name, errors by item name); an ERR: line inside a section is an error for that item
```

  `GameState.get_placement_options(city_id, districts, wonders) -> tuple[dict[str, list[Placement]], dict[str, str]]` — one `execute_write`, no `_advisor_budget_check`.
- `DecisionInputs.placements: dict[str, list[Any]] | None`, `DecisionInputs.placement_errors: dict[str, str] | None`.
- `LiveObserver.inputs()` for PRODUCTION: after `list_city_production`, `districts = [o.item_name for o in options if o.category == "DISTRICT" and not o.is_repair]`, `wonders = [o.item_name for o in options if o.category == "BUILDING" and o.item_name in wonder_types and not o.is_repair]`; if either non-empty, `placements, errors = await gs.get_placement_options(city.city_id, districts, wonders)`.
- `production_candidates(city, options, wonder_types, placements=None, placement_errors=None, per_item=3)`: for a district/wonder option with placements, one candidate per top-`per_item` placement (sorted by score desc, then x, y) with `ProductionParams(target_x, target_y)`, label `f"{pretty(item)} at ({x},{y})"`, facts `{cost, turns, "tile": note, "score": score}`; with an error → `Exclusion(item, f"placement: {error}")`; with no placements and no error → `Exclusion(item, "placement: no valid tile")`.
- `shortlist(candidates, limit)`: when over the limit, drop placement candidates with the lowest `facts["score"]` first (ties: later x,y first), then fall back to the existing rule.
- Executor `SET_PRODUCTION` precheck: an option matches when category and name match and either the candidate has no target, or the option is a repair with equal coordinates, or the option is not a repair (placement chosen by us). Verify unchanged (`PRODUCING|<item>|` from dispatch, else readback).
- `probe --kind placements`: for each city, options → districts/wonders → `get_placement_options`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/drex/test_drex_placement.py
"""Districts and wonders with a placement are production candidates."""
import asyncio

import drex_fixtures as fx
from drex_fakes import FakeGame

from civ_mcp.drex.candidates import ActionKind, DecisionCategory
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
    lua = build_placement_batch(fx.CAPITAL_ID, ["DISTRICT_CAMPUS"], ["BUILDING_PYRAMIDS"])
    assert lua.count("---END---") == 1
    assert f"{SECTION_MARK}district:DISTRICT_CAMPUS" in lua
    assert f"{SECTION_MARK}wonder:BUILDING_PYRAMIDS" in lua


def test_parse_placement_batch_ranks_by_score_and_keeps_errors():
    lines = [
        f"{SECTION_MARK}district:DISTRICT_CAMPUS",
        "DPLOT|11|12|3|Plains Hills|science:3",
        "DPLOT|12|13|1|Grass|science:1",
        f"{SECTION_MARK}district:DISTRICT_HOLY_SITE",
        "ERR:NO_VALID_PLOT|No valid tile for DISTRICT_HOLY_SITE",
        f"{SECTION_MARK}wonder:BUILDING_PYRAMIDS",
        f"{ERROR_MARK}wonder:BUILDING_PYRAMIDS|attempt to index nil",
    ]
    placements, errors = parse_placement_batch(lines)
    assert [(p.x, p.y) for p in placements["DISTRICT_CAMPUS"]] == [(11, 12), (12, 13)]
    assert placements["DISTRICT_CAMPUS"][0].score == 3
    assert "DISTRICT_HOLY_SITE" in errors and "No valid tile" in errors["DISTRICT_HOLY_SITE"]
    assert "BUILDING_PYRAMIDS" in errors


def test_districts_and_wonders_become_candidates_with_a_tile():
    cands, excl = production_candidates(
        fx.capital(), fx.production_options(), fx.WONDERS, placements=fx.placements()
    )
    campus = [c for c in cands if c.params.item_name == "DISTRICT_HOLY_SITE"]
    assert len(campus) == 3 and all(c.params.target_x is not None for c in campus)
    assert campus[0].facts["score"] >= campus[-1].facts["score"]
    pyramids = [c for c in cands if c.params.item_name == "BUILDING_PYRAMIDS"]
    assert len(pyramids) == 2  # only two tiles returned
    assert not any("requires placement" in e.reason for e in excl)


def test_advisor_error_excludes_the_item_with_its_reason():
    cands, excl = production_candidates(
        fx.capital(), fx.production_options(), fx.WONDERS,
        placements={}, placement_errors={"DISTRICT_HOLY_SITE": "NO_VALID_PLOT|No valid tile"},
    )
    assert not any(c.params.item_name == "DISTRICT_HOLY_SITE" for c in cands)
    assert any(e.option == "DISTRICT_HOLY_SITE" and "No valid tile" in e.reason for e in excl)
    assert any(e.option == "BUILDING_PYRAMIDS" and "no valid tile" in e.reason for e in excl)


def test_placement_shortlist_drops_worst_tiles_first():
    cands, _ = production_candidates(
        fx.capital(), fx.production_options(), fx.WONDERS, placements=fx.placements()
    )
    kept, cut = shortlist(cands, limit=len(cands) - 2)
    cut_ids = {c.candidate_id for c in cut}
    worst = min(
        (c for c in cands if c.params.target_x is not None), key=lambda c: c.facts["score"]
    )
    assert worst.candidate_id in cut_ids
    assert all(c.params.target_x is not None for c in cands if c.candidate_id in cut_ids)


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
    point, _ = build_decision_point(spec, core, inputs, DecisionMemory(), objective="o", decision_id="T5#1", max_options=255)
    cand = next(c for c in point.candidates if c.params.item_name == "DISTRICT_HOLY_SITE")
    outcome = asyncio.run(Executor(game, sleep=_no_sleep).execute(cand, point, current_version=obs.version, turn=5, inputs=inputs))
    assert outcome.status is OutcomeStatus.CONFIRMED
    assert ("set_city_production", (fx.CAPITAL_ID, "DISTRICT", "DISTRICT_HOLY_SITE", cand.params.target_x, cand.params.target_y)) in game.calls
```

Fixture `placements()`:

```python
def placements():
    from civ_mcp.lua.drex_queries import Placement
    return {
        "DISTRICT_HOLY_SITE": [Placement(11, 12, 3, "Plains Hills; faith:3"), Placement(12, 13, 2, "Grass; faith:2"), Placement(9, 12, 1, "Grass; faith:1"), Placement(8, 11, 0, "Plains")],
        "BUILDING_PYRAMIDS": [Placement(10, 13, 2, "Desert"), Placement(11, 13, 1, "Desert Hills")],
    }
```

(`fx.production_options()` already lists `DISTRICT_HOLY_SITE` and `BUILDING_PYRAMIDS` — the Phase 1 tests exclude them.) Fake: `self.placements: dict[int, dict] = {}`; `async def get_placement_options(self, city_id, districts, wonders)`: count a round trip; return `({k: v for k, v in self.placements.get(city_id, {}).items() if k in districts or k in wonders}, {})`. `FakeGame.set_city_production` already accepts `target_x/target_y`.

Existing tests to adjust: `test_drex_enumerate.py` production tests that assert the "requires placement" exclusions — keep them by passing `placements=None` (default) which keeps today's behavior; the `test_executor_performs_the_mapped_call...` parametrized case for production stays.

- [ ] **Step 2: Run tests to verify they fail** — `uv run pytest tests/drex/test_drex_placement.py -v` → `ImportError`.

- [ ] **Step 3: Implement** per the Interfaces block. `parse_placement_batch` uses `split_batch`, then per section: an `ERR:` line → error; else `lq.parse_district_advisor_response(lines)` → `Placement(x, y, total_adjacency, f"{terrain_desc}; " + ", ".join(f"{k}:{v}" for k, v in adjacency.items()))`; wonders → `lq.parse_wonder_advisor_response(lines)` → `Placement(x, y, displacement_score, f"{terrain}/{feature}" + ("; river" if has_river else "") + ("; coast" if is_coastal else ""))` (check the exact attribute names in `lua/models.py:DistrictPlacement/WonderPlacement`).

- [ ] **Step 4: Suite, lint, commit** — `git commit -m "Districts and wonders with a placement are production candidates"`.

---

### Task 2: Trade routes for idle traders

**Files:**
- Modify: `candidates.py`, `observation.py`, `live.py`, `enumerate.py`, `executor.py`, `refresh.py`, `spectate.py`, `cli.py`
- Modify: `drex_fixtures.py`, `drex_fakes.py`, `test_drex_executor.py`, `test_drex_refresh.py`
- Test: `tests/drex/test_drex_trade.py` (create)

**Interfaces:**
- `ActionKind.MAKE_TRADE_ROUTE = "make_trade_route"`; `TradeRouteParams(unit: UnitRef, target_x: int, target_y: int, city_name: str, owner_name: str)`; id `f"trade:{unit_id}:{x},{y}"`; dispatch `make_trade_route(unit_index, x, y)`.
- `DecisionInputs.trade_destinations: list[lq.TradeDestination] | None`, `trade_status: lq.TradeRouteStatus | None`.
- `inputs()` for UNIT: when `unit.unit_type == "UNIT_TRADER"`, also `trade_status = await gs.get_trade_routes()` and, if `capacity > active_count` and the trader is not `on_route`, `trade_destinations = await gs.get_trade_destinations(unit.unit_index)` (2 extra reads for a trader decision; traders are rare).
- `trade_route_candidates(unit, space, status, destinations) -> list[Candidate]`: none when `status.capacity <= status.active_count` or the trader is `on_route`; else one per destination, label `f"Trade route to {city} ({owner})"`, facts `{"domestic": bool, "city_state": bool, "quest": bool, "trading_post": bool, "distance": hex_distance}`. `unit_candidates` gains `trade=...` optional argument appended to its result.
- Precheck: `_unit_space` identity + fresh `get_trade_destinations` contains the target (reuse inputs when current). Verify: raw `TRADE_ROUTE_STARTED|` → confirmed; `_game_error` → refused; else poll `get_trade_routes().traders` for the unit `on_route`.
- `refresh.py`: `MAKE_TRADE_ROUTE: {"units", "overview"}`. `spectate.focus_point`: target city tile. Scheduler `note()`: a trade route resolves the unit (like non-move orders; add to the "not MOVE_UNIT" branch — already the default).
- `probe --kind trade`: `get_trade_routes` plus destinations for each idle trader.

- [ ] **Step 1: Tests**

```python
# tests/drex/test_drex_trade.py
def test_trade_route_candidates_one_per_destination(): ...
def test_no_trade_route_candidates_without_capacity(): ...   # capacity == active_count → []
def test_trader_on_route_is_not_offered_a_new_route(): ...
def test_live_inputs_read_status_and_destinations_for_a_trader(): ...  # 2 extra round trips; none for a warrior
def test_make_trade_route_dispatches_and_confirms(): ...  # ("make_trade_route", (idx, x, y)) in calls; raw "TRADE_ROUTE_STARTED|to Kabul"
def test_trade_route_precheck_rejects_a_destination_that_vanished(): ...
```

Fixtures: `trader()` (`lq.UnitInfo` of type `UNIT_TRADER`, `TRADER_ID = ME*65536 + 5`, `TRADER_IDX = 5`, moves 1), `trader_space()` (`UnitActionSpace` with `is_civilian=True`, no targets, two reachable tiles), `trade_status(capacity=2, active=0, on_route=False)`, `trade_destinations()` (Roma domestic, Kabul city-state with quest). Fake: `self.trade_status`, `self.trade_destinations`, `get_trade_routes`, `get_trade_destinations(unit_index)`, `make_trade_route(unit_index, x, y)` → marks the trader `on_route`, increments `active_count`, returns `"TRADE_ROUTE_STARTED|to Kabul"`.

- [ ] **Steps 2–4:** RED, implement, GREEN, suite, commit `"Idle traders start trade routes chosen by Drex"`.

---

### Task 3: Documentation

- [ ] `docs/drex.md`: supported decisions table rows for district/wonder placement and trade routes; Limitations updated (remove "district/wonder placement" and "trade routes"; keep espionage missions, purchases, unit upgrades, multi-turn movement); status table Phase 3 row (offline, `probe --kind placements|trade` before the next live run); note that the advisor budget (`ADVISOR_BUDGET_*`) applies to the MCP tools only, the controller's batched read bypasses it.
- [ ] Commit `"Document Phase 3: placement and trade routes"`.

---

## Self-review notes

- **Spec coverage.** Section 4 rows district placement, wonder placement (Task 1), trade route (Task 2). Phase 3 of Section 8 complete; Phases 4–5 untouched.
- **Type consistency.** `Placement` (Task 1) is what fixtures, `production_candidates`, `shortlist` and the probe use. `TradeRouteParams.unit: UnitRef` matches `_unit_space`. `DecisionInputs` field names match `live.py` and the enumerate call sites.
- **Review Focus pins.** 1 → Task 1 `test_advisor_error_excludes_the_item_with_its_reason`; 2 → Task 1 `test_placement_precheck_accepts_the_item_and_repairs_keep_their_tile`; 3 → Task 1 `test_placement_shortlist_drops_worst_tiles_first`; 4 → Task 2 `test_no_trade_route_candidates_without_capacity`; 5 → Task 2 `test_trader_on_route_is_not_offered_a_new_route`.
- **Live-unverified (game closed):** the exact `DPLOT|`/wonder line shapes are parsed by the existing parsers, so only the batching glue is new; `probe --kind placements|trade` checks both before the next `play`.
