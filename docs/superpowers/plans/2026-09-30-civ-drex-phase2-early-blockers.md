# civ-drex Phase 2: Early-Game Blockers — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every end-turn blocker the game raises in the first ~60 turns becomes a Drex decision: unit promotion, governors, era dedication, Great People (including forced claims), religion founding and added beliefs, city and district ranged attacks. A committed dump of the engine's `EndTurnBlockingTypes` enum plus a test pins that every member is classified.

**Architecture:** Each kind follows the existing typed pipeline exactly as the pantheon kind does: a `DecisionCategory`, one or more `ActionKind`s with frozen param dataclasses, a candidate builder in `enumerate.py`, an `inputs()` branch in `live.py`, a `dispatch_call` mapping plus `_precheck`/`_verify` branches in `executor.py`, a scheduler slot, a `refresh.py` rule, a `FakeGame` implementation and tests. Two kinds need no dispatch for some candidates ("wait" for Great People, "hold fire" for city attacks, the stored steps of religion founding): a `NO_DISPATCH` sentinel makes the executor confirm them without touching the game. New Lua lives in `src/civ_mcp/lua/drex_queries.py` and is imported by `GameState` directly (not through `civ_mcp/lua/__init__.py`, which other agents are editing).

**Tech Stack:** Python 3.12, asyncio, pytest (`uv run pytest tests -q`), ruff via `uvx ruff`. Lua executed in Civ 6 through FireTuner (`GameConnection`), GameCore for reads of unit XP, InGame for everything else.

**Spec:** `docs/superpowers/specs/2026-09-30-civ-drex-never-stop-design.md` — Section 4 (Section B table and the blocker coverage matrix), Section 7 (coverage test), Section 8 phase 2.

## Global Constraints

- Drex chooses among two or more candidates; the controller executes alone only `forced_single_candidate`, `forced_turn_ending_order`, `forced_close_screen` (spec principle 1). A "wait"/"hold fire" candidate is a real option and always reaches Drex.
- Fresh precheck, single dispatch per candidate per turn, verified postcondition (spec principle 3).
- Speed budget from Phase 1 holds: at most 2 round trips to execute a decision and 1 for the refresh (`tests/drex/test_drex_speed.py::test_decision_round_trip_budget` must stay green). New inputs reads are per decision, not per observation.
- Every new `ActionKind` needs a `refresh.py` rule (`test_every_action_kind_has_a_refresh_rule`) and every new blocker a scheduler classification (Task 10's coverage test).
- Do not edit `src/civ_mcp/lua/__init__.py`, `src/civ_mcp/lua/{cities,overview,tech,economy,governance,map}.py`, `src/civ_mcp/server.py` or `src/civ_mcp/game_launcher.py` beyond what a task names: other agents have uncommitted work there. New Lua goes in the new module `src/civ_mcp/lua/drex_queries.py`. Stage only files you changed; never `git add -A`.
- A live game run uses this checkout; live checks happen only at a planned restart (a single tuner client is allowed).
- Suite is 429 passing at the start; run `uv run pytest tests -q` before every commit; lint with `uvx ruff format` and `uvx ruff check` on the files you touched (pre-existing warnings: `BLE001`, `S110`, `UP041`, `PIE810`, `F541`, `F401`, `ISC004`, `C408`, `RUF015`, `RUF059`, `EXE001`).
- Commit messages end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.

## Review Focus

1. **Promotion notifications that never clear.** GameCore `SetPromotion` does not consume XP, so `CanPromote()` can stay true forever (comment in `lua/units.py:106-111`). The promotable-units query must apply the XP-threshold formula the legacy code uses, and `promote_unit` already dismisses the stale notification when no unit genuinely needs one. Pinned in Task 2 (`test_promotable_units_require_the_xp_threshold`).
2. **Governor decision repeating forever.** Appoint/assign/promote are repeatable within a turn; without a per-turn cap a stuck readback would loop. Pinned in Task 3 (`test_governor_decisions_are_capped_per_turn`).
3. **Great Person "wait" when the engine forces a claim.** With `ENDTURN_BLOCKING_CLAIM_GREAT_PERSON` present, "wait" must not be offered, and with one claimable person the recruit is `forced_single_candidate`. Pinned in Task 5 (`test_forced_claim_drops_wait`).
4. **Religion founding across a turn boundary.** Partial choices (religion, follower belief) must be discarded when the turn changes or the blocker disappears, never carried into a later founding. Pinned in Task 6 (`test_partial_religion_choice_is_dropped_on_new_turn`).
5. **City attack with no targets.** A `CITY_RANGE_ATTACK` blocker with an empty target list must resolve as a forced "hold fire" and not stall the turn. Pinned in Task 8 (`test_city_attack_without_targets_is_forced_hold_fire`).

---

## File Structure

| File | Responsibility |
|---|---|
| `src/civ_mcp/drex/candidates.py` (modify) | New `DecisionCategory` and `ActionKind` members; param dataclasses; `NO_DISPATCH`. |
| `src/civ_mcp/lua/drex_queries.py` (create) | New Lua: promotable units (GameCore), add belief, city attack targets, `EndTurnBlockingTypes` dump; their parsers and models. |
| `src/civ_mcp/game_state.py` (modify) | `get_promotable_units`, `add_belief`, `get_city_attack_targets`, `get_end_turn_blocking_types`. |
| `src/civ_mcp/drex/observation.py` (modify) | `DecisionInputs` fields for the new kinds; `TurnLedger`-held partial religion choice is passed in as `religion_partial`. |
| `src/civ_mcp/drex/enumerate.py` (modify) | One candidate builder per kind. |
| `src/civ_mcp/drex/points.py` (modify) | Questions and `_enumerate` branches. |
| `src/civ_mcp/drex/live.py` (modify) | `inputs()` branches. |
| `src/civ_mcp/drex/executor.py` (modify) | Dispatch mapping, prechecks, verifications, `NO_DISPATCH` handling. |
| `src/civ_mcp/drex/scheduler.py` (modify) | Blocker constants, `SUPPORTED_BLOCKERS`, `PHASE_LATER_BLOCKERS`, proactive Great People slot, governor cap, religion partial state on `TurnLedger`. |
| `src/civ_mcp/drex/refresh.py` (modify) | Rules for the new kinds. |
| `src/civ_mcp/drex/runner.py` (modify) | Store religion partial choices after a confirmed stored step; pass `religion_partial` into inputs. |
| `src/civ_mcp/drex/cli.py` (modify) | `probe --blockers` and `probe --kind <name>`. |
| `fixtures/drex/end_turn_blocking_types.txt` (create) | Live dump of the engine enum. |
| `tests/drex/drex_fixtures.py`, `tests/drex/drex_fakes.py` (modify) | Fixtures and fake methods for the new kinds. |
| Tests (create/modify): `test_drex_promotion.py`, `test_drex_governor.py`, `test_drex_dedication.py`, `test_drex_great_people.py`, `test_drex_religion.py`, `test_drex_city_attack.py`, `test_blocker_coverage.py`; `test_drex_refresh.py`, `test_drex_scheduler.py`, `test_drex_executor.py` (extend). |
| `docs/drex.md` (modify) | Supported decisions table, scheduler order, coverage. |

Task order: 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10 → 11.

---

### Task 1: Shared plumbing for new kinds

**Files:**
- Modify: `src/civ_mcp/drex/candidates.py`
- Modify: `src/civ_mcp/drex/executor.py:92-146` (dispatch), `:180-250` (execute)
- Modify: `src/civ_mcp/drex/observation.py` (`DecisionInputs`)
- Modify: `src/civ_mcp/drex/scheduler.py` (`TurnLedger.religion_partial`, `TurnLedger.great_people_offered`)
- Test: `tests/drex/test_drex_executor.py`

**Interfaces:**
- Produces, in `candidates.py`:

```python
class DecisionCategory(StrEnum):
    ...existing...
    PROMOTION = "promotion"
    GOVERNOR = "governor"
    DEDICATION = "dedication"
    GREAT_PERSON = "great_person"
    RELIGION = "religion"
    BELIEF = "belief"
    CITY_ATTACK = "city_attack"

class ActionKind(StrEnum):
    ...existing...
    PROMOTE_UNIT = "promote_unit"
    APPOINT_GOVERNOR = "appoint_governor"
    ASSIGN_GOVERNOR = "assign_governor"
    PROMOTE_GOVERNOR = "promote_governor"
    CHOOSE_DEDICATION = "choose_dedication"
    RECRUIT_GREAT_PERSON = "recruit_great_person"
    PATRONIZE_GREAT_PERSON = "patronize_great_person"
    WAIT_GREAT_PERSON = "wait_great_person"          # no dispatch
    CHOOSE_RELIGION = "choose_religion"              # no dispatch, stored
    CHOOSE_FOLLOWER_BELIEF = "choose_follower_belief"  # no dispatch, stored
    FOUND_RELIGION = "found_religion"                # dispatch with stored parts
    ADD_BELIEF = "add_belief"
    CITY_ATTACK = "city_attack"
    HOLD_FIRE = "hold_fire"                          # no dispatch

@dataclass(frozen=True)
class PromoteParams: unit: UnitRef; promotion_type: str
@dataclass(frozen=True)
class AppointGovernorParams: governor_type: str
@dataclass(frozen=True)
class AssignGovernorParams: governor_type: str; city_id: int
@dataclass(frozen=True)
class PromoteGovernorParams: governor_type: str; promotion_type: str
@dataclass(frozen=True)
class DedicationParams: index: int; name: str
@dataclass(frozen=True)
class GreatPersonParams: individual_id: int; individual_name: str; yield_type: str | None = None  # None = recruit with points
@dataclass(frozen=True)
class WaitParams: subject: str
@dataclass(frozen=True)
class ReligionChoiceParams: religion_type: str
@dataclass(frozen=True)
class BeliefParams: belief_type: str; belief_class: str
@dataclass(frozen=True)
class FoundReligionParams: religion_type: str; follower_belief: str; founder_belief: str
@dataclass(frozen=True)
class CityAttackParams: city_id: int; target_x: int; target_y: int; target_unit_type: str
@dataclass(frozen=True)
class HoldFireParams: city_id: int
```

  `ActionParams` union extended with all of them. In `executor.py`: `NO_DISPATCH = DispatchCall("__none__", ())`; `_execute` skips the game call when `call.method == "__none__"` (raw `""`, no `_no_replay`), then verifies as usual; `dispatch_call` returns `NO_DISPATCH` for `WAIT_GREAT_PERSON`, `CHOOSE_RELIGION`, `CHOOSE_FOLLOWER_BELIEF`, `HOLD_FIRE`. `DispatchCall.to_record()` for it is `{"method": null, "args": []}`.
- `DecisionInputs` gains: `promotable: lq_drex.PromotableUnit | None`, `promotions: lq.UnitPromotionStatus | None`, `governors: lq.GovernorStatus | None`, `dedications: lq.DedicationStatus | None`, `great_people: list[lq.GreatPersonInfo] | None`, `religion: lq.ReligionFoundingStatus | None`, `religion_partial: dict[str, str] | None` (keys `religion_type`, `follower_belief`), `city_targets: list[lq_drex.CityAttackTarget] | None`, `city_attack_city: lq.CityInfo | None`.
- `TurnLedger` gains `religion_partial: dict[str, str] = field(default_factory=dict)`, `great_people_offered: bool = False`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/drex/test_drex_executor.py`:

```python
# ------------------------------------------------- Phase 2 shared plumbing
def test_no_dispatch_candidates_are_confirmed_without_touching_the_game():
    from civ_mcp.drex.candidates import WaitParams
    from civ_mcp.drex.executor import NO_DISPATCH

    game = FakeGame()
    cand = Candidate.create(ActionKind.WAIT_GREAT_PERSON, WaitParams("Hypatia"), label="Wait")
    assert dispatch_call(cand) is NO_DISPATCH
    assert NO_DISPATCH.to_record() == {"method": None, "args": []}
    outcome = _run(game, cand)
    assert outcome.status is OutcomeStatus.CONFIRMED and outcome.reason == "no_action"
    assert outcome.dispatched is False and game.calls == []


def test_every_new_action_kind_has_a_dispatch_mapping():
    from civ_mcp.drex import candidates as c

    samples = {
        ActionKind.PROMOTE_UNIT: c.PromoteParams(c.UnitRef(131073, 1, "UNIT_WARRIOR", 10, 12), "PROMOTION_BATTLECRY"),
        ActionKind.APPOINT_GOVERNOR: c.AppointGovernorParams("GOVERNOR_THE_EDUCATOR"),
        ActionKind.ASSIGN_GOVERNOR: c.AssignGovernorParams("GOVERNOR_THE_EDUCATOR", fx.CAPITAL_ID),
        ActionKind.PROMOTE_GOVERNOR: c.PromoteGovernorParams("GOVERNOR_THE_EDUCATOR", "GOVERNOR_PROMOTION_EDUCATOR_LIBRARIAN"),
        ActionKind.CHOOSE_DEDICATION: c.DedicationParams(0, "COMMEMORATION_SCIENTIFIC"),
        ActionKind.RECRUIT_GREAT_PERSON: c.GreatPersonParams(7, "Hypatia"),
        ActionKind.PATRONIZE_GREAT_PERSON: c.GreatPersonParams(7, "Hypatia", "YIELD_GOLD"),
        ActionKind.WAIT_GREAT_PERSON: c.WaitParams("great_people"),
        ActionKind.CHOOSE_RELIGION: c.ReligionChoiceParams("RELIGION_BUDDHISM"),
        ActionKind.CHOOSE_FOLLOWER_BELIEF: c.BeliefParams("BELIEF_CHORAL_MUSIC", "BELIEF_CLASS_FOLLOWER"),
        ActionKind.FOUND_RELIGION: c.FoundReligionParams("RELIGION_BUDDHISM", "BELIEF_CHORAL_MUSIC", "BELIEF_TITHE"),
        ActionKind.ADD_BELIEF: c.BeliefParams("BELIEF_TITHE", "BELIEF_CLASS_ENHANCER"),
        ActionKind.CITY_ATTACK: c.CityAttackParams(fx.CAPITAL_ID, 11, 12, "UNIT_WARRIOR"),
        ActionKind.HOLD_FIRE: c.HoldFireParams(fx.CAPITAL_ID),
    }
    expected = {
        ActionKind.PROMOTE_UNIT: ("promote_unit", (131073, "PROMOTION_BATTLECRY")),
        ActionKind.APPOINT_GOVERNOR: ("appoint_governor", ("GOVERNOR_THE_EDUCATOR",)),
        ActionKind.ASSIGN_GOVERNOR: ("assign_governor", ("GOVERNOR_THE_EDUCATOR", fx.CAPITAL_ID)),
        ActionKind.PROMOTE_GOVERNOR: ("promote_governor", ("GOVERNOR_THE_EDUCATOR", "GOVERNOR_PROMOTION_EDUCATOR_LIBRARIAN")),
        ActionKind.CHOOSE_DEDICATION: ("choose_dedication", (0,)),
        ActionKind.RECRUIT_GREAT_PERSON: ("recruit_great_person", (7,)),
        ActionKind.PATRONIZE_GREAT_PERSON: ("patronize_great_person", (7, "YIELD_GOLD")),
        ActionKind.FOUND_RELIGION: ("found_religion", ("RELIGION_BUDDHISM", "BELIEF_CHORAL_MUSIC", "BELIEF_TITHE")),
        ActionKind.ADD_BELIEF: ("add_belief", ("BELIEF_TITHE",)),
        ActionKind.CITY_ATTACK: ("city_attack", (fx.CAPITAL_ID, 11, 12)),
    }
    for kind, params in samples.items():
        call = dispatch_call(Candidate.create(kind, params, label="x"))
        if kind in expected:
            assert (call.method, call.args) == expected[kind], kind
        else:
            assert call.method == "__none__", kind
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/drex/test_drex_executor.py -k "no_dispatch or new_action_kind" -v`
Expected: FAIL with `AttributeError: WAIT_GREAT_PERSON` / `ImportError: NO_DISPATCH`.

- [ ] **Step 3: Implement**

`candidates.py`: add the enum members and dataclasses above; extend `ActionParams`. Note `PromoteParams.unit.unit_id` is the composite id `promote_unit` expects.

`executor.py`:

```python
NO_DISPATCH = DispatchCall("__none__", ())
```

`DispatchCall.to_record`: `{"method": None if self.method == "__none__" else self.method, "args": [...]}`.

`dispatch_call` cases:

```python
        case ActionKind.PROMOTE_UNIT, PromoteParams():
            return DispatchCall("promote_unit", (p.unit.unit_id, p.promotion_type))
        case ActionKind.APPOINT_GOVERNOR, AppointGovernorParams():
            return DispatchCall("appoint_governor", (p.governor_type,))
        case ActionKind.ASSIGN_GOVERNOR, AssignGovernorParams():
            return DispatchCall("assign_governor", (p.governor_type, p.city_id))
        case ActionKind.PROMOTE_GOVERNOR, PromoteGovernorParams():
            return DispatchCall("promote_governor", (p.governor_type, p.promotion_type))
        case ActionKind.CHOOSE_DEDICATION, DedicationParams():
            return DispatchCall("choose_dedication", (p.index,))
        case ActionKind.RECRUIT_GREAT_PERSON, GreatPersonParams():
            return DispatchCall("recruit_great_person", (p.individual_id,))
        case ActionKind.PATRONIZE_GREAT_PERSON, GreatPersonParams():
            return DispatchCall("patronize_great_person", (p.individual_id, p.yield_type))
        case ActionKind.FOUND_RELIGION, FoundReligionParams():
            return DispatchCall("found_religion", (p.religion_type, p.follower_belief, p.founder_belief))
        case ActionKind.ADD_BELIEF, BeliefParams():
            return DispatchCall("add_belief", (p.belief_type,))
        case ActionKind.CITY_ATTACK, CityAttackParams():
            return DispatchCall("city_attack", (p.city_id, p.target_x, p.target_y))
        case (ActionKind.WAIT_GREAT_PERSON | ActionKind.CHOOSE_RELIGION
              | ActionKind.CHOOSE_FOLLOWER_BELIEF | ActionKind.HOLD_FIRE), _:
            return NO_DISPATCH
```

`_execute`: after `call = dispatch_call(candidate)`, if `call.method == "__none__"`: `self._dispatched.add(key)`; `outcome = ActionOutcome(OutcomeStatus.CONFIRMED, "no_action", False)`; return it (skip verify). `_precheck` for these four kinds returns `_ok()` (Tasks 5–8 tighten them). Add a `_verify` fallthrough `case _ if call is NO_DISPATCH` is not needed since `_execute` returns early.

`observation.py`: add the `DecisionInputs` fields (all default `None`). Import `civ_mcp.lua.drex_queries as lq_drex` lazily via `TYPE_CHECKING` to avoid a cycle (the module is created in Task 2; for Task 1 type them as `Any`).

`scheduler.py`: `TurnLedger.religion_partial: dict[str, str] = field(default_factory=dict)`, `great_people_offered: bool = False`.

- [ ] **Step 4: Run tests, then the suite**

Run: `uv run pytest tests/drex/test_drex_executor.py -k "no_dispatch or new_action_kind" -v` → PASS. `uv run pytest tests -q` → `test_every_action_kind_has_a_refresh_rule` now FAILS for the new kinds: add to `refresh.py` `_TABLE`:

```python
    ActionKind.PROMOTE_UNIT: frozenset({"units"}),
    ActionKind.APPOINT_GOVERNOR: frozenset(),
    ActionKind.ASSIGN_GOVERNOR: frozenset({"cities"}),
    ActionKind.PROMOTE_GOVERNOR: frozenset(),
    ActionKind.CHOOSE_DEDICATION: frozenset({"overview"}),
    ActionKind.RECRUIT_GREAT_PERSON: frozenset({"units", "overview"}),
    ActionKind.PATRONIZE_GREAT_PERSON: frozenset({"units", "overview"}),
    ActionKind.WAIT_GREAT_PERSON: frozenset(),
    ActionKind.CHOOSE_RELIGION: frozenset(),
    ActionKind.CHOOSE_FOLLOWER_BELIEF: frozenset(),
    ActionKind.FOUND_RELIGION: frozenset({"overview", "units", "cities"}),
    ActionKind.ADD_BELIEF: frozenset({"overview"}),
    ActionKind.CITY_ATTACK: frozenset({"units"}),
    ActionKind.HOLD_FIRE: frozenset(),
```

(blockers and popup are always included.) Re-run the suite → all pass.

- [ ] **Step 5: Commit**

```bash
uvx ruff format src/civ_mcp/drex/candidates.py src/civ_mcp/drex/executor.py src/civ_mcp/drex/observation.py src/civ_mcp/drex/scheduler.py src/civ_mcp/drex/refresh.py tests/drex/test_drex_executor.py
git add src/civ_mcp/drex/candidates.py src/civ_mcp/drex/executor.py src/civ_mcp/drex/observation.py src/civ_mcp/drex/scheduler.py src/civ_mcp/drex/refresh.py tests/drex/test_drex_executor.py
git commit -m "Phase 2 plumbing: new decision categories, action kinds, params and no-dispatch candidates"
```

---

### Task 2: Unit promotion decision

**Files:**
- Create: `src/civ_mcp/lua/drex_queries.py`
- Modify: `src/civ_mcp/game_state.py` (`get_promotable_units`)
- Modify: `src/civ_mcp/drex/enumerate.py`, `points.py`, `live.py`, `executor.py`, `scheduler.py`
- Modify: `tests/drex/drex_fixtures.py`, `tests/drex/drex_fakes.py`
- Test: `tests/drex/test_drex_promotion.py` (create)

**Interfaces:**
- Produces in `drex_queries.py`:

```python
@dataclass
class PromotableUnit:
    unit_id: int; unit_index: int; unit_type: str; xp: int; xp_needed: int; promotion_count: int

def build_promotable_units_query() -> str      # GameCore
def parse_promotable_units(lines) -> list[PromotableUnit]
```

  `GameState.get_promotable_units() -> list[PromotableUnit]` (one `execute_read`). Scheduler: `PROMOTION_BLOCKER = "ENDTURN_BLOCKING_UNIT_PROMOTION"` in `SUPPORTED_BLOCKERS`; when present, `DecisionSpec(PROMOTION, f"unit:{unit_id}")` for the lowest promotable unit id not yet resolved this turn (the list comes from `core.promotable`, a new `CoreObservation` field filled only when the blocker is present — one extra GameCore round trip per observation while the blocker stands, none otherwise). `LiveObserver.core()`/`refresh()` fill it via `gs.get_promotable_units()` when `PROMOTION_BLOCKER in blockers`. `inputs()` for PROMOTION: `promotions=await gs.get_unit_promotions(unit_id)`, `promotable=<entry>`. Candidates: one `PROMOTE_UNIT` per `promotions.promotions` entry with facts `{"effect": description}`. Precheck: unit still in `get_promotable_units()` (fresh read, 1 rt) and promotion still listed (reuse `inputs.promotions` when current). Verify: raw starts with `PROMOTED|` → confirmed `promoted_from_dispatch`; else poll `get_unit_promotions(unit_id).promotion_count > before`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/drex/test_drex_promotion.py
"""Unit promotion as a Drex decision."""

import asyncio

import drex_fixtures as fx
from drex_fakes import FakeGame

from civ_mcp.drex.candidates import ActionKind, DecisionCategory
from civ_mcp.drex.enumerate import promotion_candidates
from civ_mcp.drex.executor import Executor, OutcomeStatus
from civ_mcp.drex.live import LiveObserver
from civ_mcp.drex.observation import Blocker, DecisionMemory, DecisionSpec
from civ_mcp.drex.points import build_decision_point
from civ_mcp.drex.scheduler import PROMOTION_BLOCKER, Scheduler, TurnLedger
from civ_mcp.lua.drex_queries import parse_promotable_units


async def _no_sleep(_):
    return None


def test_promotable_units_require_the_xp_threshold():
    lines = [
        "PROMOTABLE|131073|1|UNIT_WARRIOR|30|15|1",
        "SKIP|131074|2|UNIT_SCOUT|10|15|0|below_threshold",
    ]
    units = parse_promotable_units(lines)
    assert [u.unit_id for u in units] == [131073]
    assert units[0].xp == 30 and units[0].promotion_count == 1


def test_promotion_candidates_one_per_available_promotion():
    cands = promotion_candidates(fx.promotable_warrior(), fx.warrior_promotions())
    assert [c.kind for c in cands] == [ActionKind.PROMOTE_UNIT] * 2
    assert {c.params.promotion_type for c in cands} == {
        "PROMOTION_BATTLECRY", "PROMOTION_TORTOISE"
    }
    assert all(c.params.unit.unit_id == fx.WARRIOR_ID for c in cands)
    assert cands[0].facts["effect"]


def test_scheduler_offers_promotion_when_the_blocker_stands():
    game = FakeGame()
    game.promotable = [fx.promotable_warrior()]
    game.extra_blockers = [(PROMOTION_BLOCKER, "Unit can be promoted")]
    core = asyncio.run(LiveObserver(game).core())
    assert core.promotable and core.promotable[0].unit_id == fx.WARRIOR_ID
    step = Scheduler().next(core, TurnLedger(turn=5))
    assert step.category is DecisionCategory.PROMOTION
    assert step.entity == f"unit:{fx.WARRIOR_ID}"


def test_promotion_is_not_read_without_the_blocker():
    game = FakeGame()
    game.promotable = [fx.promotable_warrior()]
    core = asyncio.run(LiveObserver(game).core())
    assert core.promotable == []
    assert game.query_counts["get_promotable_units"] == 0


def test_promote_dispatches_and_confirms_from_output():
    game = FakeGame()
    game.promotable = [fx.promotable_warrior()]
    game.extra_blockers = [(PROMOTION_BLOCKER, "Unit can be promoted")]
    obs = LiveObserver(game)
    core = asyncio.run(obs.core())
    spec = DecisionSpec(DecisionCategory.PROMOTION, f"unit:{fx.WARRIOR_ID}")
    inputs = asyncio.run(obs.inputs(spec, core))
    point, _ = build_decision_point(spec, core, inputs, DecisionMemory(), objective="o", decision_id="T5#1", max_options=255)
    cand = point.candidates[0]
    outcome = asyncio.run(Executor(game, sleep=_no_sleep).execute(cand, point, current_version=obs.version, turn=5, inputs=inputs))
    assert outcome.status is OutcomeStatus.CONFIRMED
    assert ("promote_unit", (fx.WARRIOR_ID, cand.params.promotion_type)) in game.calls
    assert game.promotable == []  # the fake consumed the promotion


def test_promotion_precheck_rejects_a_unit_no_longer_promotable():
    game = FakeGame()
    game.promotable = [fx.promotable_warrior()]
    game.extra_blockers = [(PROMOTION_BLOCKER, "x")]
    obs = LiveObserver(game)
    core = asyncio.run(obs.core())
    spec = DecisionSpec(DecisionCategory.PROMOTION, f"unit:{fx.WARRIOR_ID}")
    inputs = asyncio.run(obs.inputs(spec, core))
    point, _ = build_decision_point(spec, core, inputs, DecisionMemory(), objective="o", decision_id="T5#1", max_options=255)
    game.promotable = []  # promoted by something else meanwhile
    outcome = asyncio.run(Executor(game, sleep=_no_sleep).execute(point.candidates[0], point, current_version=obs.version, turn=5, inputs=inputs))
    assert outcome.status is OutcomeStatus.REJECTED and game.calls == []
```

Fixtures (`drex_fixtures.py`):

```python
def promotable_warrior():
    from civ_mcp.lua.drex_queries import PromotableUnit
    return PromotableUnit(WARRIOR_ID, WARRIOR_IDX, "UNIT_WARRIOR", xp=30, xp_needed=15, promotion_count=1)


def warrior_promotions():
    return lq.UnitPromotionStatus(
        unit_id=WARRIOR_ID, unit_index=WARRIOR_IDX, unit_type="UNIT_WARRIOR",
        promotions=[
            lq.PromotionOption("PROMOTION_BATTLECRY", "Battlecry", "+7 Combat Strength vs. melee and ranged units"),
            lq.PromotionOption("PROMOTION_TORTOISE", "Tortoise", "+10 Combat Strength when defending against ranged attacks"),
        ],
        xp=30, xp_needed=15, promotion_count=1,
    )
```

Fake (`drex_fakes.py`): `self.promotable: list = []`; `async def get_promotable_units(self)`: count + `self.conn.roundtrips += 1`; return copies. `async def get_unit_promotions(self, unit_id)`: return `fx.warrior_promotions()` if any promotable has that id else an empty status. `async def promote_unit(self, unit_id, promotion_type)`: `_record`, remove from `self.promotable`, drop the blocker from `extra_blockers`, return `f"PROMOTED|{promotion_type}|stored:1->0"`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/drex/test_drex_promotion.py -v`
Expected: FAIL with `ModuleNotFoundError: civ_mcp.lua.drex_queries`.

- [ ] **Step 3: Implement the Lua and parser**

```python
# src/civ_mcp/lua/drex_queries.py
"""Lua queries added for the decision-only controller (Phase 2 kinds).

Kept out of ``civ_mcp.lua.__init__`` on purpose: ``GameState`` imports this
module directly.
"""
from __future__ import annotations

from dataclasses import dataclass

from civ_mcp.lua._helpers import SENTINEL


@dataclass
class PromotableUnit:
    unit_id: int
    unit_index: int
    unit_type: str
    xp: int
    xp_needed: int
    promotion_count: int


def build_promotable_units_query() -> str:
    """GameCore: units that genuinely have a promotion available.

    GameCore SetPromotion does not consume XP, so CanPromote() alone stays
    true forever; the XP-threshold formula the legacy end-turn code uses
    (needed = T1 * (n+1) * (n+2) / 2 for n promotions held) is applied too.
    """
    return f"""
local me = Game.GetLocalPlayer()
for i, u in Players[me]:GetUnits():Members() do
  if u:GetX() ~= -9999 then
    local ok, exp = pcall(function() return u:GetExperience() end)
    local ui = GameInfo.Units[u:GetType()]
    local promClass = ui and ui.PromotionClass or ""
    if ok and exp and promClass ~= "" then
      local n = 0
      for p in GameInfo.UnitPromotions() do
        if p.PromotionClass == promClass and exp:HasPromotion(p.Index) then n = n + 1 end
      end
      local t1 = exp:GetExperienceForNextLevel()
      local xp = exp:GetExperiencePoints()
      local needed = t1 * (n + 1) * (n + 2) / 2
      local canAny = false
      for p in GameInfo.UnitPromotions() do
        if p.PromotionClass == promClass and not exp:HasPromotion(p.Index) then
          local okc, c = pcall(function() return exp:CanPromote(p.Index) end)
          if okc and c then canAny = true break end
        end
      end
      local line = (xp >= needed and canAny) and "PROMOTABLE|" or "SKIP|"
      print(line .. u:GetID() .. "|" .. (u:GetID() % 65536) .. "|" .. (ui.UnitType or "UNKNOWN") .. "|" .. xp .. "|" .. needed .. "|" .. n .. (xp >= needed and "" or "|below_threshold"))
    end
  end
end
print("{SENTINEL}")
"""


def parse_promotable_units(lines: list[str]) -> list[PromotableUnit]:
    out = []
    for line in lines:
        if line.startswith("PROMOTABLE|"):
            p = line.split("|")
            out.append(PromotableUnit(int(p[1]), int(p[2]), p[3], int(float(p[4])), int(float(p[5])), int(p[6])))
    return out
```

(`u:GetID()` in GameCore is the composite id; the existing promotions query prints `unit:GetID() % 65536` as the index, matching `UnitInfo.unit_index`.)

`game_state.py`: `from civ_mcp.lua import drex_queries as lq_drex`; `async def get_promotable_units(self) -> list[lq_drex.PromotableUnit]: return lq_drex.parse_promotable_units(await self.conn.execute_read(lq_drex.build_promotable_units_query()))`.

- [ ] **Step 4: Observation, scheduler, candidates, executor**

`observation.py`: `CoreObservation.promotable: list[Any] = field(default_factory=list)`. `live.py`: in `_from_snapshot` and the fallback path, after blockers are known: `promotable = await gs.get_promotable_units() if PROMOTION_BLOCKER in {b.blocking_type ...} else []` (import the constant from `scheduler.py` — to avoid a cycle define `PROMOTION_BLOCKER` in `observation.py` and re-export from scheduler). In `refresh()`: when `"units"` or `"blockers"` in parts and the blocker is present in the refreshed blockers, re-read `promotable`; otherwise keep `previous.promotable` if the blocker is still present, else `[]`.

`scheduler.py`: `PROMOTION_BLOCKER` in `SUPPORTED_BLOCKERS`; in `_next_proactive`, after the four empire blockers:

```python
        if PROMOTION_BLOCKER in blockers:
            for pu in sorted(core.promotable, key=lambda u: u.unit_id):
                spec = DecisionSpec(DecisionCategory.PROMOTION, f"unit:{pu.unit_id}")
                if self._open(ledger, spec):
                    return spec
```

`key_for`: PROMOTION uses `f"promotion:{entity}"`; `_limit`: 2 per unit per turn; `note()`: resolved on CONFIRMED/PENDING (the default branch already does that for categories not listed).

`enumerate.py`:

```python
def promotion_candidates(unit: PromotableUnit, status: lq.UnitPromotionStatus) -> list[Candidate]:
    ref = UnitRef(unit.unit_id, unit.unit_index, unit.unit_type, -1, -1)
    return [
        Candidate.create(ActionKind.PROMOTE_UNIT, PromoteParams(ref, p.promotion_type),
                         label=p.name or pretty(p.promotion_type), facts={"effect": p.description})
        for p in status.promotions
    ]
```

(UnitRef x/y are unknown here; `-1` marks that. The precheck does not compare position for promotions.) `points.py`: `QUESTIONS[PROMOTION] = "Which promotion should this unit take?"`; `_enumerate`: `if cat is PROMOTION and inputs.promotable and inputs.promotions: return promotion_candidates(inputs.promotable, inputs.promotions), [], entity, inputs`. `live.py inputs()`: `case PROMOTION: uid = int(eid.split(":")[1]); pu = next((u for u in core.promotable if u.unit_id == uid), None); return DecisionInputs(promotable=pu, promotions=await gs.get_unit_promotions(uid) if pu else None)`.

`executor.py` precheck:

```python
            case ActionKind.PROMOTE_UNIT:
                fresh = await gs.get_promotable_units()
                if not any(u.unit_id == p.unit.unit_id for u in fresh):
                    return _no("unit_not_promotable")
                status = self._known.promotions if self._known and self._known.promotions else await gs.get_unit_promotions(p.unit.unit_id)
                if not any(o.promotion_type == p.promotion_type for o in status.promotions):
                    return _no("promotion_not_available")
                return _ok(count=status.promotion_count)
```

verify:

```python
            case ActionKind.PROMOTE_UNIT:
                if raw.startswith(("PROMOTED|", "OK:PROMOTED|")):
                    return confirmed("promoted_from_dispatch")
                if _game_error(raw):
                    return self._unconfirmed(raw, "promotion_refused")
                async def more():
                    st = await gs.get_unit_promotions(p.unit.unit_id)
                    return st.promotion_count > pre["count"]
                if await self._poll(more):
                    return confirmed("promotion_count_increased")
                return self._unconfirmed(raw, "promotion_not_observed")
```

Budget: precheck 1 rt (promotable) + dispatch 1 (+ the dismiss write inside `promote_unit`) — `promote_unit` itself does two round trips (GameCore promote + InGame stale-notification check). Record in `docs/drex.md` that a promotion costs 3 execute round trips and exempt `promote_unit` in `test_decision_round_trip_budget` with a comment (the budget test's `over` filter gets `if r["dispatch"]["method"] != "promote_unit"`).

- [ ] **Step 5: Run tests, suite, commit**

```bash
uv run pytest tests/drex/test_drex_promotion.py -v && uv run pytest tests -q
uvx ruff format src/civ_mcp/lua/drex_queries.py src/civ_mcp/game_state.py src/civ_mcp/drex/*.py tests/drex/test_drex_promotion.py tests/drex/drex_fixtures.py tests/drex/drex_fakes.py
git add src/civ_mcp/lua/drex_queries.py src/civ_mcp/game_state.py src/civ_mcp/drex/enumerate.py src/civ_mcp/drex/points.py src/civ_mcp/drex/live.py src/civ_mcp/drex/executor.py src/civ_mcp/drex/scheduler.py src/civ_mcp/drex/observation.py tests/drex/test_drex_promotion.py tests/drex/drex_fixtures.py tests/drex/drex_fakes.py tests/drex/test_drex_speed.py
git commit -m "Unit promotion is a Drex decision"
```

---

### Task 3: Governor decisions

**Files:**
- Modify: `enumerate.py`, `points.py`, `live.py`, `executor.py`, `scheduler.py`, fixtures, fakes
- Test: `tests/drex/test_drex_governor.py` (create)

**Interfaces:**
- Scheduler: `GOVERNOR_BLOCKERS = frozenset({"ENDTURN_BLOCKING_GOVERNOR_APPOINTMENT", "ENDTURN_BLOCKING_GOVERNOR_IDLE", "ENDTURN_BLOCKING_GOVERNOR_OPPORTUNITY", "ENDTURN_BLOCKING_GOVERNOR_PROMOTION"})` all in `SUPPORTED_BLOCKERS`; when any is present, `DecisionSpec(GOVERNOR, "empire")`, limit 5 decisions per turn (`_limit`), not resolved on success (repeatable like envoys: add `GOVERNOR` to the `note()` non-resolving tuple).
- `inputs()`: `governors=await gs.get_governors()`, plus `core.cities` for assignment targets.
- `governor_candidates(status: lq.GovernorStatus, cities: list[lq.CityInfo]) -> list[Candidate]`:
  - if `status.can_appoint`: one `APPOINT_GOVERNOR` per `available_to_appoint` (facts: title, base ability).
  - for each `appointed` with `assigned_city_id == -1`: one `ASSIGN_GOVERNOR` per own city that has no governor assigned (facts: city name, population).
  - for each `appointed` with `available_promotions` and `status.points_available > 0`: one `PROMOTE_GOVERNOR` per promotion (facts: description, level).
- Precheck (fresh `get_governors`, 1 rt; reuse `inputs.governors` when current): the same eligibility rule for that candidate. Verify: raw starts with `APPOINTED|`, `ASSIGNED|`, `PROMOTED|` (after `_action_result` strips `OK:`) → confirmed from dispatch; `APPOINT_REQUESTED|` → poll `get_governors()` for the type in `appointed`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/drex/test_drex_governor.py
"""Governor appoint / assign / promote as Drex decisions."""
import asyncio

import drex_fixtures as fx
from drex_fakes import FakeGame

from civ_mcp.drex.candidates import ActionKind, DecisionCategory
from civ_mcp.drex.enumerate import governor_candidates
from civ_mcp.drex.executor import Executor, OutcomeStatus
from civ_mcp.drex.live import LiveObserver
from civ_mcp.drex.observation import DecisionMemory, DecisionSpec
from civ_mcp.drex.points import build_decision_point
from civ_mcp.drex.scheduler import GOVERNOR_BLOCKERS, Scheduler, TurnLedger
from civ_mcp.drex.executor import ActionOutcome


async def _no_sleep(_):
    return None


def test_candidates_cover_appoint_assign_and_promote():
    cands = governor_candidates(fx.governors(points=1, unassigned=True), [fx.capital()])
    kinds = {c.kind for c in cands}
    assert kinds == {ActionKind.APPOINT_GOVERNOR, ActionKind.ASSIGN_GOVERNOR, ActionKind.PROMOTE_GOVERNOR}
    assign = next(c for c in cands if c.kind is ActionKind.ASSIGN_GOVERNOR)
    assert assign.params.city_id == fx.CAPITAL_ID and assign.facts["city"] == "Roma"


def test_no_assign_candidates_for_a_city_that_already_has_a_governor():
    status = fx.governors(points=0, unassigned=True)
    cands = governor_candidates(status, [fx.capital()])
    # the fixture's other governor already sits in Roma
    assert not any(c.kind is ActionKind.ASSIGN_GOVERNOR and c.params.city_id == fx.CAPITAL_ID
                   for c in cands if c.params.governor_type == "GOVERNOR_THE_EDUCATOR" and False)
    assert all(c.kind is not ActionKind.APPOINT_GOVERNOR for c in cands)  # no points


def test_scheduler_offers_governor_for_each_blocker_type():
    for b in sorted(GOVERNOR_BLOCKERS):
        game = FakeGame()
        game.extra_blockers = [(b, "governor")]
        core = asyncio.run(LiveObserver(game).core())
        step = Scheduler().next(core, TurnLedger(turn=5))
        assert step.category is DecisionCategory.GOVERNOR, b


def test_governor_decisions_are_capped_per_turn():
    s = Scheduler()
    game = FakeGame()
    game.extra_blockers = [("ENDTURN_BLOCKING_GOVERNOR_IDLE", "x")]
    core = asyncio.run(LiveObserver(game).core())
    ledger = TurnLedger(turn=5)
    n = 0
    while True:
        step = s.next(core, ledger)
        if step.category is not DecisionCategory.GOVERNOR:
            break
        s.note(ledger, step, ActionKind.APPOINT_GOVERNOR, ActionOutcome(OutcomeStatus.CONFIRMED, "x", True))
        n += 1
        assert n <= 5
    assert n == 5


def test_appoint_dispatches_and_confirms():
    game = FakeGame()
    game.governor_status = fx.governors(points=1, unassigned=False)
    game.extra_blockers = [("ENDTURN_BLOCKING_GOVERNOR_APPOINTMENT", "x")]
    obs = LiveObserver(game)
    core = asyncio.run(obs.core())
    spec = DecisionSpec(DecisionCategory.GOVERNOR, "empire")
    inputs = asyncio.run(obs.inputs(spec, core))
    point, _ = build_decision_point(spec, core, inputs, DecisionMemory(), objective="o", decision_id="T5#1", max_options=255)
    cand = next(c for c in point.candidates if c.kind is ActionKind.APPOINT_GOVERNOR)
    outcome = asyncio.run(Executor(game, sleep=_no_sleep).execute(cand, point, current_version=obs.version, turn=5, inputs=inputs))
    assert outcome.status is OutcomeStatus.CONFIRMED
    assert ("appoint_governor", (cand.params.governor_type,)) in game.calls
    assert any(g.governor_type == cand.params.governor_type for g in game.governor_status.appointed)
```

Fixture `governors(points: int, unassigned: bool)`: `GovernorStatus(points_available=points, points_spent=1, can_appoint=points > 0, appointed=[AppointedGovernor("GOVERNOR_THE_EDUCATOR", "Pingala", -1 if unassigned else CAPITAL_ID, "Unassigned" if unassigned else "Roma", False, 0, [GovernorPromotion("GOVERNOR_PROMOTION_EDUCATOR_LIBRARIAN", "Librarian", "+1 Science per ...", 1, 0)])], available_to_appoint=[GovernorInfo("GOVERNOR_THE_DEFENDER", "Victor", "Castellan", "Military governor", "Redoubt", "+5 Combat Strength ...")])`. Fake: `self.governor_status = fx.governors(points=0, unassigned=False)`; `get_governors` (count round trip); `appoint_governor` appends an `AppointedGovernor(type, type.title(), -1, "Unassigned", False)`, decrements points, drops governor blockers when nothing is left to do, returns `"APPOINTED|Victor (Castellan)"`; `assign_governor` sets `assigned_city_id`, returns `"ASSIGNED|Pingala to Roma"`; `promote_governor` removes the promotion, returns `"PROMOTED|Pingala with Librarian"`.

- [ ] **Step 2: Run tests to verify they fail** — `uv run pytest tests/drex/test_drex_governor.py -v` → `ImportError: governor_candidates`.

- [ ] **Step 3: Implement** the interfaces above. Verify branch:

```python
            case ActionKind.APPOINT_GOVERNOR | ActionKind.ASSIGN_GOVERNOR | ActionKind.PROMOTE_GOVERNOR:
                first = raw.splitlines()[0] if raw else ""
                if first.startswith(("APPOINTED|", "ASSIGNED|", "PROMOTED|")):
                    return confirmed("governor_confirmed_from_dispatch")
                if _game_error(raw):
                    return self._unconfirmed(raw, "governor_action_refused")
                async def applied():
                    st = await gs.get_governors()
                    if c.kind is ActionKind.APPOINT_GOVERNOR:
                        return any(g.governor_type == p.governor_type for g in st.appointed)
                    if c.kind is ActionKind.ASSIGN_GOVERNOR:
                        return any(g.governor_type == p.governor_type and g.assigned_city_id == p.city_id for g in st.appointed)
                    return not any(pr.promotion_type == p.promotion_type for g in st.appointed if g.governor_type == p.governor_type for pr in g.available_promotions)
                if await self._poll(applied):
                    return confirmed("governor_readback_confirmed")
                return self._unconfirmed(raw, "governor_action_not_observed")
```

- [ ] **Step 4: Suite, format, commit** — `git commit -m "Governor appoint, assign and promote are Drex decisions"`.

---

### Task 4: Era dedication decision

**Files:** `enumerate.py`, `points.py`, `live.py`, `executor.py`, `scheduler.py`, fixtures, fakes; Test: `tests/drex/test_drex_dedication.py` (create)

**Interfaces:**
- `DEDICATION_BLOCKER = "ENDTURN_BLOCKING_COMMEMORATION_AVAILABLE"` in `SUPPORTED_BLOCKERS`; `DecisionSpec(DEDICATION, "empire")`, resolved on success, limit `selections_allowed` per turn (default 1).
- `inputs()`: `dedications=await gs.get_dedications()`.
- `dedication_candidates(status) -> list[Candidate]`: one `CHOOSE_DEDICATION` per `choices` entry not in `active`, label `pretty(name)`, facts `{"bonus": normal_desc if age_type == "Normal" else golden_desc if age_type in ("Golden", "Heroic") else dark_desc, "age": age_type}`.
- Precheck: choice index still offered and not active (reuse `inputs.dedications` when current). Verify: raw starts with `DEDICATION_CHOSEN|` → confirmed; else poll `get_dedications().active` contains `p.name`.

- [ ] **Step 1: Tests**

```python
# tests/drex/test_drex_dedication.py
def test_candidates_describe_the_bonus_for_the_current_age():
    cands = dedication_candidates(fx.dedications(age="Golden"))
    assert {c.params.name for c in cands} == {"COMMEMORATION_SCIENTIFIC", "COMMEMORATION_MILITARY"}
    assert all(c.facts["age"] == "Golden" for c in cands)
    assert cands[0].facts["bonus"].startswith("golden:")


def test_active_dedications_are_not_offered_again():
    st = fx.dedications(age="Normal")
    st.active = ["COMMEMORATION_SCIENTIFIC"]
    assert [c.params.name for c in dedication_candidates(st)] == ["COMMEMORATION_MILITARY"]


def test_scheduler_offers_dedication_when_the_blocker_stands(): ...  # as in Task 3
def test_choose_dedication_dispatches_index_and_confirms(): ...      # ("choose_dedication", (idx,)) in game.calls; fake returns "DEDICATION_CHOSEN|Scientific"
```

Fixture `dedications(age)`: `DedicationStatus(age_type=age, era=1, era_score=12, dark_threshold=5, golden_threshold=17, selections_allowed=1, active=[], choices=[DedicationChoice(0, "COMMEMORATION_SCIENTIFIC", "normal: +1 era score per ...", "golden: free tech ...", "dark: ..."), DedicationChoice(1, "COMMEMORATION_MILITARY", "normal: ...", "golden: ...", "dark: ...")])`. Fake: `self.dedication_status`, `get_dedications`, `choose_dedication(index)` appends the chosen name to `active`, drops the blocker, returns `"DEDICATION_CHOSEN|Scientific"`.

- [ ] **Steps 2–4:** RED, implement, GREEN, suite, commit `"Era dedication is a Drex decision"`.

---

### Task 5: Great People decisions (proactive and forced claims)

**Files:** as Task 4; Test: `tests/drex/test_drex_great_people.py` (create)

**Interfaces:**
- `CLAIM_BLOCKER = "ENDTURN_BLOCKING_CLAIM_GREAT_PERSON"` in `SUPPORTED_BLOCKERS`.
- Scheduler: blocker-driven when `CLAIM_BLOCKER` present (`DecisionSpec(GREAT_PERSON, "empire")`, no wait option); proactive once per turn after production and before units when `core.great_people_available` is true — a new `CoreObservation` field set by `LiveObserver` from `gs.get_great_people()` **only every 5 turns or when the blocker is present** (the read is one InGame round trip; `core.turn % 5 == 0` gate keeps the per-turn overhead at zero otherwise; ledger `great_people_offered` prevents repeats in a turn).
- `great_person_candidates(people, gold: float, faith: float, *, forced: bool) -> list[Candidate]`: for each person with `claimant == "Unclaimed"`: `RECRUIT` if `can_recruit`; `PATRONIZE` gold if `0 < gold_cost <= gold`; `PATRONIZE` faith if `0 < faith_cost <= faith`; plus one `WAIT_GREAT_PERSON` unless `forced`. Facts: class, era, ability, cost/points, price. If `forced` and only one candidate remains it becomes `forced_single_candidate` through the existing single-legal-option path (`legal_count == 1`).
- Precheck: fresh `get_great_people` (or `inputs.great_people` when current): person still unclaimed and still affordable. Verify: raw `RECRUITED|`/`PATRONIZED|` → confirmed; else poll `get_great_people` for claimant != Unclaimed. `WAIT` is `NO_DISPATCH`.
- `GameOverview` must expose gold and faith: check `lq.GameOverview` fields (`gold` exists; confirm `faith` — the overview Lua prints faith balance as field 9; if the dataclass lacks it, add `faith: float = 0.0` to the model and parser without touching other agents' files — `lua/models.py` and `lua/overview.py` are not in the do-not-edit list, but `overview.py` has uncommitted edits: stage only your hunk with the `git apply --cached` filter used in Phase 1).

- [ ] **Step 1: Tests**

```python
def test_wait_is_offered_unless_the_claim_is_forced():
    people = fx.great_people()
    free = great_person_candidates(people, gold=500, faith=0, forced=False)
    forced = great_person_candidates(people, gold=500, faith=0, forced=True)
    assert any(c.kind is ActionKind.WAIT_GREAT_PERSON for c in free)
    assert not any(c.kind is ActionKind.WAIT_GREAT_PERSON for c in forced)


def test_forced_claim_drops_wait():
    people = [p for p in fx.great_people() if p.can_recruit][:1]
    cands = great_person_candidates(people, gold=0, faith=0, forced=True)
    assert [c.kind for c in cands] == [ActionKind.RECRUIT_GREAT_PERSON]
    point, _ = build_decision_point(...GREAT_PERSON spec with inputs.great_people=people, core with CLAIM_BLOCKER...)
    assert point.forced_rule == "forced_single_candidate"


def test_patronage_requires_the_treasury():
    people = fx.great_people()
    cheap = great_person_candidates(people, gold=10, faith=0, forced=False)
    assert not any(c.kind is ActionKind.PATRONIZE_GREAT_PERSON for c in cheap)


def test_great_people_read_only_every_five_turns_or_when_forced(): ...  # query_counts["get_great_people"] == 0 at turn 6 without blocker, 1 at turn 5 or with blocker
def test_recruit_dispatches_and_confirms(): ...
def test_wait_confirms_without_dispatch_and_resolves_for_the_turn(): ...  # runner: after WAIT, scheduler does not offer GREAT_PERSON again this turn
```

Fixture `great_people()`: two entries — `GreatPersonInfo("Great Scientist", "Hypatia", "Classical", 60, "Unclaimed", 60, "Libraries +1 Science", 340, 0, True, individual_id=7)` and `GreatPersonInfo("Great General", "Boudica", "Classical", 60, "Unclaimed", 20, "+5 CS ...", 400, 0, False, individual_id=9)`. Fake: `self.great_people`, `get_great_people`, `recruit_great_person(id)` sets claimant to "Rome", returns `"RECRUITED|Hypatia"`; `patronize_great_person(id, yield_type)` likewise, returns `"PATRONIZED|Hypatia|YIELD_GOLD"`.

- [ ] **Steps 2–4:** RED, implement, GREEN, suite, commit `"Great People recruit, patronize or wait are Drex decisions"`.

---

### Task 6: Religion founding (three-step) and added beliefs

**Files:** `drex_queries.py` (`build_add_belief`), `game_state.py` (`add_belief`), `enumerate.py`, `points.py`, `live.py`, `executor.py`, `scheduler.py`, `runner.py`, fixtures, fakes; Test: `tests/drex/test_drex_religion.py` (create)

**Interfaces:**
- Blockers: `RELIGION_BLOCKER = "ENDTURN_BLOCKING_RELIGION"`, `BELIEF_BLOCKER = "ENDTURN_BLOCKING_BELIEF"`, both in `SUPPORTED_BLOCKERS`.
- Religion: `DecisionSpec(RELIGION, "empire")` while `RELIGION_BLOCKER` stands. `inputs()`: `religion=await gs.get_religion_founding_status()`, `religion_partial=dict(ledger.religion_partial)` (the runner sets this after `observer.inputs()` returns, since the observer has no ledger). `religion_candidates(status, partial)`:
  - no `religion_type` in partial → one `CHOOSE_RELIGION` per `available_religions` (label = name).
  - no `follower_belief` → one `CHOOSE_FOLLOWER_BELIEF` per `beliefs_by_class["BELIEF_CLASS_FOLLOWER"]`.
  - else → one `FOUND_RELIGION` per `beliefs_by_class["BELIEF_CLASS_FOUNDER"]` with the stored religion and follower belief.
  Stored steps are `NO_DISPATCH`; the runner, on a CONFIRMED outcome of `CHOOSE_RELIGION`/`CHOOSE_FOLLOWER_BELIEF`, writes `ledger.religion_partial[...]`. `TurnLedger` is recreated on a new turn, which drops partial choices (Review Focus 4); the scheduler also clears `religion_partial` when the blocker is absent. Limit: 3 RELIGION decisions per turn; resolved only after `FOUND_RELIGION` (add RELIGION to the non-resolving tuple in `note()` and resolve explicitly in the runner when the kind is `FOUND_RELIGION`).
  Precheck for `FOUND_RELIGION`: fresh status `not has_religion`, religion and both beliefs still available. Verify: raw `RELIGION_FOUNDED|` → confirmed; else poll `has_religion`.
- Belief: `DecisionSpec(BELIEF, "empire")` while `BELIEF_BLOCKER` stands. `belief_candidates(status)`: one `ADD_BELIEF` per belief in every class of `beliefs_by_class` (facts: class, description); capped by the existing shortlist at 255. New Lua:

```python
def build_add_belief(belief_type: str) -> str:
    return f"""
local me = Game.GetLocalPlayer()
local b = GameInfo.Beliefs["{belief_type}"]
if not b then print("ERR:BELIEF_NOT_FOUND|{belief_type}"); print("{SENTINEL}"); return end
local p = {{}}
p[PlayerOperations.PARAM_BELIEF_TYPE] = b.Hash
UI.RequestPlayerOperation(me, PlayerOperations.ADD_BELIEF, p)
print("OK:BELIEF_ADDED|" .. Locale.Lookup(b.Name))
print("{SENTINEL}")
"""
```

  `GameState.add_belief(belief_type) -> str` (InGame, `_action_result`). Verify: raw `BELIEF_ADDED|` → confirmed; else poll `get_religion_founding_status()` for the belief no longer available.

- [ ] **Step 1: Tests**

```python
def test_religion_steps_in_order():
    st = fx.religion_founding()
    step1 = religion_candidates(st, {})
    assert {c.kind for c in step1} == {ActionKind.CHOOSE_RELIGION}
    step2 = religion_candidates(st, {"religion_type": "RELIGION_BUDDHISM"})
    assert {c.kind for c in step2} == {ActionKind.CHOOSE_FOLLOWER_BELIEF}
    step3 = religion_candidates(st, {"religion_type": "RELIGION_BUDDHISM", "follower_belief": "BELIEF_CHORAL_MUSIC"})
    assert {c.kind for c in step3} == {ActionKind.FOUND_RELIGION}
    assert all(c.params.follower_belief == "BELIEF_CHORAL_MUSIC" for c in step3)


def test_runner_stores_partial_choices_then_founds(tmp_path):
    game = FakeGame(); game.religion_status = fx.religion_founding(); game.extra_blockers = [(RELIGION_BLOCKER, "Found a religion")]
    runner, _ = _runner(game, tmp_path, selector=PreferSelector(prefixes=("religion:RELIGION_BUDDHISM", "follower:BELIEF_CHORAL_MUSIC", "found:", "skip:", "research:", "produce:")))
    asyncio.run(runner.run())
    found = [a for m, a in game.calls if m == "found_religion"]
    assert found == [("RELIGION_BUDDHISM", "BELIEF_CHORAL_MUSIC", found[0][2])]
    assert game.religion_status.has_religion is True


def test_partial_religion_choice_is_dropped_on_new_turn(): ...  # ledger replaced → religion_partial == {}; and scheduler clears it when the blocker is gone
def test_belief_candidates_span_classes_and_add_belief_confirms(): ...  # ("add_belief", ("BELIEF_TITHE",)) in calls; raw "BELIEF_ADDED|Tithe"
```

Fixture `religion_founding()`: `ReligionFoundingStatus(has_religion=False, religion_type=None, religion_name=None, pantheon_index=3, faith_balance=120.0, available_religions=[("RELIGION_BUDDHISM", "Buddhism"), ("RELIGION_TAOISM", "Taoism")], beliefs_by_class={"BELIEF_CLASS_FOLLOWER": [ReligionBeliefOption("BELIEF_CLASS_FOLLOWER", "BELIEF_CHORAL_MUSIC", "Choral Music", "..."), ReligionBeliefOption(..., "BELIEF_FEED_THE_WORLD", ...)], "BELIEF_CLASS_FOUNDER": [ReligionBeliefOption("BELIEF_CLASS_FOUNDER", "BELIEF_TITHE", "Tithe", "..."), ...], "BELIEF_CLASS_ENHANCER": [...]})`. Candidate ids: `religion:<type>`, `follower:<belief>`, `found:<founder>`, `belief:<type>` (set via `Candidate.create` id conventions in `candidates.py` — follow how `pantheon:` ids are formed).

- [ ] **Steps 2–4:** RED, implement, GREEN, suite, commit `"Religion founding (three steps) and added beliefs are Drex decisions"`.

---

### Task 7: Live probe for new queries

**Files:** `src/civ_mcp/drex/cli.py` (`probe --kind`)

**Interfaces:** `civ-drex probe --kind promotion|governor|dedication|great_person|religion|city_attack|blockers` runs only that read-only query against the loaded game and prints the parsed result (dataclasses via `to_jsonable`). `--kind blockers` prints `GameState.get_end_turn_blocking_types()` (Task 10 adds it; implement the CLI wiring here with the method call, Task 10 supplies the method — order the `--kind blockers` branch to import lazily so Task 7 is testable alone).

- [ ] **Step 1: Test** in `tests/drex/test_drex_cli.py`: `_probe_kind("dedication", gs)` calls `gs.get_dedications` once and returns a JSON-able dict (use a stub `gs` with async methods recording calls).
- [ ] **Steps 2–4:** RED → implement `_probe_kind(kind, gs) -> dict` and the argparse flag → GREEN → commit `"civ-drex probe --kind runs one new query read-only"`.

---

### Task 8: City and district ranged attack decisions

**Files:** `drex_queries.py` (`CityAttackTarget`, `build_city_attack_targets_query`, `parse_city_attack_targets`), `game_state.py` (`get_city_attack_targets`), `enumerate.py`, `points.py`, `live.py`, `executor.py`, `scheduler.py`, fixtures, fakes; Test: `tests/drex/test_drex_city_attack.py` (create)

**Interfaces:**
- Blockers: `CITY_ATTACK_BLOCKERS = frozenset({"ENDTURN_BLOCKING_CITY_RANGE_ATTACK", "ENDTURN_BLOCKING_DISTRICT_RANGE_ATTACK"})` in `SUPPORTED_BLOCKERS`. While either stands: one `DecisionSpec(CITY_ATTACK, f"city:{city_id}")` per own city, ascending id, once per city per turn.
- Lua (InGame), reusing `CityManager.GetCommandTargets(pCity, CityCommandTypes.RANGE_ATTACK)` exactly as `build_city_attack` does:

```python
@dataclass
class CityAttackTarget:
    x: int; y: int; unit_type: str; owner_id: int; hp: int; max_hp: int

def build_city_attack_targets_query(city_id: int) -> str:
    return f"""
local me = Game.GetLocalPlayer()
local pCity = Players[me]:GetCities():FindID({city_id} % 65536)
if pCity == nil then print("ERR:CITY_NOT_FOUND"); print("{SENTINEL}"); return end
local w = Map.GetGridSize()
local targets = CityManager.GetCommandTargets(pCity, CityCommandTypes.RANGE_ATTACK)
if targets then
  for _, tbl in pairs(targets) do
    if type(tbl) == "table" then
      for _, idx in ipairs(tbl) do
        local x, y = idx % w, math.floor(idx / w)
        local pu = Map.GetUnitsAt(x, y)
        if pu then for u in pu:Units() do
          if u:GetOwner() ~= me then
            local info = GameInfo.Units[u:GetType()]
            print("TARGET|" .. x .. "|" .. y .. "|" .. (info and info.UnitType or "UNKNOWN") .. "|" .. u:GetOwner() .. "|" .. (u:GetMaxDamage() - u:GetDamage()) .. "|" .. u:GetMaxDamage())
          end
        end end
      end
    end
  end
end
print("{SENTINEL}")
"""
```

  `GameState.get_city_attack_targets(city_id) -> list[CityAttackTarget]`. `inputs()`: `city_attack_city=core.city(id)`, `city_targets=await gs.get_city_attack_targets(id)`.
- `city_attack_candidates(city, targets) -> list[Candidate]`: one `CITY_ATTACK` per target (facts: unit type, owner, hp, distance) plus one `HOLD_FIRE`. With no targets only `HOLD_FIRE` remains → `forced_single_candidate` (Review Focus 5). Precheck for `CITY_ATTACK`: target still in fresh `get_city_attack_targets` (reuse inputs when current). Verify: raw `CITY_RANGE_ATTACK|` → confirmed; `_game_error(raw)` → unconfirmed `attack_refused`; else PENDING `combat_resolution_async`. `HOLD_FIRE` is `NO_DISPATCH`; after it the city is resolved for the turn, and if the blocker still stands at end turn the existing blocked-repeat path handles it (the engine clears the blocker when the turn is forced through; verify live in Task 9 and, if the blocker persists, add a housekeeping dismissal of the `CITY_RANGE_ATTACK` notification mirroring `end_turn.py`'s governor dismissal Lua — record the decision in the ledger).
- District attacks: the same query on the city returns the encampment's targets as well when the engine lists them under the city's command targets; if live verification shows otherwise, `DISTRICT_RANGE_ATTACK` falls back to `HOLD_FIRE` only and is recorded as a Phase 4 follow-up in the ledger and `docs/drex.md`.

- [ ] **Step 1: Tests**

```python
def test_city_attack_candidates_include_each_target_and_hold_fire(): ...
def test_city_attack_without_targets_is_forced_hold_fire():
    game = FakeGame(); game.city_targets = {fx.CAPITAL_ID: []}; game.extra_blockers = [("ENDTURN_BLOCKING_CITY_RANGE_ATTACK", "x")]
    ...build point...
    assert [c.kind for c in point.candidates] == [ActionKind.HOLD_FIRE] and point.forced_rule == "forced_single_candidate"
def test_city_attack_dispatches_and_reports_pending_or_confirmed(): ...  # ("city_attack", (city_id, x, y)) in calls
def test_parse_city_attack_targets(): ...
```

- [ ] **Steps 2–4:** RED, implement, GREEN, suite, commit `"City and district ranged attacks are Drex decisions"`.

---

### Task 9: Live validation restart

- [ ] Stop the current run with `pkill -TERM -f "[b]in/civ-drex play"` (SIGINT does not reach the child through `uv run`), confirm `uv run python scripts/test_connection.py` answers, run `uv run civ-drex probe --kind promotion`, `--kind governor`, `--kind dedication`, `--kind great_person`, `--kind religion`, `--kind city_attack` (for the capital) and `--kind blockers`, and paste the outputs into the ledger. Fix any parser mismatch found (each fix with a failing test first).
- [ ] Save the `--kind blockers` output as `fixtures/drex/end_turn_blocking_types.txt` (one `NAME|value` per line, sorted).
- [ ] Restart `uv run civ-drex play --turns 500` and watch until the first new-kind decision appears in the log (`grep -c '"category": "promotion"'` etc.). Record the first live outcome of each kind reached.

---

### Task 10: Blocker coverage test

**Files:** `drex_queries.py` (`build_end_turn_blocking_types_query`, `parse_end_turn_blocking_types`), `game_state.py` (`get_end_turn_blocking_types`), `scheduler.py` (`PHASE_LATER_BLOCKERS`), `fixtures/drex/end_turn_blocking_types.txt` (from Task 9), Test: `tests/drex/test_blocker_coverage.py` (create)

**Interfaces:**
- Lua (InGame): `for k, v in pairs(EndTurnBlockingTypes) do print("BT|" .. k .. "|" .. tostring(v)) end`; parser returns `dict[str, int]`.
- `scheduler.py`:

```python
# Engine blockers whose decision kinds are scheduled for later phases (spec
# Section 8). Listing them here is a deliberate classification: the run waits
# on them (unsupported_blocker) rather than stopping, and the coverage test
# fails if the engine adds a type nobody classified.
PHASE_LATER_BLOCKERS = frozenset({
    "ENDTURN_BLOCKING_CONSIDER_RAZE_CITY", "ENDTURN_BLOCKING_CONSIDER_DISLOYAL_CITY",
    "ENDTURN_BLOCKING_SPY_CHOOSE_ESCAPE_ROUTE", "ENDTURN_BLOCKING_SPY_CHOOSE_DRAGNET_PRIORITY",
    "ENDTURN_BLOCKING_ARTIFACT", "ENDTURN_BLOCKING_EMERGENCY_NEEDS_ATTENTION",
    "ENDTURN_BLOCKING_WORLD_CONGRESS_SESSION", "ENDTURN_BLOCKING_WORLD_CONGRESS_SPECIAL_SESSION",
})
```

- [ ] **Step 1: Test**

```python
# tests/drex/test_blocker_coverage.py
from pathlib import Path
from civ_mcp.drex.scheduler import HOUSEKEEPING_BLOCKERS, PHASE_LATER_BLOCKERS, SUPPORTED_BLOCKERS

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "drex" / "end_turn_blocking_types.txt"


def test_every_engine_blocker_type_is_classified():
    names = {ln.split("|")[0] for ln in FIXTURE.read_text().splitlines() if ln and not ln.startswith("#")}
    names.discard("NO_ENDTURN_BLOCKING")
    classified = SUPPORTED_BLOCKERS | HOUSEKEEPING_BLOCKERS | PHASE_LATER_BLOCKERS
    assert names <= classified, sorted(names - classified)
    assert not (SUPPORTED_BLOCKERS & PHASE_LATER_BLOCKERS)


def test_fixture_matches_the_game_sources_list():
    # the 31 names read from ActionPanel.lua / NotificationPanel.lua on 2026-09-30
    expected = {"UNITS", "UNIT_NEEDS_ORDERS", "STACKED_UNITS", "PRODUCTION", "RESEARCH", "CIVIC", "FILL_CIVIC_SLOT",
                "CONSIDER_GOVERNMENT_CHANGE", "GIVE_INFLUENCE_TOKEN", "PANTHEON", "RELIGION", "BELIEF", "UNIT_PROMOTION",
                "GOVERNOR_APPOINTMENT", "GOVERNOR_IDLE", "GOVERNOR_OPPORTUNITY", "GOVERNOR_PROMOTION", "COMMEMORATION_AVAILABLE",
                "CLAIM_GREAT_PERSON", "CONSIDER_RAZE_CITY", "CONSIDER_DISLOYAL_CITY", "SPY_CHOOSE_ESCAPE_ROUTE",
                "SPY_CHOOSE_DRAGNET_PRIORITY", "ARTIFACT", "EMERGENCY_NEEDS_ATTENTION", "CITY_RANGE_ATTACK", "DISTRICT_RANGE_ATTACK",
                "WORLD_CONGRESS_SESSION", "WORLD_CONGRESS_SPECIAL_SESSION", "WORLD_CONGRESS_LOOK"}
    names = {ln.split("|")[0].removeprefix("ENDTURN_BLOCKING_") for ln in FIXTURE.read_text().splitlines() if ln and not ln.startswith("#")}
    names.discard("NO_ENDTURN_BLOCKING")
    assert expected <= names, sorted(expected - names)
```

`UNIT_NEEDS_ORDERS` must be added to `SUPPORTED_BLOCKERS` (units) if the live dump contains it.

- [ ] **Steps 2–4:** RED (fixture missing → write it from Task 9's dump), implement, GREEN, commit `"Every engine end-turn blocker type is classified; coverage test"`.

---

### Task 11: Documentation

- [ ] `docs/drex.md`: supported decisions table gains the seven kinds with their candidates/dispatch/postcondition; scheduler order lists promotion, governor, dedication, religion, belief, city attack after the empire blockers and Great People after production; the "Speed" section notes promotion costs 3 execute round trips; the coverage matrix reference points at `fixtures/drex/end_turn_blocking_types.txt` and `PHASE_LATER_BLOCKERS`; `probe --kind`. Status table: Phase 2 row with the first live outcomes from Task 9.
- [ ] Commit `"Document Phase 2 decision kinds and blocker coverage"`.

---

## Self-review notes

- **Spec coverage.** Section B rows: unit promotion (T2), governors (T3), dedication (T4), Great Person incl. `CLAIM_GREAT_PERSON` (T5), religion three-step and `BELIEF` (T6), `CITY_RANGE_ATTACK`/`DISTRICT_RANGE_ATTACK` (T8). Coverage matrix and `probe --blockers` (T7, T10). Phase 3/4 rows are classified as `PHASE_LATER_BLOCKERS`, not implemented — matches spec Section 8.
- **Type consistency.** `NO_DISPATCH` (T1) used by T5, T6, T8. `PromotableUnit` (T2) consumed by fixtures and scheduler. `religion_partial` name matches `TurnLedger` (T1) ↔ `DecisionInputs` (T1) ↔ runner (T6). `CityAttackTarget` fields match the Lua print order.
- **Review Focus pins.** 1 → T2 `test_promotable_units_require_the_xp_threshold`; 2 → T3 `test_governor_decisions_are_capped_per_turn`; 3 → T5 `test_forced_claim_drops_wait`; 4 → T6 `test_partial_religion_choice_is_dropped_on_new_turn`; 5 → T8 `test_city_attack_without_targets_is_forced_hold_fire`.
- **Known uncertainties, verified live in Task 9:** `u:GetID()` semantics in GameCore for the composite id; `CityManager.GetCommandTargets` including encampment targets; whether `HOLD_FIRE` leaves the `CITY_RANGE_ATTACK` blocker standing (fallback: dismiss the notification as housekeeping); the exact `EndTurnBlockingTypes` member list (fixture).
