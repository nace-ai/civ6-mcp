# civ-drex Phase 5: Treasury — unit upgrades and gold purchases — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Gold stops piling up unused: a unit that can be upgraded gets "upgrade" among its orders, and once per turn the empire decides whether to buy something affordable in one of its cities or save the gold. Both are Drex decisions; nothing here is forced by the engine, so nothing here can stop the run.

**Architecture:** Upgrades extend the existing unit decision: `UnitInfo.can_upgrade / upgrade_target / upgrade_cost` (already read by the core units query through the engine's `CanStartCommand(UPGRADE)`) become one `UPGRADE_UNIT` candidate when the treasury covers the cost, dispatched through the existing `upgrade_unit`. Purchases are one new empire-wide category `PURCHASE`, scheduled once per turn after production when the treasury holds at least `PURCHASE_MIN_GOLD`: inputs are every city's production list (the existing `list_city_production`, whose options carry `gold_cost`), candidates are every affordable unit or building in every city plus one "save the gold" option (no dispatch), dispatched through the existing `purchase_item(..., "YIELD_GOLD")`. Dispatch, precheck, verify, refresh and camera follow the existing patterns.

**Tech Stack:** Python 3.12, asyncio, pytest (`uv run pytest tests -q`), ruff via `uvx ruff`. No new Lua.

**Spec:** `docs/superpowers/specs/2026-09-30-civ-drex-never-stop-design.md` — Section 8 phase 5 ("gold purchases, unit upgrades ... same decision-kind pattern").

## Global Constraints

- Drex chooses among two or more candidates; the controller never buys or upgrades on its own. When only "save the gold" is affordable the decision is forced without a Drex call.
- Bounds (documented controller rules, like the 3-decisions-per-unit budget): one purchase decision per turn for the whole empire; the purchase inputs are read only when `overview.gold >= PURCHASE_MIN_GOLD` (60, below the cheapest unit purchase at standard speed) — a read-saving gate, not a choice.
- Speed: purchase inputs cost one round trip per city (≈ 60 ms each); an upgrade adds no read (its facts come from the core units part).
- Do not edit `src/civ_mcp/lua/*` other than `drex_queries.py`, nor `server.py`, `game_launcher.py`, `end_turn.py`. Stage only files you changed.
- Suite is 575 passing at the start; full suite before each commit; `uvx ruff format` / `uvx ruff check` on touched files. Commits end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.

## Review Focus

1. **Upgrade of a unit that already acted.** `can_upgrade` is read at observation time; if the unit moved or attacked since, the engine refuses. The precheck must reuse the unit precheck (same tile, moves left) and the dispatch's `ERR:CANNOT_UPGRADE` must become `rejected`, never `unknown`. Pinned in Task 1 (`test_upgrade_refused_by_engine_is_rejected`).
2. **Treasury changes between observation and dispatch.** Two purchases cannot happen in one decision, but an upgrade earlier in the turn may have spent the gold: the purchase precheck compares the cost with the gold in the freshest observation the executor knows, and the dispatch's own balance check turns a shortfall into `rejected`. Pinned in Task 2 (`test_purchase_precheck_uses_fresh_treasury`).
3. **Option explosion.** Many cities × many purchasable items can exceed 255; the shortlist must drop the most expensive purchases first and never drop "save the gold". Pinned in Task 2 (`test_purchase_shortlist_drops_most_expensive_first_and_keeps_save`).
4. **Unit purchase blocked by a stacked unit.** `build_purchase_item` bails with `ERR:STACKING_CONFLICT` when a same-class unit stands on the city tile; the outcome must be `rejected` with the candidate excluded for the rest of the turn, not a retry loop. Pinned in Task 2 (`test_stacking_conflict_is_rejected_and_excluded`).
5. **No purchase decision spam.** With nothing affordable the decision is forced "save" without a Drex call, and the category is resolved for the turn either way. Pinned in Task 2 (`test_nothing_affordable_is_forced_save_and_resolves_the_turn`).

---

## File Structure

| File | Responsibility |
|---|---|
| `src/civ_mcp/drex/candidates.py` (modify) | `ActionKind.UPGRADE_UNIT`, `PURCHASE_ITEM`, `SAVE_GOLD`; `DecisionCategory.PURCHASE`; `UpgradeParams`, `PurchaseParams`, `SaveGoldParams`; ids. |
| `src/civ_mcp/drex/enumerate.py` (modify) | `unit_candidates(..., gold=None)` adds the upgrade; `purchase_candidates(cities_options, gold)`; purchase-aware `shortlist`. |
| `src/civ_mcp/drex/observation.py`, `live.py`, `points.py` (modify) | `DecisionInputs.purchase_options`; inputs read; question; wiring. |
| `src/civ_mcp/drex/executor.py`, `refresh.py`, `spectate.py`, `scheduler.py` (modify) | Dispatch/precheck/verify; refresh rules; camera; `PURCHASE_MIN_GOLD`, order, key, note. |
| Tests: `tests/drex/test_drex_upgrade.py`, `tests/drex/test_drex_purchase.py` (create); `drex_fixtures.py`, `drex_fakes.py`, `test_drex_refresh.py`, `test_drex_executor.py` (modify). |
| `docs/drex.md` (modify) | Rows, order, limitations. |

Task order: 1 → 2 → 3.

---

### Task 1: Unit upgrade as a unit order

**Files:**
- Modify: `src/civ_mcp/drex/candidates.py`, `enumerate.py`, `points.py`, `executor.py`, `refresh.py`, `spectate.py`
- Modify: `tests/drex/drex_fakes.py`, `tests/drex/test_drex_refresh.py`, `tests/drex/test_drex_executor.py`
- Test: `tests/drex/test_drex_upgrade.py` (create)

**Interfaces:**
- `candidates.py`: `ActionKind.UPGRADE_UNIT = "upgrade_unit"`; `@dataclass(frozen=True) class UpgradeParams: unit: UnitRef; target_type: str; cost: int`; id `upgrade:<unit_id>`.
- `enumerate.py`: `unit_candidates(space, unit, *, me, gold: float | None = None)` — when `unit.can_upgrade and unit.upgrade_target` and the unit is on the tile the units query validated (`same_tile`): affordable (`gold is None or gold >= unit.upgrade_cost`) → candidate `UPGRADE_UNIT` labelled `Upgrade to <Target> (<cost> gold)` with facts `{"cost", "treasury", "from"}`; unaffordable → `Exclusion(f"upgrade to {target}", f"costs {cost} gold, treasury {int(gold)}")`.
- `points.py` passes `gold=core.overview.gold`.
- `executor.py`: dispatch `("upgrade_unit", (p.unit.unit_id,))`; precheck `_unit_space` (unit present, same tile, moves left); verify `raw.startswith("UPGRADED|")` → confirmed, game error → `rejected` (`upgrade_refused`), else readback: `get_unit_state(...).moves_remaining <= 0` → confirmed `moves_spent`, else pending.
- `refresh.py`: `UPGRADE_UNIT: {"units", "overview"}`; `spectate.py`: unit tile.

- [ ] **Step 1: Fake**

`tests/drex/drex_fakes.py`, after `skip_unit`:

```python
    async def upgrade_unit(self, unit_id):
        fail = self._record("upgrade_unit", unit_id)
        u = self.units.get(unit_id)
        if u is None:
            return "Error: UNIT_NOT_FOUND"
        if not u.can_upgrade or not u.upgrade_target:
            return f"Error: CANNOT_UPGRADE|{u.unit_type} | cost:0g have:{int(self.gold)}g"
        if self.gold < u.upgrade_cost:
            return (
                f"Error: CANNOT_UPGRADE|{u.unit_type} -> {u.upgrade_target}"
                f" | cost:{u.upgrade_cost}g have:{int(self.gold)}g"
            )
        old, u.unit_type = u.unit_type, u.upgrade_target
        self.gold -= u.upgrade_cost
        u.can_upgrade, u.upgrade_target, u.upgrade_cost = False, "", 0
        u.moves_remaining = 0
        space = self._space_by_index(u.unit_index)
        if space is not None:
            space.moves_remaining = 0
        self._after(fail)
        return f"UPGRADED|{old} -> {u.unit_type}"
```

If `FakeGame` has no `_space_by_index`, use `self.spaces.get(u.unit_index)`. Check that `get_game_overview` reports `self.gold` (it does: `gold=self.gold`); if not, make it so.

- [ ] **Step 2: Failing tests**

Create `tests/drex/test_drex_upgrade.py`:

```python
"""Unit upgrade as one of a unit's orders (Drex decides, treasury permitting)."""

import asyncio

import drex_fixtures as fx
from drex_fakes import FakeGame

from civ_mcp.drex.candidates import ActionKind, DecisionCategory
from civ_mcp.drex.enumerate import unit_candidates
from civ_mcp.drex.executor import Executor, OutcomeStatus
from civ_mcp.drex.live import LiveObserver
from civ_mcp.drex.observation import DecisionMemory, DecisionSpec
from civ_mcp.drex.points import build_decision_point
from civ_mcp.drex.scheduler import Scheduler, TurnLedger


async def _no_sleep(_):
    return None


def _upgradeable_warrior():
    w = fx.warrior()
    w.can_upgrade, w.upgrade_target, w.upgrade_cost = True, "UNIT_SWORDSMAN", 90
    return w


def _point(game):
    obs = LiveObserver(game)
    core = asyncio.run(obs.core())
    spec = DecisionSpec(DecisionCategory.UNIT, f"unit:{fx.WARRIOR_ID}")
    inputs = asyncio.run(obs.inputs(spec, core))
    point, excluded = build_decision_point(
        spec, core, inputs, DecisionMemory(),
        objective="o", decision_id="T5#1", max_options=255,
    )
    return obs, core, point, inputs, excluded


def test_upgrade_offered_when_affordable():
    cands, excl = unit_candidates(fx.warrior_space(), _upgradeable_warrior(), me=0, gold=120)
    up = [c for c in cands if c.kind is ActionKind.UPGRADE_UNIT]
    assert len(up) == 1
    assert up[0].candidate_id == f"upgrade:{fx.WARRIOR_ID}"
    assert "Swordsman" in up[0].label and "90" in up[0].label
    assert up[0].facts["cost"] == 90 and up[0].facts["treasury"] == 120


def test_upgrade_excluded_when_unaffordable_with_reason():
    cands, excl = unit_candidates(fx.warrior_space(), _upgradeable_warrior(), me=0, gold=40)
    assert not [c for c in cands if c.kind is ActionKind.UPGRADE_UNIT]
    assert any("costs 90 gold" in e.reason for e in excl)


def test_no_upgrade_candidate_for_a_unit_without_one():
    cands, _ = unit_candidates(fx.warrior_space(), fx.warrior(), me=0, gold=999)
    assert not [c for c in cands if c.kind is ActionKind.UPGRADE_UNIT]


def test_upgrade_dispatches_confirms_and_spends_gold():
    game = FakeGame()
    game.units[fx.WARRIOR_ID] = _upgradeable_warrior()
    game.gold = 200
    obs, core, point, inputs, _ = _point(game)
    cand = next(c for c in point.candidates if c.kind is ActionKind.UPGRADE_UNIT)
    outcome = asyncio.run(
        Executor(game, sleep=_no_sleep).execute(
            cand, point, current_version=obs.version, turn=5, inputs=inputs
        )
    )
    assert outcome.status is OutcomeStatus.CONFIRMED
    assert ("upgrade_unit", (fx.WARRIOR_ID,)) in game.calls
    assert game.gold == 110 and game.units[fx.WARRIOR_ID].unit_type == "UNIT_SWORDSMAN"
    s = Scheduler()
    ledger = TurnLedger(turn=5)
    step = DecisionSpec(DecisionCategory.UNIT, f"unit:{fx.WARRIOR_ID}")
    s.note(ledger, step, cand.kind, outcome, cand.candidate_id)
    assert not s._open(ledger, step)  # an upgrade ends the unit's turn


def test_upgrade_refused_by_engine_is_rejected():
    game = FakeGame()
    game.units[fx.WARRIOR_ID] = _upgradeable_warrior()
    game.gold = 200
    obs, core, point, inputs, _ = _point(game)
    cand = next(c for c in point.candidates if c.kind is ActionKind.UPGRADE_UNIT)
    game.gold = 10  # spent since the observation: the engine refuses
    outcome = asyncio.run(
        Executor(game, sleep=_no_sleep).execute(
            cand, point, current_version=obs.version, turn=5, inputs=inputs
        )
    )
    assert outcome.status is OutcomeStatus.REJECTED
    assert "upgrade_refused" in outcome.reason
```

`tests/drex/test_drex_refresh.py`:

```python
def test_upgrade_refreshes_units_and_overview():
    assert refresh_parts(ActionKind.UPGRADE_UNIT) == frozenset(
        {"units", "overview", "blockers", "popup"}
    )
```

`tests/drex/test_drex_executor.py`: add before the parametrize decorators

```python
_UPGRADEABLE = fx.warrior()
_UPGRADEABLE.can_upgrade, _UPGRADEABLE.upgrade_target, _UPGRADEABLE.upgrade_cost = (
    True, "UNIT_SWORDSMAN", 90,
)
DISPATCH_CASES.append(
    (
        _cand(
            unit_candidates(fx.warrior_space(), _UPGRADEABLE, me=0, gold=500)[0],
            ActionKind.UPGRADE_UNIT,
            lambda c: True,
        ),
        ("upgrade_unit", (fx.WARRIOR_ID,)),
    )
)
```

and in the parametrized executor test's setup: `game.units[fx.WARRIOR_ID] = copy.deepcopy(_UPGRADEABLE)` guarded by `if cand.kind is ActionKind.UPGRADE_UNIT:` plus `game.gold = 500`.

- [ ] **Step 3: Run to verify failure** — `uv run pytest tests/drex/test_drex_upgrade.py tests/drex/test_drex_refresh.py -q` → `AttributeError: UPGRADE_UNIT` / unexpected keyword `gold`.

- [ ] **Step 4: Implement**

`candidates.py`: kind, params (`UpgradeParams(unit: UnitRef, target_type: str, cost: int)`), union, `PARAMS_FOR_KIND`, id `case ActionKind.UPGRADE_UNIT, UpgradeParams(unit=u): return f"upgrade:{u.unit_id}"`.

`enumerate.py`, in `unit_candidates` after the improvements loop:

```python
    if unit.can_upgrade and unit.upgrade_target:
        target = unit.upgrade_target
        if not same_tile:
            excluded.append(Exclusion(f"upgrade to {target}", "unit moved since upgrade was validated"))
        elif gold is not None and gold < unit.upgrade_cost:
            excluded.append(
                Exclusion(f"upgrade to {target}", f"costs {unit.upgrade_cost} gold, treasury {int(gold)}")
            )
        else:
            out.append(
                Candidate.create(
                    ActionKind.UPGRADE_UNIT,
                    UpgradeParams(unit=ref, target_type=target, cost=unit.upgrade_cost),
                    label=f"Upgrade to {pretty(target.replace('UNIT_', ''))} ({unit.upgrade_cost} gold)",
                    facts={"cost": unit.upgrade_cost, "treasury": None if gold is None else int(gold), "from": pretty(unit.unit_type.replace('UNIT_', ''))},
                )
            )
```

`points.py`: `unit_candidates(inputs.action_space, inputs.unit, me=core.local_player_id, gold=core.overview.gold)`.

`executor.py`: dispatch `case ActionKind.UPGRADE_UNIT, UpgradeParams(): return DispatchCall("upgrade_unit", (p.unit.unit_id,))`; precheck: `space, why = await self._unit_space(p.unit); return _no(why) if space is None else _ok()`; verify:

```python
            case ActionKind.UPGRADE_UNIT:
                if raw.startswith("UPGRADED|"):
                    return confirmed("upgrade_confirmed_from_dispatch")
                if _game_error(raw):
                    return self._unconfirmed(raw, "upgrade_refused")

                async def spent():
                    state = await gs.get_unit_state(p.unit.unit_index)
                    return state is not None and state.moves_remaining <= 0

                if await self._poll(spent):
                    return confirmed("moves_spent")
                return ActionOutcome(OutcomeStatus.PENDING, "order_accepted_effect_later", True)
```

Check `_unconfirmed` yields `REJECTED` for a game error (as `order_refused` does for fortify); if it yields `UNKNOWN`, use the same helper the fortify case uses for refusals.

`refresh.py`: `ActionKind.UPGRADE_UNIT: frozenset({"units", "overview"})`. `spectate.py`: `case ActionKind.UPGRADE_UNIT, UpgradeParams(): return p.unit.x, p.unit.y, candidate.label`.

- [ ] **Step 5: Tests green, suite green** — `uv run pytest tests -q`.
- [ ] **Step 6: Lint + commit** — message `Unit upgrades are a unit order Drex can choose when the treasury covers the cost`.

---

### Task 2: Gold purchases, once per turn

**Files:**
- Modify: `candidates.py`, `enumerate.py`, `observation.py`, `live.py`, `points.py`, `executor.py`, `refresh.py`, `spectate.py`, `scheduler.py`
- Modify: `tests/drex/drex_fakes.py`, `tests/drex/drex_fixtures.py`, `test_drex_refresh.py`, `test_drex_executor.py`
- Test: `tests/drex/test_drex_purchase.py` (create)

**Interfaces:**
- `candidates.py`: `DecisionCategory.PURCHASE = "purchase"`; `ActionKind.PURCHASE_ITEM = "purchase_item"`, `ActionKind.SAVE_GOLD = "save_gold"` (no dispatch); `PurchaseParams(city_id: int, city_name: str, item_type: str, item_name: str, gold_cost: int)`; `SaveGoldParams()`; ids `purchase:<city_id>:<item_type>:<item_name>`, `save_gold`.
- `enumerate.py`: `purchase_candidates(cities: list[lq.CityInfo], options_by_city: dict[int, list[lq.ProductionOption]], gold: float, wonder_types: set[str]) -> tuple[list[Candidate], list[Exclusion]]` — affordable (`0 <= gold_cost <= gold`) UNIT/BUILDING options that are not repairs and not wonders, one candidate each with facts `{"city", "gold_cost", "treasury", "production_turns"}`; unaffordable → `Exclusion(f"{city}: {item}", f"costs {gold_cost} gold, treasury {int(gold)}")`; always one `SAVE_GOLD` candidate labelled `Save the gold (buy nothing this turn)` with facts `{"treasury"}`. `shortlist`: purchase candidates give way after placements, most expensive first; `SAVE_GOLD` is never dropped.
- `observation.py`: `DecisionInputs.purchase_options: dict[int, list[lq.ProductionOption]] | None`.
- `live.py`: `case DecisionCategory.PURCHASE:` read `list_city_production` for every city in `core.cities` → `DecisionInputs(purchase_options={...}, wonder_types=sorted(await self._wonder_types(core.game_identity)))`.
- `scheduler.py`: `PURCHASE_MIN_GOLD = 60`; `TurnLedger.purchase_offered: bool`; in `_next_proactive` after the per-city city-attack loop and before Great People: `if not ledger.purchase_offered and core.overview.gold >= PURCHASE_MIN_GOLD: spec = DecisionSpec(DecisionCategory.PURCHASE, "empire"); if self._open(ledger, spec): return spec`; `note` sets `purchase_offered = True` for the category (like Great People) and resolves the key; `key_for` adds `PURCHASE` to the category-keyed set; `SCHEDULER_ORDER` line `"purchase: once per turn when the treasury holds PURCHASE_MIN_GOLD or more (buy an affordable item in a city, or save)"`.
- `executor.py`: dispatch `("purchase_item", (p.city_id, p.item_type, p.item_name, "YIELD_GOLD"))`, `SAVE_GOLD` → `NO_DISPATCH`; precheck: known `purchase_options[city_id]` else `list_city_production(city_id)` must still list the item with `gold_cost >= 0`, and `gold_cost <= self._known_gold()` where the executor keeps the gold of the latest inputs' observation (pass `core.overview.gold` via `DecisionInputs.treasury: float | None`, set by `live.inputs` for PURCHASE from `core.overview.gold`); verify `raw.startswith("PURCHASED|")` → confirmed; game error → `rejected` (`purchase_refused`), else readback: overview gold decreased → confirmed `gold_spent`, else unconfirmed.
- `refresh.py`: `PURCHASE_ITEM: {"overview", "cities", "units"}`, `SAVE_GOLD: frozenset()`. `spectate.py`: `PURCHASE_ITEM` → city tile.

- [ ] **Step 1: Fixtures and fake**

`drex_fixtures.py`: give `production_options()` gold costs if it does not already: set `gold_cost` on the unit option (e.g. `UNIT_WARRIOR` → 160) and a building (`BUILDING_MONUMENT` → 240), `-1` on districts/wonders. Check the existing fixture first and add a helper `purchasable_options()` if changing the shared one breaks production tests.

`drex_fakes.py`:

```python
    async def purchase_item(self, city_id, item_type, item_name, yield_type="YIELD_GOLD"):
        fail = self._record("purchase_item", city_id, item_type, item_name, yield_type)
        opt = next(
            (o for o in self.production.get(city_id, []) if o.item_name == item_name and o.gold_cost >= 0),
            None,
        )
        if opt is None:
            return f"Error: CANNOT_PURCHASE|{item_name} not purchasable"
        if item_name in self.stacked_units_on_tile:
            return f"Error: STACKING_CONFLICT|Cannot purchase {item_name}"
        if self.gold < opt.gold_cost:
            return f"Error: CANNOT_PURCHASE|costs {opt.gold_cost}g but you only have {int(self.gold)}g"
        had, self.gold = self.gold, self.gold - opt.gold_cost
        self._after(fail)
        return f"PURCHASED|{item_name}|cost={opt.gold_cost}g (had {int(had)}g)"
```

with `self.stacked_units_on_tile: set[str] = set()` in `__init__`.

- [ ] **Step 2: Failing tests** — `tests/drex/test_drex_purchase.py` covering: candidates one per affordable item plus save (facts, ids); unaffordable excluded with reason; wonders and repairs never offered; scheduler offers PURCHASE once per turn only when gold ≥ 60 and after PRODUCTION, before UNIT; `test_nothing_affordable_is_forced_save_and_resolves_the_turn` (gold 70, cheapest 160 → single SAVE candidate, `forced_single_candidate`, no dispatch, second `next` is not PURCHASE); dispatch confirms and spends gold; `test_purchase_precheck_uses_fresh_treasury` (inputs built with gold 300, executor called with `inputs=None` and game.gold=50 → rejected, no call); `test_stacking_conflict_is_rejected_and_excluded` (fake `stacked_units_on_tile={"UNIT_WARRIOR"}` → REJECTED, then `build_decision_point(..., failed={cand.candidate_id})` excludes it); `test_purchase_shortlist_drops_most_expensive_first_and_keeps_save` (build 300 purchase candidates across fake cities, `shortlist(cands, 255)` keeps SAVE and the cheapest); refresh rule; DISPATCH_CASES entry for `PURCHASE_ITEM` and `SAVE_GOLD` in `NO_DISPATCH_KINDS`.

- [ ] **Step 3: Verify RED**, **Step 4: Implement** per Interfaces, **Step 5: suite green**, **Step 6: lint + commit** — message `Gold purchases are a once-per-turn Drex decision (buy an affordable item or save)`.

---

### Task 3: Docs and live run

- [ ] `docs/drex.md`: scheduler order item for purchases (6c) and the upgrade order in the unit row; two "Supported decisions" rows; Limitations bullet drops "purchases, unit upgrades"; status table row for Phase 5.
- [ ] Suite green; commit `Document Phase 5: upgrades and purchases`.
- [ ] Restart the live run on the new code (`pkill -TERM -f "[b]in/civ-drex play"`, then `nohup uv run civ-drex play --turns 500 > logs/drex/play-phase5.out 2>&1 &`) and watch the log for the first `upgrade_unit` / `purchase_item` decisions and their outcomes; record the first live outcome of each in the ledger.
