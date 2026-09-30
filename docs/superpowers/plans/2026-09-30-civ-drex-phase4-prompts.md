# civ-drex Phase 4: Late and rare prompts — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The three modal prompts this Base-ruleset game can still raise and the controller only waits on today become Drex decisions: a captured or rebelled city (keep / raze / liberate / reject), a caught spy's escape route (both the escape-route and the dragnet-priority blocker, which the Base UI routes to the same popup), and the player an excavated artifact is credited to. After this phase the only `PHASE_LATER_BLOCKERS` left are types this game does not have (emergencies, World Congress sessions).

**Architecture:** Each prompt is one new `DecisionCategory` scheduled from its end-turn blocker, one `ActionKind`, one InGame Lua query that lists exactly the options the game's own popup offers (`RazeCity.lua`, `EspionageEscape.lua`, `ChooseArtifact.lua`), and one dispatch that re-validates and requests the same `PlayerOperation` / `CityCommand` the popup does. Scheduler, executor, refresh and spectator follow the existing blocker-driven pattern (`CITY_ATTACK`, `BELIEF`). The prompt categories repeat while the blocker stands (a second captured city the same turn), are bounded per turn, and a blocker that stands with nothing left to decide is dismissed as housekeeping like a stale promotion flag.

**Tech Stack:** Python 3.12, asyncio, pytest (`uv run pytest tests -q`), ruff via `uvx ruff`. Lua through FireTuner, InGame context.

**Spec:** `docs/superpowers/specs/2026-09-30-civ-drex-never-stop-design.md` — Section 4 rows "Captured city", "Disloyal city", "Spy escape"; blocker matrix rows `CONSIDER_RAZE_CITY`, `CONSIDER_DISLOYAL_CITY`, `SPY_CHOOSE_ESCAPE_ROUTE`, `SPY_CHOOSE_DRAGNET_PRIORITY`, `ARTIFACT`; Section 8 phase 4.

## Global Constraints

- Every selection among two or more options is Drex's (spec principle 1). A prompt with one legal option (a city that can only be kept, an artifact with no rival claimant, a spy whose only route is the City Center) executes under `forced_single_candidate`.
- The run never stops: a prompt the query cannot read yields `no_candidates`, the key is exhausted for the turn, and the standing blocker is dismissed as housekeeping (never a stop).
- Speed: inputs for a prompt decision are 1 round trip; execute is at most 2 (precheck reuses the inputs when the observation is current, dispatch confirms from its own output); refresh 1.
- `GetNextEscapingSpyID` crashes the game in some states (comment in `espionage.py`): it is called only after the escape or dragnet blocker's notification is found standing, in the same Lua script.
- Do not edit `src/civ_mcp/lua/{__init__,cities,overview,tech,economy,governance,map}.py`, `src/civ_mcp/server.py`, `src/civ_mcp/game_launcher.py`, `src/civ_mcp/end_turn.py` (other agents' work; the runner's `decision_only=True` end turn already leaves these blockers alone); new Lua goes in `src/civ_mcp/lua/drex_queries.py`. Stage only files you changed.
- The game is open: after each task's suite is green, run `uv run civ-drex probe --kind <new kind>` while the run is stopped (`pkill -TERM -f "[b]in/civ-drex play"`) if the tuner is free, and record the output in the ledger. A prompt-specific query returns its "nothing pending" line when no prompt stands; that is the expected live result.
- Suite is 532 passing at the start; run the full suite before each commit; lint touched files with `uvx ruff format` / `uvx ruff check`. Commits end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Composite ids as elsewhere: unit `GetID() + owner * 65536`; city ids are passed to `FindID(id % 65536)`, so the query prints `GetID() + me * 65536` for cities too.

## Review Focus

1. **Two cities pending in one turn.** `resolve_city_capture(action)` acts on "the next pending city", not on a city id. If a second capture is pending, Drex's choice for city A must never be applied to city B: the precheck re-reads the pending city and rejects when the id differs. Pinned in Task 1 (`test_precheck_rejects_when_a_different_city_is_pending`).
2. **Blocker that outlives the decision.** After a confirmed keep/raze the engine may keep `CONSIDER_RAZE_CITY` raised for a tick; the scheduler re-offers, the query says nothing is pending, and the run must dismiss the blocker as housekeeping rather than loop or wait 30 s. Pinned in Task 1 (`test_standing_blocker_with_nothing_pending_is_dismissed`).
3. **Escape query when no spy is escaping.** Calling `GetNextEscapingSpyID` without the prompt can crash the game; the query must return `NO_ESCAPING_SPY` before that call whenever neither escape blocker's notification stands. Pinned in Task 2 (`test_escape_query_checks_the_notification_before_asking_for_the_spy`).
4. **Artifact with no choice.** Goody-hut, barbarian and heroic-relic artifacts list only the acting player; the candidate list must then be a single forced option, never an empty decision that exhausts and dismisses a prompt the engine still wants answered. Pinned in Task 3 (`test_no_choice_artifact_is_forced_single_candidate`).
5. **Dragnet blocker.** The Base UI opens the escape popup for `SPY_CHOOSE_DRAGNET_PRIORITY`; the scheduler must treat it like the escape blocker (same category, same query) and never as unsupported. Pinned in Task 2 (`test_scheduler_offers_spy_escape_for_both_blockers`).

---

## File Structure

| File | Responsibility |
|---|---|
| `src/civ_mcp/lua/drex_queries.py` (modify) | `CapturedCity` + `build_captured_city_query` / `parse_captured_city`; `EscapeChoice` + `build_spy_escape_options_query` / `parse_spy_escape_options` / `build_choose_spy_escape`; `ArtifactChoice` + `build_artifact_choice_query` / `parse_artifact_choice` / `build_choose_artifact_player`. |
| `src/civ_mcp/game_state.py` (modify) | `get_captured_city`, `get_spy_escape_choice`, `choose_spy_escape`, `get_artifact_choice`, `choose_artifact_player` (existing `resolve_city_capture` is the captured-city dispatch). |
| `src/civ_mcp/drex/candidates.py` (modify) | Categories `CAPTURED_CITY`, `SPY_ESCAPE`, `ARTIFACT`; kinds `RESOLVE_CAPTURED_CITY`, `CHOOSE_ESCAPE_ROUTE`, `CHOOSE_ARTIFACT_PLAYER`; params and ids. |
| `src/civ_mcp/drex/observation.py` (modify) | `DecisionInputs.captured_city`, `.spy_escape`, `.artifact`. |
| `src/civ_mcp/drex/live.py`, `points.py`, `enumerate.py`, `executor.py`, `refresh.py`, `spectate.py`, `scheduler.py`, `cli.py` (modify) | Inputs, questions, candidates, dispatch/precheck/verify, refresh rules, camera focus, blocker sets and order, probes. |
| Tests: `tests/drex/test_drex_captured_city.py`, `test_drex_spy_escape.py`, `test_drex_artifact.py` (create); `drex_fixtures.py`, `drex_fakes.py`, `test_drex_refresh.py`, `test_drex_scheduler.py`, `test_drex_runner.py`, `test_blocker_coverage.py` (modify). |
| `docs/drex.md` (modify) | Supported decisions rows, scheduler order, blocker paragraph, Limitations. |

Task order: 1 → 2 → 3 → 4.

---

### Task 1: Captured and rebelled city decision

**Files:**
- Modify: `src/civ_mcp/lua/drex_queries.py`, `src/civ_mcp/game_state.py`, `src/civ_mcp/drex/candidates.py`, `src/civ_mcp/drex/observation.py`, `src/civ_mcp/drex/live.py`, `src/civ_mcp/drex/points.py`, `src/civ_mcp/drex/enumerate.py`, `src/civ_mcp/drex/executor.py`, `src/civ_mcp/drex/refresh.py`, `src/civ_mcp/drex/spectate.py`, `src/civ_mcp/drex/scheduler.py`, `src/civ_mcp/drex/cli.py`
- Modify: `tests/drex/drex_fixtures.py`, `tests/drex/drex_fakes.py`, `tests/drex/test_drex_refresh.py`
- Test: `tests/drex/test_drex_captured_city.py` (create)

**Interfaces:**
- `drex_queries.py`:
  - `CAPTURE_ACTIONS = ("keep", "raze", "liberate_founder", "liberate_previous", "reject")` — the vocabulary `build_resolve_city_capture(action)` accepts.
  - `@dataclass class CapturedCity: city_id: int; name: str; x: int; y: int; population: int; districts: int; source: str; original_owner: str; previous_owner: str; options: list[str]` (`source` is `"captured"` or `"rebelled"`; `options` ⊆ `CAPTURE_ACTIONS`, in that order).
  - `build_captured_city_query() -> str`; `parse_captured_city(lines) -> CapturedCity | None`.
- `game_state.py`: `async get_captured_city() -> CapturedCity | None` (InGame).
- `candidates.py`: `DecisionCategory.CAPTURED_CITY = "captured_city"`; `ActionKind.RESOLVE_CAPTURED_CITY = "resolve_captured_city"`; `@dataclass(frozen=True) class CapturedCityParams: city_id: int; city_name: str; action: str`; id `captured:<city_id>:<action>`.
- `enumerate.py`: `captured_city_candidates(city: CapturedCity) -> list[Candidate]`.
- `scheduler.py`: `CAPTURED_CITY_BLOCKERS = frozenset({"ENDTURN_BLOCKING_CONSIDER_RAZE_CITY", "ENDTURN_BLOCKING_CONSIDER_DISLOYAL_CITY"})`; `PROMPT_BLOCKERS: tuple[tuple[frozenset[str], DecisionCategory], ...]` (Task 2 and 3 append to it); prompt categories are keyed by category (`key_for`), limited to 4 decisions per turn, not resolved by `note` (repeat while the blocker stands), and their standing blockers are dismissed by `stale_blockers` once the key is closed.
- `executor.py`: dispatch `("resolve_city_capture", (action,))`; precheck via `self._known.captured_city` else `gs.get_captured_city()`; verify `raw.startswith(f"{action.upper()}|")`.

- [ ] **Step 1: Fixtures and fake**

Append to `tests/drex/drex_fixtures.py`:

```python
def captured_city(options=("keep", "raze", "liberate_founder")):
    from civ_mcp.lua.drex_queries import CapturedCity

    return CapturedCity(
        city_id=65540,
        name="Antium",
        x=14,
        y=9,
        population=4,
        districts=1,
        source="captured",
        original_owner="Greece",
        previous_owner="Greece",
        options=list(options),
    )
```

In `tests/drex/drex_fakes.py`, add to `FakeGame.__init__` (after `self.trade_destinations`):

```python
        self.captured = None  # Phase 4: pending captured/rebelled city
```

and the methods (after `get_city_attack_targets`):

```python
    async def get_captured_city(self):
        self.query_counts["get_captured_city"] += 1
        self.conn.roundtrips += 1
        return copy.deepcopy(self.captured)
```

and (after `city_attack`):

```python
    async def resolve_city_capture(self, action):
        fail = self._record("resolve_city_capture", action)
        if self.captured is None:
            return "Error: NO_PENDING_CITY|No rebelled or captured city pending decision"
        if action not in self.captured.options:
            return f"Error: CANNOT_{action.upper()}|Cannot {action} {self.captured.name}"
        name, cid = self.captured.name, self.captured.city_id
        self.captured = None
        from civ_mcp.drex.scheduler import CAPTURED_CITY_BLOCKERS

        self.extra_blockers = [
            b for b in self.extra_blockers if b[0] not in CAPTURED_CITY_BLOCKERS
        ]
        self._after(fail)
        return f"{action.upper()}|{name} (pop 4, id:{cid}, captured)"
```

- [ ] **Step 2: Write the failing tests**

Create `tests/drex/test_drex_captured_city.py`:

```python
"""Captured or rebelled city: keep, raze, liberate or reject (Drex decides)."""

import asyncio

import drex_fixtures as fx
from drex_fakes import FakeGame

from civ_mcp.drex.candidates import ActionKind, DecisionCategory
from civ_mcp.drex.enumerate import captured_city_candidates
from civ_mcp.drex.executor import Executor, OutcomeStatus
from civ_mcp.drex.live import LiveObserver
from civ_mcp.drex.observation import DecisionMemory, DecisionSpec
from civ_mcp.drex.points import build_decision_point
from civ_mcp.drex.scheduler import (
    CAPTURED_CITY_BLOCKERS,
    PHASE_LATER_BLOCKERS,
    SUPPORTED_BLOCKERS,
    Scheduler,
    TurnLedger,
)
from civ_mcp.lua.drex_queries import (
    build_captured_city_query,
    parse_captured_city,
)

RAZE = "ENDTURN_BLOCKING_CONSIDER_RAZE_CITY"


async def _no_sleep(_):
    return None


def _point(obs, core):
    spec = DecisionSpec(DecisionCategory.CAPTURED_CITY, "empire")
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
    return point, inputs


def test_parse_captured_city_reads_city_and_engine_options():
    lines = [
        "CAPTURED|65540|Antium|14|9|4|1|captured|Greece|Greece",
        "OPTION|keep",
        "OPTION|liberate_founder",
        "OPTION|raze",
    ]
    c = parse_captured_city(lines)
    assert c.city_id == 65540 and c.name == "Antium" and (c.x, c.y) == (14, 9)
    assert c.population == 4 and c.districts == 1 and c.source == "captured"
    assert c.original_owner == "Greece"
    assert c.options == ["keep", "raze", "liberate_founder"]  # canonical order


def test_parse_captured_city_none_without_pending_city():
    assert parse_captured_city(["NO_PENDING_CITY"]) is None
    assert parse_captured_city([]) is None


def test_captured_city_query_checks_every_directive_the_popup_offers():
    lua = build_captured_city_query()
    for d in ("KEEP", "RAZE", "LIBERATE_FOUNDER", "LIBERATE_PREVIOUS_OWNER", "REJECT"):
        assert f"CityDestroyDirectives.{d}" in lua
    assert "GetNextRebelledCity" in lua and "GetNextCapturedCity" in lua
    assert "CanStartCommand" in lua and "RequestCommand" not in lua


def test_captured_city_candidates_one_per_engine_option():
    cands = captured_city_candidates(fx.captured_city())
    assert [c.params.action for c in cands] == ["keep", "raze", "liberate_founder"]
    assert all(c.kind is ActionKind.RESOLVE_CAPTURED_CITY for c in cands)
    lib = cands[2]
    assert lib.candidate_id == "captured:65540:liberate_founder"
    assert "Greece" in lib.label and "Antium" in lib.label
    assert lib.facts["population"] == 4 and lib.facts["source"] == "captured"


def test_blockers_are_supported_not_phase_later():
    assert CAPTURED_CITY_BLOCKERS <= SUPPORTED_BLOCKERS
    assert not (CAPTURED_CITY_BLOCKERS & PHASE_LATER_BLOCKERS)


def test_scheduler_offers_captured_city_first_for_both_blockers():
    for b in sorted(CAPTURED_CITY_BLOCKERS):
        game = FakeGame()
        game.captured = fx.captured_city()
        game.extra_blockers = [(b, "Consider city")]
        core = asyncio.run(LiveObserver(game).core())
        step = Scheduler().next(core, TurnLedger(turn=5))
        assert step.category is DecisionCategory.CAPTURED_CITY, b
        assert step.entity == "empire"


def test_resolve_dispatches_action_and_confirms_from_dispatch():
    game = FakeGame()
    game.captured = fx.captured_city()
    game.extra_blockers = [(RAZE, "x")]
    obs = LiveObserver(game)
    core = asyncio.run(obs.core())
    point, inputs = _point(obs, core)
    cand = next(c for c in point.candidates if c.params.action == "raze")
    outcome = asyncio.run(
        Executor(game, sleep=_no_sleep).execute(
            cand, point, current_version=obs.version, turn=5, inputs=inputs
        )
    )
    assert outcome.status is OutcomeStatus.CONFIRMED
    assert game.calls == [("resolve_city_capture", ("raze",))]
    assert game.query_counts["get_captured_city"] == 1  # inputs reused


def test_single_option_is_forced():
    game = FakeGame()
    game.captured = fx.captured_city(options=("keep",))
    game.extra_blockers = [(RAZE, "x")]
    obs = LiveObserver(game)
    core = asyncio.run(obs.core())
    point, _ = _point(obs, core)
    assert [c.params.action for c in point.candidates] == ["keep"]
    assert point.forced_rule == "forced_single_candidate"


def test_precheck_rejects_when_a_different_city_is_pending():
    game = FakeGame()
    game.captured = fx.captured_city()
    game.extra_blockers = [(RAZE, "x")]
    obs = LiveObserver(game)
    core = asyncio.run(obs.core())
    point, inputs = _point(obs, core)
    cand = next(c for c in point.candidates if c.params.action == "raze")
    other = fx.captured_city()
    other.city_id, other.name = 65541, "Ostia"
    game.captured = other
    outcome = asyncio.run(
        Executor(game, sleep=_no_sleep).execute(
            cand, point, current_version="stale", turn=5, inputs=None
        )
    )
    assert outcome.status is OutcomeStatus.REJECTED and game.calls == []
    assert "different_city" in outcome.reason


def test_precheck_rejects_an_action_the_engine_withdrew():
    game = FakeGame()
    game.captured = fx.captured_city()
    game.extra_blockers = [(RAZE, "x")]
    obs = LiveObserver(game)
    core = asyncio.run(obs.core())
    point, _ = _point(obs, core)
    cand = next(c for c in point.candidates if c.params.action == "raze")
    game.captured = fx.captured_city(options=("keep",))
    outcome = asyncio.run(
        Executor(game, sleep=_no_sleep).execute(
            cand, point, current_version="stale", turn=5, inputs=None
        )
    )
    assert outcome.status is OutcomeStatus.REJECTED and game.calls == []


def test_second_pending_city_is_decided_in_the_same_turn():
    game = FakeGame()
    game.captured = fx.captured_city()
    game.extra_blockers = [(RAZE, "x")]
    game.sticky_blockers = {RAZE}  # engine still flags: another city is pending
    obs = LiveObserver(game)
    core = asyncio.run(obs.core())
    s = Scheduler()
    ledger = TurnLedger(turn=5)
    step = s.next(core, ledger)
    point, inputs = _point(obs, core)
    cand = point.candidates[0]
    outcome = asyncio.run(
        Executor(game, sleep=_no_sleep).execute(
            cand, point, current_version=obs.version, turn=5, inputs=inputs
        )
    )
    s.note(ledger, step, cand.kind, outcome, cand.candidate_id)
    again = s.next(core, ledger)
    assert again.category is DecisionCategory.CAPTURED_CITY


def test_standing_blocker_with_nothing_pending_is_dismissed(tmp_path):
    from test_drex_runner import PreferSelector, _fake_end_turn, _records

    from civ_mcp.drex.decision_log import DecisionLog
    from civ_mcp.drex.runner import RunConfig, Runner

    game = FakeGame()
    game.captured = fx.captured_city()
    game.extra_blockers = [(RAZE, "Consider city")]
    game.sticky_blockers = {RAZE}
    log = DecisionLog(tmp_path / "run.jsonl", run_id="t", secrets=[])
    runner = Runner(
        game,
        PreferSelector(prefixes=("captured:65540:keep", "skip:", "research:", "produce:")),
        log,
        RunConfig(turns=1),
        end_turn=_fake_end_turn(game),
    )
    runner._sleep = _no_sleep
    runner.max_loop_iterations = 80
    result = asyncio.run(runner.run())
    assert result.stop_reason == "turn_budget_reached"
    assert ("resolve_city_capture", ("keep",)) in game.calls
    recs = _records(tmp_path)
    assert not [r for r in recs if r["type"] == "unsupported_blocker"]
    hk = [
        r
        for r in recs
        if r["type"] == "housekeeping" and r["action"] == "blocker_dismissed"
    ]
    assert hk and RAZE in hk[0]["blockers"]
    decided = [
        r for r in recs if r["type"] == "decision" and r["category"] == "captured_city"
    ]
    assert decided and decided[0]["chosen"]["candidate_id"] == "captured:65540:keep"
```

Note: the `decision` record field names must match what `DecisionLog` writes; check `test_drex_runner.py` for the key that holds the chosen candidate (it reads `r["chosen"]` or `r["decision"]["candidate_id"]`) and use that spelling.

In `tests/drex/test_drex_refresh.py` add:

```python
def test_captured_city_refreshes_cities_units_overview():
    assert refresh_parts(ActionKind.RESOLVE_CAPTURED_CITY) == frozenset(
        {"cities", "units", "overview", "blockers", "popup"}
    )
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/drex/test_drex_captured_city.py tests/drex/test_drex_refresh.py -q 2>&1 | tail -5`
Expected: FAIL / ERROR with `ImportError` (`captured_city_candidates`, `CAPTURED_CITY_BLOCKERS`, `build_captured_city_query`) and `AttributeError: RESOLVE_CAPTURED_CITY`.

- [ ] **Step 4: Lua query and parser**

Append to `src/civ_mcp/lua/drex_queries.py`:

```python
# --------------------------------------------------------------- Phase 4 prompts

CAPTURE_ACTIONS = ("keep", "raze", "liberate_founder", "liberate_previous", "reject")
_CAPTURE_DIRECTIVES = {
    "keep": "KEEP",
    "raze": "RAZE",
    "liberate_founder": "LIBERATE_FOUNDER",
    "liberate_previous": "LIBERATE_PREVIOUS_OWNER",
    "reject": "REJECT",
}

_LUA_CIV_NAME = """
local function civName(pid)
  if pid == nil or pid < 0 then return "" end
  local cfg = PlayerConfigurations[pid]
  if cfg == nil then return "" end
  local ok, s = pcall(function() return Locale.Lookup(cfg:GetCivilizationShortDescription()) end)
  return (ok and s) or ""
end
"""


@dataclass
class CapturedCity:
    """The city the engine wants a keep/raze/liberate decision for."""

    city_id: int
    name: str
    x: int
    y: int
    population: int
    districts: int
    source: str  # "captured" (conquest) or "rebelled" (loyalty flip)
    original_owner: str  # civ short name, "" if unknown
    previous_owner: str
    options: list[str]  # subset of CAPTURE_ACTIONS the engine allows right now


def build_captured_city_query() -> str:
    """InGame: the pending captured/rebelled city and the directives the
    engine accepts for it (the same ``CanStartCommand`` checks RazeCity.lua
    makes before showing each button)."""
    checks = "\n".join(
        f"do local p = {{}} p[UnitOperationTypes.PARAM_FLAGS] = CityDestroyDirectives.{d}\n"
        f"  local ok, can = pcall(function() return CityManager.CanStartCommand(city, CityCommandTypes.DESTROY, p) end)\n"
        f'  if ok and can then print("OPTION|{a}") end end'
        for a, d in _CAPTURE_DIRECTIVES.items()
    )
    return f"""
local me = Game.GetLocalPlayer()
local player = Players[me]
local city = player:GetCities():GetNextRebelledCity()
local source = "rebelled"
if city == nil then city = player:GetCities():GetNextCapturedCity() source = "captured" end
if city == nil then print("NO_PENDING_CITY"); print("{SENTINEL}"); return end
{_LUA_CIV_NAME}
local orig, prev, nd = -1, -1, 0
pcall(function() orig = city:GetOriginalOwner() end)
pcall(function() prev = city:GetOwnerBeforeOccupation() end)
pcall(function() nd = city:GetDistricts():GetNumZonedDistrictsRequiringPopulation() end)
print("CAPTURED|" .. (city:GetID() + me * 65536) .. "|" .. Locale.Lookup(city:GetName()) .. "|" .. city:GetX() .. "|" .. city:GetY() .. "|" .. city:GetPopulation() .. "|" .. nd .. "|" .. source .. "|" .. civName(orig) .. "|" .. civName(prev))
{checks}
print("{SENTINEL}")
"""


def parse_captured_city(lines: list[str]) -> CapturedCity | None:
    city: CapturedCity | None = None
    offered: set[str] = set()
    for line in lines:
        if line.startswith("CAPTURED|"):
            p = line.split("|")
            if len(p) < 10:
                continue
            city = CapturedCity(
                int(p[1]),
                p[2],
                int(p[3]),
                int(p[4]),
                int(p[5]),
                int(p[6]),
                p[7],
                p[8],
                p[9],
                [],
            )
        elif line.startswith("OPTION|"):
            offered.add(line.split("|", 1)[1].strip())
    if city is None:
        return None
    city.options = [a for a in CAPTURE_ACTIONS if a in offered]
    return city
```

In `src/civ_mcp/game_state.py`, after `get_city_attack_targets`:

```python
    async def get_captured_city(self) -> lq_drex.CapturedCity | None:
        """InGame: the captured/rebelled city awaiting keep/raze/liberate, with
        the directives the engine accepts; None when nothing is pending."""
        lines = await self.conn.execute_write(lq_drex.build_captured_city_query())
        return lq_drex.parse_captured_city(lines)
```

- [ ] **Step 5: Candidates, inputs, enumeration, question**

`src/civ_mcp/drex/candidates.py`:
- `DecisionCategory`: add `CAPTURED_CITY = "captured_city"`.
- `ActionKind`: add `RESOLVE_CAPTURED_CITY = "resolve_captured_city"`.
- After `TradeRouteParams`:

```python
@dataclass(frozen=True)
class CapturedCityParams:
    city_id: int
    city_name: str
    action: str  # one of lua.drex_queries.CAPTURE_ACTIONS
```

- Add `| CapturedCityParams` to `ActionParams`; `ActionKind.RESOLVE_CAPTURED_CITY: CapturedCityParams` to `PARAMS_FOR_KIND`; in `candidate_id_for` before the final `raise`:

```python
        case ActionKind.RESOLVE_CAPTURED_CITY, CapturedCityParams(city_id=c, action=a):
            return f"captured:{c}:{a}"
```

`src/civ_mcp/drex/observation.py`, `DecisionInputs`: add

```python
    captured_city: Any = None  # lua.drex_queries.CapturedCity
```

`src/civ_mcp/drex/enumerate.py` (import `CapturedCityParams`; add before `trade_route_candidates`):

```python
_CAPTURE_LABELS = {
    "keep": "Keep {name} as our city",
    "raze": "Raze {name} (burn it down over the coming turns)",
    "liberate_founder": "Liberate {name}: return it to its founder{orig}",
    "liberate_previous": "Liberate {name}: return it to its previous owner{prev}",
    "reject": "Reject {name} (refuse to take the city)",
}


def captured_city_candidates(city: Any) -> list[Candidate]:
    """One candidate per directive the engine accepts for the pending city
    (lua.drex_queries.CapturedCity.options), in canonical order."""
    orig = f" ({city.original_owner})" if city.original_owner else ""
    prev = f" ({city.previous_owner})" if city.previous_owner else ""
    return [
        Candidate.create(
            ActionKind.RESOLVE_CAPTURED_CITY,
            CapturedCityParams(city.city_id, city.name, action),
            label=_CAPTURE_LABELS[action].format(name=city.name, orig=orig, prev=prev),
            facts={
                "population": city.population,
                "districts": city.districts,
                "source": city.source,
                "founder": city.original_owner or None,
                "previous_owner": city.previous_owner or None,
            },
        )
        for action in city.options
        if action in _CAPTURE_LABELS
    ]
```

`src/civ_mcp/drex/points.py`: import `captured_city_candidates`; `QUESTIONS[DecisionCategory.CAPTURED_CITY] = "What should the empire do with this captured city?"`; in `_enumerate` before the final `return [], [Exclusion(...)]`:

```python
    if cat is DecisionCategory.CAPTURED_CITY and inputs.captured_city is not None:
        return captured_city_candidates(inputs.captured_city), [], entity, inputs
```

`src/civ_mcp/drex/live.py`, in `inputs()`:

```python
            case DecisionCategory.CAPTURED_CITY:
                return DecisionInputs(captured_city=await gs.get_captured_city())
```

- [ ] **Step 6: Executor, refresh, spectator**

`src/civ_mcp/drex/executor.py` (import `CapturedCityParams`):
- `dispatch_call`: `case ActionKind.RESOLVE_CAPTURED_CITY, CapturedCityParams(): return DispatchCall("resolve_city_capture", (p.action,))`
- `_precheck`:

```python
            case ActionKind.RESOLVE_CAPTURED_CITY:
                known = self._known.captured_city if self._known is not None else None
                city = known if known is not None else await gs.get_captured_city()
                if city is None:
                    return _no("no_city_pending")
                if city.city_id != p.city_id:
                    return _no("different_city_pending")
                if p.action not in city.options:
                    return _no("action_not_available")
                return _ok()
```

- `_verify`:

```python
            case ActionKind.RESOLVE_CAPTURED_CITY:
                if raw.startswith(f"{p.action.upper()}|"):
                    return confirmed("capture_resolved_from_dispatch")
                if _game_error(raw):
                    return self._unconfirmed(raw, "capture_refused")

                async def gone():
                    city = await gs.get_captured_city()
                    return city is None or city.city_id != p.city_id

                if await self._poll(gone):
                    return confirmed("captured_city_no_longer_pending")
                return self._unconfirmed(raw, "capture_not_observed")
```

Note `self._known` is a `DecisionInputs`: the `captured_city` field exists after Step 5. When `inputs` was passed with the current version, `known` is the same object the candidates came from (1 round trip saved), which `test_resolve_dispatches_action_and_confirms_from_dispatch` asserts via `query_counts`.

`src/civ_mcp/drex/refresh.py`: `ActionKind.RESOLVE_CAPTURED_CITY: frozenset({"cities", "units", "overview"})`.

`src/civ_mcp/drex/spectate.py`, `focus_point` (import `CapturedCityParams`):

```python
        case ActionKind.RESOLVE_CAPTURED_CITY, CapturedCityParams():
            city = core.city(p.city_id)
            if city is None:
                return None
            return city.x, city.y, candidate.label
```

- [ ] **Step 7: Scheduler**

`src/civ_mcp/drex/scheduler.py`:
- Constants (after `GOVERNOR_BLOCKERS`):

```python
CAPTURED_CITY_BLOCKERS = frozenset(
    {
        "ENDTURN_BLOCKING_CONSIDER_RAZE_CITY",
        "ENDTURN_BLOCKING_CONSIDER_DISLOYAL_CITY",
    }
)
# Modal prompts: decided first, repeat while the blocker stands (bounded), and
# a blocker that stands once nothing is pending is dismissed as housekeeping.
PROMPT_BLOCKERS: tuple[tuple[frozenset[str], DecisionCategory], ...] = (
    (CAPTURED_CITY_BLOCKERS, DecisionCategory.CAPTURED_CITY),
)
PROMPT_CATEGORIES = frozenset(c for _, c in PROMPT_BLOCKERS)
```

- `SUPPORTED_BLOCKERS`: add `*CAPTURED_CITY_BLOCKERS`; remove both names from `PHASE_LATER_BLOCKERS`.
- `key_for`: add `DecisionCategory.CAPTURED_CITY` to the category-keyed tuple.
- `_limit`: `case DecisionCategory.CAPTURED_CITY: return 4`.
- `_next_proactive`, right after the `budget_hit` check:

```python
        blockers = core.blocker_types()
        for group, category in PROMPT_BLOCKERS:
            if blockers & group:
                spec = DecisionSpec(category, "empire")
                if self._open(ledger, spec):
                    return spec
```

(and remove the later duplicate `blockers = core.blocker_types()` line).
- `note`: add `DecisionCategory.CAPTURED_CITY,  # repeats while the blocker stands` to the tuple of categories not resolved on success.
- `stale_blockers`, before the final `return`:

```python
        for group, category in PROMPT_BLOCKERS:
            standing = blockers & group
            if standing and not self._open(ledger, DecisionSpec(category, "empire")):
                stale.extend(sorted(standing))
```

- `SCHEDULER_ORDER`: insert `"captured_city: when CONSIDER_RAZE_CITY / CONSIDER_DISLOYAL_CITY blocks (keep, raze, liberate, reject)",` after the deal line.

`src/civ_mcp/drex/cli.py`, `PROBE_KINDS`: `"captured_city": lambda gs: gs.get_captured_city(),`.

- [ ] **Step 8: Run the tests to verify they pass, then the suite**

Run: `uv run pytest tests/drex/test_drex_captured_city.py tests/drex/test_drex_refresh.py -q 2>&1 | tail -5`
Expected: all PASS.

Run: `uv run pytest tests -q 2>&1 | tail -3`
Expected: `532 + new` passed, 0 failed. `tests/drex/test_blocker_coverage.py` still passes (both names are classified, now as supported).

- [ ] **Step 9: Lint and commit**

```bash
uvx ruff format src/civ_mcp/lua/drex_queries.py src/civ_mcp/drex tests/drex && uvx ruff check src/civ_mcp/lua/drex_queries.py src/civ_mcp/drex tests/drex
git add src/civ_mcp/lua/drex_queries.py src/civ_mcp/game_state.py src/civ_mcp/drex/candidates.py src/civ_mcp/drex/observation.py src/civ_mcp/drex/live.py src/civ_mcp/drex/points.py src/civ_mcp/drex/enumerate.py src/civ_mcp/drex/executor.py src/civ_mcp/drex/refresh.py src/civ_mcp/drex/spectate.py src/civ_mcp/drex/scheduler.py src/civ_mcp/drex/cli.py tests/drex/drex_fixtures.py tests/drex/drex_fakes.py tests/drex/test_drex_refresh.py tests/drex/test_drex_captured_city.py
git commit -m "Captured and rebelled cities are Drex decisions (keep, raze, liberate, reject)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: Spy escape route (escape and dragnet blockers)

**Files:**
- Modify: `src/civ_mcp/lua/drex_queries.py`, `src/civ_mcp/game_state.py`, `src/civ_mcp/drex/candidates.py`, `src/civ_mcp/drex/observation.py`, `src/civ_mcp/drex/live.py`, `src/civ_mcp/drex/points.py`, `src/civ_mcp/drex/enumerate.py`, `src/civ_mcp/drex/executor.py`, `src/civ_mcp/drex/refresh.py`, `src/civ_mcp/drex/spectate.py`, `src/civ_mcp/drex/scheduler.py`, `src/civ_mcp/drex/cli.py`
- Modify: `tests/drex/drex_fixtures.py`, `tests/drex/drex_fakes.py`, `tests/drex/test_drex_refresh.py`
- Test: `tests/drex/test_drex_spy_escape.py` (create)

**Interfaces:**
- `drex_queries.py`: `ESCAPE_DISTRICTS = ("DISTRICT_AERODROME", "DISTRICT_HARBOR", "DISTRICT_COMMERCIAL_HUB", "DISTRICT_CITY_CENTER")`; `@dataclass class EscapeChoice: spy_unit_id: int; spy_name: str; city_name: str; x: int; y: int; routes: list[str]`; `build_spy_escape_options_query()`, `parse_spy_escape_options(lines) -> EscapeChoice | None`, `build_choose_spy_escape(district_type)` (prints `OK:ESCAPE_ROUTE|<spy>|<district>`).
- `game_state.py`: `get_spy_escape_choice() -> EscapeChoice | None`; `choose_spy_escape(district_type) -> str`.
- `candidates.py`: `DecisionCategory.SPY_ESCAPE = "spy_escape"`; `ActionKind.CHOOSE_ESCAPE_ROUTE = "choose_escape_route"`; `EscapeRouteParams(spy_unit_id: int, spy_name: str, district_type: str)`; id `escape:<spy_unit_id>:<district_type>`.
- `enumerate.py`: `escape_route_candidates(choice: EscapeChoice) -> list[Candidate]`.
- `scheduler.py`: `SPY_ESCAPE_BLOCKERS = frozenset({"ENDTURN_BLOCKING_SPY_CHOOSE_ESCAPE_ROUTE", "ENDTURN_BLOCKING_SPY_CHOOSE_DRAGNET_PRIORITY"})` appended to `PROMPT_BLOCKERS` with `DecisionCategory.SPY_ESCAPE`.

- [ ] **Step 1: Fixtures and fake**

Append to `tests/drex/drex_fixtures.py`:

```python
SPY_ID = 65600


def spy_escape(routes=("DISTRICT_HARBOR", "DISTRICT_CITY_CENTER")):
    from civ_mcp.lua.drex_queries import EscapeChoice

    return EscapeChoice(
        spy_unit_id=SPY_ID,
        spy_name="Artimpasa",
        city_name="Athens",
        x=20,
        y=15,
        routes=list(routes),
    )
```

`tests/drex/drex_fakes.py`: in `__init__` add `self.spy_escape = None  # Phase 4: caught spy awaiting an escape route`; methods:

```python
    async def get_spy_escape_choice(self):
        self.query_counts["get_spy_escape_choice"] += 1
        self.conn.roundtrips += 1
        return copy.deepcopy(self.spy_escape)

    async def choose_spy_escape(self, district_type):
        fail = self._record("choose_spy_escape", district_type)
        if self.spy_escape is None:
            return "Error: NO_ESCAPING_SPY"
        if district_type not in self.spy_escape.routes:
            return f"Error: ROUTE_NOT_AVAILABLE|{district_type}"
        name = self.spy_escape.spy_name
        self.spy_escape = None
        from civ_mcp.drex.scheduler import SPY_ESCAPE_BLOCKERS

        self.extra_blockers = [
            b for b in self.extra_blockers if b[0] not in SPY_ESCAPE_BLOCKERS
        ]
        self._after(fail)
        return f"ESCAPE_ROUTE|{name}|{district_type}"
```

- [ ] **Step 2: Write the failing tests**

Create `tests/drex/test_drex_spy_escape.py`:

```python
"""A caught spy's escape route (escape-route and dragnet blockers)."""

import asyncio

import drex_fixtures as fx
from drex_fakes import FakeGame

from civ_mcp.drex.candidates import ActionKind, DecisionCategory
from civ_mcp.drex.enumerate import escape_route_candidates
from civ_mcp.drex.executor import Executor, OutcomeStatus
from civ_mcp.drex.live import LiveObserver
from civ_mcp.drex.observation import DecisionMemory, DecisionSpec
from civ_mcp.drex.points import build_decision_point
from civ_mcp.drex.scheduler import (
    PHASE_LATER_BLOCKERS,
    SPY_ESCAPE_BLOCKERS,
    SUPPORTED_BLOCKERS,
    Scheduler,
    TurnLedger,
)
from civ_mcp.lua.drex_queries import (
    ESCAPE_DISTRICTS,
    build_choose_spy_escape,
    build_spy_escape_options_query,
    parse_spy_escape_options,
)

ESCAPE = "ENDTURN_BLOCKING_SPY_CHOOSE_ESCAPE_ROUTE"
DRAGNET = "ENDTURN_BLOCKING_SPY_CHOOSE_DRAGNET_PRIORITY"


async def _no_sleep(_):
    return None


def _point(obs, core):
    spec = DecisionSpec(DecisionCategory.SPY_ESCAPE, "empire")
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
    return point, inputs


def test_parse_spy_escape_options():
    lines = [
        "ESCAPE|65600|Artimpasa|Athens|20|15",
        "ROUTE|DISTRICT_CITY_CENTER",
        "ROUTE|DISTRICT_HARBOR",
    ]
    c = parse_spy_escape_options(lines)
    assert c.spy_unit_id == 65600 and c.spy_name == "Artimpasa"
    assert c.city_name == "Athens" and (c.x, c.y) == (20, 15)
    assert c.routes == ["DISTRICT_HARBOR", "DISTRICT_CITY_CENTER"]  # fastest first
    assert parse_spy_escape_options(["NO_ESCAPING_SPY"]) is None


def test_escape_query_checks_the_notification_before_asking_for_the_spy():
    lua = build_spy_escape_options_query()
    assert ESCAPE in lua and DRAGNET in lua
    assert lua.index("NotificationManager.GetList") < lua.index("GetNextEscapingSpyID")
    assert "NO_ESCAPING_SPY" in lua
    for d in ESCAPE_DISTRICTS:
        assert d in lua
    assert "SET_ESCAPE_ROUTE" not in lua  # read-only


def test_choose_escape_lua_validates_the_route_and_requests_it():
    lua = build_choose_spy_escape("DISTRICT_HARBOR")
    assert "HasDistrict" in lua and "ROUTE_NOT_AVAILABLE" in lua
    assert "PlayerOperations.SET_ESCAPE_ROUTE" in lua
    assert 'print("OK:ESCAPE_ROUTE|"' in lua
    assert "ERR:" in build_choose_spy_escape("DISTRICT_CAMPUS")  # not an escape route


def test_escape_route_candidates_one_per_route():
    cands = escape_route_candidates(fx.spy_escape())
    assert [c.params.district_type for c in cands] == [
        "DISTRICT_HARBOR",
        "DISTRICT_CITY_CENTER",
    ]
    assert all(c.kind is ActionKind.CHOOSE_ESCAPE_ROUTE for c in cands)
    assert cands[0].candidate_id == f"escape:{fx.SPY_ID}:DISTRICT_HARBOR"
    assert "Harbor" in cands[0].label and "Artimpasa" in cands[0].label
    assert cands[0].facts["city"] == "Athens"


def test_blockers_are_supported_not_phase_later():
    assert SPY_ESCAPE_BLOCKERS <= SUPPORTED_BLOCKERS
    assert not (SPY_ESCAPE_BLOCKERS & PHASE_LATER_BLOCKERS)


def test_scheduler_offers_spy_escape_for_both_blockers():
    for b in (ESCAPE, DRAGNET):
        game = FakeGame()
        game.spy_escape = fx.spy_escape()
        game.extra_blockers = [(b, "Choose escape route")]
        core = asyncio.run(LiveObserver(game).core())
        step = Scheduler().next(core, TurnLedger(turn=5))
        assert step.category is DecisionCategory.SPY_ESCAPE, b


def test_choose_escape_dispatches_and_confirms_from_dispatch():
    game = FakeGame()
    game.spy_escape = fx.spy_escape()
    game.extra_blockers = [(ESCAPE, "x")]
    obs = LiveObserver(game)
    core = asyncio.run(obs.core())
    point, inputs = _point(obs, core)
    cand = next(c for c in point.candidates if c.params.district_type == "DISTRICT_HARBOR")
    outcome = asyncio.run(
        Executor(game, sleep=_no_sleep).execute(
            cand, point, current_version=obs.version, turn=5, inputs=inputs
        )
    )
    assert outcome.status is OutcomeStatus.CONFIRMED
    assert game.calls == [("choose_spy_escape", ("DISTRICT_HARBOR",))]


def test_city_center_only_is_forced():
    game = FakeGame()
    game.spy_escape = fx.spy_escape(routes=("DISTRICT_CITY_CENTER",))
    game.extra_blockers = [(ESCAPE, "x")]
    obs = LiveObserver(game)
    core = asyncio.run(obs.core())
    point, _ = _point(obs, core)
    assert len(point.candidates) == 1
    assert point.forced_rule == "forced_single_candidate"


def test_precheck_rejects_a_route_or_spy_that_changed():
    game = FakeGame()
    game.spy_escape = fx.spy_escape()
    game.extra_blockers = [(ESCAPE, "x")]
    obs = LiveObserver(game)
    core = asyncio.run(obs.core())
    point, _ = _point(obs, core)
    cand = next(c for c in point.candidates if c.params.district_type == "DISTRICT_HARBOR")
    game.spy_escape = fx.spy_escape(routes=("DISTRICT_CITY_CENTER",))
    out = asyncio.run(
        Executor(game, sleep=_no_sleep).execute(
            cand, point, current_version="stale", turn=5, inputs=None
        )
    )
    assert out.status is OutcomeStatus.REJECTED and game.calls == []
    other = fx.spy_escape()
    other.spy_unit_id = fx.SPY_ID + 1
    game.spy_escape = other
    out = asyncio.run(
        Executor(game, sleep=_no_sleep).execute(
            cand, point, current_version="stale", turn=5, inputs=None
        )
    )
    assert out.status is OutcomeStatus.REJECTED and game.calls == []


def test_runner_resolves_the_escape_and_advances(tmp_path):
    from test_drex_runner import PreferSelector, _fake_end_turn, _records

    from civ_mcp.drex.decision_log import DecisionLog
    from civ_mcp.drex.runner import RunConfig, Runner

    game = FakeGame()
    game.spy_escape = fx.spy_escape()
    game.extra_blockers = [(DRAGNET, "Choose dragnet priority")]
    log = DecisionLog(tmp_path / "run.jsonl", run_id="t", secrets=[])
    runner = Runner(
        game,
        PreferSelector(
            prefixes=(f"escape:{fx.SPY_ID}:DISTRICT_HARBOR", "skip:", "research:", "produce:")
        ),
        log,
        RunConfig(turns=1),
        end_turn=_fake_end_turn(game),
    )
    runner._sleep = _no_sleep
    runner.max_loop_iterations = 80
    result = asyncio.run(runner.run())
    assert result.stop_reason == "turn_budget_reached"
    assert ("choose_spy_escape", ("DISTRICT_HARBOR",)) in game.calls
    assert not [r for r in _records(tmp_path) if r["type"] == "unsupported_blocker"]
```

`tests/drex/test_drex_refresh.py`:

```python
def test_escape_route_refreshes_units():
    assert refresh_parts(ActionKind.CHOOSE_ESCAPE_ROUTE) == frozenset(
        {"units", "blockers", "popup"}
    )
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/drex/test_drex_spy_escape.py tests/drex/test_drex_refresh.py -q 2>&1 | tail -5`
Expected: ImportError / AttributeError for the new names.

- [ ] **Step 4: Lua**

Append to `src/civ_mcp/lua/drex_queries.py`:

```python
ESCAPE_DISTRICTS = (  # the four routes EspionageEscape.lua offers, fastest first
    "DISTRICT_AERODROME",
    "DISTRICT_HARBOR",
    "DISTRICT_COMMERCIAL_HUB",
    "DISTRICT_CITY_CENTER",
)


@dataclass
class EscapeChoice:
    spy_unit_id: int
    spy_name: str
    city_name: str
    x: int
    y: int
    routes: list[str]  # available escape districts, fastest first


def _lua_escaping_spy() -> str:
    """Shared prefix: find the escaping spy and its city, but only after one
    of the two escape blockers' notifications is found standing —
    ``GetNextEscapingSpyID`` crashes the game when nothing is escaping."""
    return f"""
local me = Game.GetLocalPlayer()
local prompt = false
local list = NotificationManager.GetList(me)
if list then
  for _, nid in ipairs(list) do
    local e = NotificationManager.Find(me, nid)
    if e and not e:IsDismissed() then
      local bt = e:GetEndTurnBlocking()
      if bt == EndTurnBlockingTypes.ENDTURN_BLOCKING_SPY_CHOOSE_ESCAPE_ROUTE or bt == EndTurnBlockingTypes.ENDTURN_BLOCKING_SPY_CHOOSE_DRAGNET_PRIORITY then prompt = true end
    end
  end
end
if not prompt then print("NO_ESCAPING_SPY"); print("{SENTINEL}"); return end
local ok_esc, spyID = pcall(function() return Players[me]:GetDiplomacy():GetNextEscapingSpyID() end)
if not ok_esc or spyID == nil or spyID < 0 then print("NO_ESCAPING_SPY"); print("{SENTINEL}"); return end
local spy = Players[me]:GetUnits():FindID(spyID)
if spy == nil then print("ERR:SPY_NOT_FOUND"); print("{SENTINEL}"); return end
local city = Cities.GetPlotPurchaseCity(spy:GetX(), spy:GetY())
if city == nil then print("ERR:NO_CITY"); print("{SENTINEL}"); return end
local function hasRoute(d)
  if d == "DISTRICT_CITY_CENTER" then return true end
  local row = GameInfo.Districts[d]
  if row == nil then return false end
  local ok, has = pcall(function() return city:GetDistricts():HasDistrict(row.Index, true, true) end)
  return ok and has
end
"""


def build_spy_escape_options_query() -> str:
    """InGame: the caught spy and the escape districts available in its city."""
    routes = "\n".join(
        f'if hasRoute("{d}") then print("ROUTE|{d}") end' for d in ESCAPE_DISTRICTS
    )
    return f"""
{_lua_escaping_spy()}
print("ESCAPE|" .. (spy:GetID() + me * 65536) .. "|" .. Locale.Lookup(spy:GetName()) .. "|" .. Locale.Lookup(city:GetName()) .. "|" .. spy:GetX() .. "|" .. spy:GetY())
{routes}
print("{SENTINEL}")
"""


def parse_spy_escape_options(lines: list[str]) -> EscapeChoice | None:
    choice: EscapeChoice | None = None
    found: set[str] = set()
    for line in lines:
        if line.startswith("ESCAPE|"):
            p = line.split("|")
            if len(p) >= 6:
                choice = EscapeChoice(int(p[1]), p[2], p[3], int(p[4]), int(p[5]), [])
        elif line.startswith("ROUTE|"):
            found.add(line.split("|", 1)[1].strip())
    if choice is None:
        return None
    choice.routes = [d for d in ESCAPE_DISTRICTS if d in found]
    return choice


def build_choose_spy_escape(district_type: str) -> str:
    """InGame: set the caught spy's escape route (``SET_ESCAPE_ROUTE``, as the
    popup does) after re-checking the district is available."""
    if district_type not in ESCAPE_DISTRICTS:
        return f'print("ERR:NOT_AN_ESCAPE_ROUTE|{district_type}"); print("{SENTINEL}")'
    return f"""
{_lua_escaping_spy()}
if not hasRoute("{district_type}") then print("ERR:ROUTE_NOT_AVAILABLE|{district_type}"); print("{SENTINEL}"); return end
local params = {{}}
params[PlayerOperations.PARAM_DISTRICT_TYPE] = GameInfo.Districts["{district_type}"].Index
UI.RequestPlayerOperation(me, PlayerOperations.SET_ESCAPE_ROUTE, params)
pcall(function() local popup = ContextPtr:LookUpControl("/InGame/EspionageEscape") if popup then popup:SetHide(true) end end)
print("OK:ESCAPE_ROUTE|" .. Locale.Lookup(spy:GetName()) .. "|{district_type}")
print("{SENTINEL}")
"""
```

`src/civ_mcp/game_state.py`:

```python
    async def get_spy_escape_choice(self) -> lq_drex.EscapeChoice | None:
        """InGame: the caught spy and its available escape districts; None
        when no escape prompt stands (the query never asks the engine for an
        escaping spy without the prompt, which can crash the game)."""
        lines = await self.conn.execute_write(lq_drex.build_spy_escape_options_query())
        return lq_drex.parse_spy_escape_options(lines)

    async def choose_spy_escape(self, district_type: str) -> str:
        """InGame: choose the caught spy's escape route."""
        lines = await self.conn.execute_write(
            lq_drex.build_choose_spy_escape(district_type)
        )
        return _action_result(lines)
```

- [ ] **Step 5: Candidates, inputs, enumeration, question, executor, refresh, spectator**

`candidates.py`: `DecisionCategory.SPY_ESCAPE = "spy_escape"`; `ActionKind.CHOOSE_ESCAPE_ROUTE = "choose_escape_route"`;

```python
@dataclass(frozen=True)
class EscapeRouteParams:
    spy_unit_id: int
    spy_name: str
    district_type: str
```

union, `PARAMS_FOR_KIND`, and id `case ActionKind.CHOOSE_ESCAPE_ROUTE, EscapeRouteParams(spy_unit_id=u, district_type=d): return f"escape:{u}:{d}"`.

`observation.py`: `spy_escape: Any = None  # lua.drex_queries.EscapeChoice`.

`enumerate.py`:

```python
def escape_route_candidates(choice: Any) -> list[Candidate]:
    """One candidate per escape district the city has (fastest first, as the
    game's popup lists them)."""
    return [
        Candidate.create(
            ActionKind.CHOOSE_ESCAPE_ROUTE,
            EscapeRouteParams(choice.spy_unit_id, choice.spy_name, d),
            label=f"{choice.spy_name} escapes through the {pretty(d.replace('DISTRICT_', ''))}",
            facts={"city": choice.city_name, "route_rank": i + 1},
        )
        for i, d in enumerate(choice.routes)
    ]
```

`points.py`: `QUESTIONS[DecisionCategory.SPY_ESCAPE] = "Which escape route should the caught spy take?"`; branch `if cat is DecisionCategory.SPY_ESCAPE and inputs.spy_escape is not None: return escape_route_candidates(inputs.spy_escape), [], entity, inputs`.

`live.py`: `case DecisionCategory.SPY_ESCAPE: return DecisionInputs(spy_escape=await gs.get_spy_escape_choice())`.

`executor.py`: dispatch `DispatchCall("choose_spy_escape", (p.district_type,))`; precheck:

```python
            case ActionKind.CHOOSE_ESCAPE_ROUTE:
                known = self._known.spy_escape if self._known is not None else None
                esc = known if known is not None else await gs.get_spy_escape_choice()
                if esc is None:
                    return _no("no_spy_escaping")
                if esc.spy_unit_id != p.spy_unit_id:
                    return _no("different_spy_escaping")
                if p.district_type not in esc.routes:
                    return _no("route_not_available")
                return _ok()
```

verify:

```python
            case ActionKind.CHOOSE_ESCAPE_ROUTE:
                if raw.startswith("ESCAPE_ROUTE|"):
                    return confirmed("escape_route_confirmed_from_dispatch")
                if _game_error(raw):
                    return self._unconfirmed(raw, "escape_refused")

                async def gone():
                    return (await gs.get_spy_escape_choice()) is None

                if await self._poll(gone):
                    return confirmed("spy_no_longer_escaping")
                return self._unconfirmed(raw, "escape_not_observed")
```

`refresh.py`: `ActionKind.CHOOSE_ESCAPE_ROUTE: frozenset({"units"})`.

`spectate.py`:

```python
        case ActionKind.CHOOSE_ESCAPE_ROUTE, EscapeRouteParams():
            unit = core.unit(p.spy_unit_id)
            if unit is None:
                return None
            return unit.x, unit.y, candidate.label
```

`scheduler.py`: `SPY_ESCAPE_BLOCKERS` constant; append `(SPY_ESCAPE_BLOCKERS, DecisionCategory.SPY_ESCAPE)` to `PROMPT_BLOCKERS`; add `*SPY_ESCAPE_BLOCKERS` to `SUPPORTED_BLOCKERS`; remove both from `PHASE_LATER_BLOCKERS`; `key_for` + `_limit` (4) + `note` non-resolving for `SPY_ESCAPE`; `SCHEDULER_ORDER` line `"spy_escape: when SPY_CHOOSE_ESCAPE_ROUTE / SPY_CHOOSE_DRAGNET_PRIORITY blocks (route per available district)",` after the captured_city line.

`cli.py`: `"spy_escape": lambda gs: gs.get_spy_escape_choice(),`.

- [ ] **Step 6: Run the tests, then the suite**

Run: `uv run pytest tests/drex/test_drex_spy_escape.py tests/drex/test_drex_refresh.py -q 2>&1 | tail -5`
Expected: PASS.

Run: `uv run pytest tests -q 2>&1 | tail -3`
Expected: all pass. `tests/drex/test_drex_end_turn.py` still passes (it drives the legacy end turn, untouched).

- [ ] **Step 7: Lint and commit**

```bash
uvx ruff format src/civ_mcp/lua/drex_queries.py src/civ_mcp/drex tests/drex && uvx ruff check src/civ_mcp/lua/drex_queries.py src/civ_mcp/drex tests/drex
git add src/civ_mcp/lua/drex_queries.py src/civ_mcp/game_state.py src/civ_mcp/drex/candidates.py src/civ_mcp/drex/observation.py src/civ_mcp/drex/live.py src/civ_mcp/drex/points.py src/civ_mcp/drex/enumerate.py src/civ_mcp/drex/executor.py src/civ_mcp/drex/refresh.py src/civ_mcp/drex/spectate.py src/civ_mcp/drex/scheduler.py src/civ_mcp/drex/cli.py tests/drex/drex_fixtures.py tests/drex/drex_fakes.py tests/drex/test_drex_refresh.py tests/drex/test_drex_spy_escape.py
git commit -m "A caught spy's escape route is a Drex decision (escape and dragnet blockers)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: Artifact player choice

**Files:**
- Modify: `src/civ_mcp/lua/drex_queries.py`, `src/civ_mcp/game_state.py`, `src/civ_mcp/drex/candidates.py`, `src/civ_mcp/drex/observation.py`, `src/civ_mcp/drex/live.py`, `src/civ_mcp/drex/points.py`, `src/civ_mcp/drex/enumerate.py`, `src/civ_mcp/drex/executor.py`, `src/civ_mcp/drex/refresh.py`, `src/civ_mcp/drex/spectate.py`, `src/civ_mcp/drex/scheduler.py`, `src/civ_mcp/drex/cli.py`
- Modify: `tests/drex/drex_fixtures.py`, `tests/drex/drex_fakes.py`, `tests/drex/test_drex_refresh.py`, `tests/drex/test_drex_scheduler.py` (line 188 test), `tests/drex/test_drex_runner.py` (line 238 test)
- Test: `tests/drex/test_drex_artifact.py` (create)

**Interfaces:**
- `drex_queries.py`: `@dataclass class ArtifactChoice: unit_id: int; unit_name: str; x: int; y: int; kind: str; era: str; players: list[tuple[int, str, str]]` (`(player_id, civ name, "acting"|"target")`); `ARTIFACT_KINDS = {0: "unknown origin", 1: "tribal village", 2: "barbarian camp", 3: "battle site", 4: "shipwreck", 5: "heroic relic"}`; `build_artifact_choice_query()`, `parse_artifact_choice(lines)`, `build_choose_artifact_player(player_id)` (prints `OK:ARTIFACT_CHOSEN|<civ>`).
- `game_state.py`: `get_artifact_choice() -> ArtifactChoice | None`; `choose_artifact_player(player_id) -> str`.
- `candidates.py`: `DecisionCategory.ARTIFACT = "artifact"`; `ActionKind.CHOOSE_ARTIFACT_PLAYER = "choose_artifact_player"`; `ArtifactParams(archaeologist_unit_id: int, player_id: int, player_name: str)`; id `artifact:<unit>:<player_id>`.
- `enumerate.py`: `artifact_candidates(choice: ArtifactChoice) -> list[Candidate]`.
- `scheduler.py`: `ARTIFACT_BLOCKER = "ENDTURN_BLOCKING_ARTIFACT"`, appended to `PROMPT_BLOCKERS` as `(frozenset({ARTIFACT_BLOCKER}), DecisionCategory.ARTIFACT)`.

- [ ] **Step 1: Fixtures and fake**

`tests/drex/drex_fixtures.py`:

```python
ARCHAEOLOGIST_ID = 65700


def artifact_choice(choice: bool = True):
    from civ_mcp.lua.drex_queries import ArtifactChoice

    players = [(0, "Rome", "acting")]
    if choice:
        players.append((3, "Greece", "target"))
    return ArtifactChoice(
        unit_id=ARCHAEOLOGIST_ID,
        unit_name="Archaeologist",
        x=18,
        y=11,
        kind="battle site" if choice else "barbarian camp",
        era="Classical Era",
        players=players,
    )
```

`tests/drex/drex_fakes.py`: `self.artifact = None  # Phase 4: artifact awaiting a player choice`;

```python
    async def get_artifact_choice(self):
        self.query_counts["get_artifact_choice"] += 1
        self.conn.roundtrips += 1
        return copy.deepcopy(self.artifact)

    async def choose_artifact_player(self, player_id):
        fail = self._record("choose_artifact_player", player_id)
        if self.artifact is None:
            return "Error: NO_ARTIFACT"
        names = {pid: name for pid, name, _ in self.artifact.players}
        if player_id not in names:
            return f"Error: PLAYER_NOT_OFFERED|{player_id}"
        self.artifact = None
        self.extra_blockers = [
            b for b in self.extra_blockers if b[0] != "ENDTURN_BLOCKING_ARTIFACT"
        ]
        self._after(fail)
        return f"ARTIFACT_CHOSEN|{names[player_id]}"
```

- [ ] **Step 2: Write the failing tests**

Create `tests/drex/test_drex_artifact.py`:

```python
"""Which civilization an excavated artifact is credited to."""

import asyncio

import drex_fixtures as fx
from drex_fakes import FakeGame

from civ_mcp.drex.candidates import ActionKind, DecisionCategory
from civ_mcp.drex.enumerate import artifact_candidates
from civ_mcp.drex.executor import Executor, OutcomeStatus
from civ_mcp.drex.live import LiveObserver
from civ_mcp.drex.observation import DecisionMemory, DecisionSpec
from civ_mcp.drex.points import build_decision_point
from civ_mcp.drex.scheduler import (
    ARTIFACT_BLOCKER,
    PHASE_LATER_BLOCKERS,
    SUPPORTED_BLOCKERS,
    Scheduler,
    TurnLedger,
)
from civ_mcp.lua.drex_queries import (
    build_artifact_choice_query,
    build_choose_artifact_player,
    parse_artifact_choice,
)


async def _no_sleep(_):
    return None


def _point(obs, core):
    spec = DecisionSpec(DecisionCategory.ARTIFACT, "empire")
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
    return point, inputs


def test_parse_artifact_choice_with_two_claimants():
    lines = [
        "ARTIFACT|65700|Archaeologist|18|11|3|Classical Era",
        "PLAYER|0|Rome|acting",
        "PLAYER|3|Greece|target",
    ]
    a = parse_artifact_choice(lines)
    assert a.unit_id == 65700 and (a.x, a.y) == (18, 11)
    assert a.kind == "battle site" and a.era == "Classical Era"
    assert a.players == [(0, "Rome", "acting"), (3, "Greece", "target")]


def test_parse_artifact_choice_without_choice_and_without_artifact():
    a = parse_artifact_choice(
        ["ARTIFACT|65700|Archaeologist|18|11|2|Ancient Era", "PLAYER|63|Barbarians|acting"]
    )
    assert a.kind == "barbarian camp" and a.players == [(63, "Barbarians", "acting")]
    assert parse_artifact_choice(["NO_ARTIFACT"]) is None


def test_artifact_lua_uses_the_popups_api():
    lua = build_artifact_choice_query()
    for s in (
        "GetNextExtractingArchaeologist",
        "GetArchaeology",
        "GetArtifactIndex",
        "Game.GetArtifactByIndex",
        "NO_ARTIFACT",
    ):
        assert s in lua
    assert "CHOOSE_ARTIFACT_PLAYER" not in lua  # read-only
    choose = build_choose_artifact_player(3)
    assert "PlayerOperations.CHOOSE_ARTIFACT_PLAYER" in choose
    assert "PARAM_PLAYER_ONE" in choose and "PLAYER_NOT_OFFERED" in choose
    assert 'print("OK:ARTIFACT_CHOSEN|"' in choose


def test_artifact_candidates_one_per_claimant():
    cands = artifact_candidates(fx.artifact_choice())
    assert [c.params.player_id for c in cands] == [0, 3]
    assert all(c.kind is ActionKind.CHOOSE_ARTIFACT_PLAYER for c in cands)
    assert cands[1].candidate_id == f"artifact:{fx.ARCHAEOLOGIST_ID}:3"
    assert "Greece" in cands[1].label
    assert cands[1].facts["role"] == "target" and cands[1].facts["origin"] == "battle site"


def test_blocker_is_supported_not_phase_later():
    assert ARTIFACT_BLOCKER in SUPPORTED_BLOCKERS
    assert ARTIFACT_BLOCKER not in PHASE_LATER_BLOCKERS


def test_scheduler_offers_artifact_when_the_blocker_stands():
    game = FakeGame()
    game.artifact = fx.artifact_choice()
    game.extra_blockers = [(ARTIFACT_BLOCKER, "Choose artifact")]
    core = asyncio.run(LiveObserver(game).core())
    step = Scheduler().next(core, TurnLedger(turn=5))
    assert step.category is DecisionCategory.ARTIFACT


def test_choose_artifact_dispatches_and_confirms_from_dispatch():
    game = FakeGame()
    game.artifact = fx.artifact_choice()
    game.extra_blockers = [(ARTIFACT_BLOCKER, "x")]
    obs = LiveObserver(game)
    core = asyncio.run(obs.core())
    point, inputs = _point(obs, core)
    cand = next(c for c in point.candidates if c.params.player_id == 3)
    outcome = asyncio.run(
        Executor(game, sleep=_no_sleep).execute(
            cand, point, current_version=obs.version, turn=5, inputs=inputs
        )
    )
    assert outcome.status is OutcomeStatus.CONFIRMED
    assert game.calls == [("choose_artifact_player", (3,))]


def test_no_choice_artifact_is_forced_single_candidate():
    game = FakeGame()
    game.artifact = fx.artifact_choice(choice=False)
    game.extra_blockers = [(ARTIFACT_BLOCKER, "x")]
    obs = LiveObserver(game)
    core = asyncio.run(obs.core())
    point, _ = _point(obs, core)
    assert [c.params.player_id for c in point.candidates] == [0]
    assert point.forced_rule == "forced_single_candidate"


def test_precheck_rejects_a_claimant_no_longer_offered():
    game = FakeGame()
    game.artifact = fx.artifact_choice()
    game.extra_blockers = [(ARTIFACT_BLOCKER, "x")]
    obs = LiveObserver(game)
    core = asyncio.run(obs.core())
    point, _ = _point(obs, core)
    cand = next(c for c in point.candidates if c.params.player_id == 3)
    game.artifact = fx.artifact_choice(choice=False)
    out = asyncio.run(
        Executor(game, sleep=_no_sleep).execute(
            cand, point, current_version="stale", turn=5, inputs=None
        )
    )
    assert out.status is OutcomeStatus.REJECTED and game.calls == []


def test_runner_resolves_the_artifact_and_advances(tmp_path):
    from test_drex_runner import PreferSelector, _fake_end_turn, _records

    from civ_mcp.drex.decision_log import DecisionLog
    from civ_mcp.drex.runner import RunConfig, Runner

    game = FakeGame()
    game.artifact = fx.artifact_choice()
    game.extra_blockers = [(ARTIFACT_BLOCKER, "Choose artifact")]
    log = DecisionLog(tmp_path / "run.jsonl", run_id="t", secrets=[])
    runner = Runner(
        game,
        PreferSelector(
            prefixes=(f"artifact:{fx.ARCHAEOLOGIST_ID}:3", "skip:", "research:", "produce:")
        ),
        log,
        RunConfig(turns=1),
        end_turn=_fake_end_turn(game),
    )
    runner._sleep = _no_sleep
    runner.max_loop_iterations = 80
    result = asyncio.run(runner.run())
    assert result.stop_reason == "turn_budget_reached"
    assert ("choose_artifact_player", (3,)) in game.calls
    assert not [r for r in _records(tmp_path) if r["type"] == "unsupported_blocker"]
```

`tests/drex/test_drex_refresh.py`:

```python
def test_artifact_refreshes_units_and_overview():
    assert refresh_parts(ActionKind.CHOOSE_ARTIFACT_PLAYER) == frozenset(
        {"units", "overview", "blockers", "popup"}
    )
```

Existing tests use `ENDTURN_BLOCKING_ARTIFACT` as the example of an unsupported blocker; once it is supported they must use a type that stays in `PHASE_LATER_BLOCKERS`:
- `tests/drex/test_drex_scheduler.py::test_unsupported_blockers_are_reported`: replace both occurrences of `"ENDTURN_BLOCKING_ARTIFACT"` with `"ENDTURN_BLOCKING_EMERGENCY_NEEDS_ATTENTION"`.
- `tests/drex/test_drex_runner.py::test_unsupported_blocker_waits_and_logs_instead_of_stopping`: replace `("ENDTURN_BLOCKING_ARTIFACT", "Choose artifact")` with `("ENDTURN_BLOCKING_EMERGENCY_NEEDS_ATTENTION", "Emergency")` and the expected list with `["ENDTURN_BLOCKING_EMERGENCY_NEEDS_ATTENTION"]`.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/drex/test_drex_artifact.py tests/drex/test_drex_refresh.py -q 2>&1 | tail -5`
Expected: ImportError / AttributeError for the new names.

- [ ] **Step 4: Lua**

Append to `src/civ_mcp/lua/drex_queries.py`:

```python
ARTIFACT_KINDS = {  # kObject.Type in ChooseArtifact.lua; 1, 2 and 5 offer no choice
    0: "unknown origin",
    1: "tribal village",
    2: "barbarian camp",
    3: "battle site",
    4: "shipwreck",
    5: "heroic relic",
}
_ARTIFACT_CHOICE_TYPES = (0, 3, 4)


@dataclass
class ArtifactChoice:
    unit_id: int
    unit_name: str
    x: int
    y: int
    kind: str
    era: str
    players: list[tuple[int, str, str]]  # (player_id, civ name, "acting" | "target")


def _lua_artifact() -> str:
    return f"""
local me = Game.GetLocalPlayer()
local arch = Players[me]:GetUnits():GetNextExtractingArchaeologist()
if arch == nil then print("NO_ARTIFACT"); print("{SENTINEL}"); return end
local idx = arch:GetArchaeology():GetArtifactIndex()
local obj = Game.GetArtifactByIndex(idx)
if obj == nil then print("ERR:NO_ARTIFACT_OBJECT"); print("{SENTINEL}"); return end
{_LUA_CIV_NAME}
local t = obj.Type or 0
local acting = obj.ActingPlayerID
local target = obj.TargetPlayerID
local hasChoice = (t == 0 or t == 3 or t == 4) and target ~= nil and target >= 0 and target ~= acting
"""


def build_artifact_choice_query() -> str:
    """InGame: the artifact awaiting a player choice, as ChooseArtifact.lua
    reads it, and the one or two civilizations it can be credited to."""
    return f"""
{_lua_artifact()}
local era = ""
pcall(function() era = Locale.Lookup(GameInfo.Eras[obj.ActingPlayerEra].Name) end)
print("ARTIFACT|" .. (arch:GetID() + me * 65536) .. "|" .. Locale.Lookup(arch:GetName()) .. "|" .. arch:GetX() .. "|" .. arch:GetY() .. "|" .. tostring(t) .. "|" .. era)
print("PLAYER|" .. tostring(acting) .. "|" .. civName(acting) .. "|acting")
if hasChoice then print("PLAYER|" .. tostring(target) .. "|" .. civName(target) .. "|target") end
print("{SENTINEL}")
"""


def parse_artifact_choice(lines: list[str]) -> ArtifactChoice | None:
    choice: ArtifactChoice | None = None
    players: list[tuple[int, str, str]] = []
    for line in lines:
        if line.startswith("ARTIFACT|"):
            p = line.split("|")
            if len(p) >= 7:
                try:
                    kind = ARTIFACT_KINDS.get(int(p[5]), "unknown origin")
                except ValueError:
                    kind = "unknown origin"
                choice = ArtifactChoice(
                    int(p[1]), p[2], int(p[3]), int(p[4]), kind, p[6], []
                )
        elif line.startswith("PLAYER|"):
            p = line.split("|")
            if len(p) >= 4:
                players.append((int(p[1]), p[2], p[3].strip()))
    if choice is None:
        return None
    choice.players = players
    return choice


def build_choose_artifact_player(player_id: int) -> str:
    """InGame: credit the artifact to ``player_id`` (``CHOOSE_ARTIFACT_PLAYER``
    with ``PARAM_PLAYER_ONE``, as the popup's buttons do) after re-checking
    that player is one the engine offers."""
    return f"""
{_lua_artifact()}
local pid = {int(player_id)}
if pid ~= acting and not (hasChoice and pid == target) then print("ERR:PLAYER_NOT_OFFERED|" .. pid); print("{SENTINEL}"); return end
local params = {{}}
params[PlayerOperations.PARAM_PLAYER_ONE] = pid
UI.RequestPlayerOperation(me, PlayerOperations.CHOOSE_ARTIFACT_PLAYER, params)
pcall(function() local popup = ContextPtr:LookUpControl("/InGame/ChooseArtifact") if popup then popup:SetHide(true) end end)
print("OK:ARTIFACT_CHOSEN|" .. civName(pid))
print("{SENTINEL}")
"""
```

`game_state.py`:

```python
    async def get_artifact_choice(self) -> lq_drex.ArtifactChoice | None:
        """InGame: the excavated artifact awaiting a player choice, or None."""
        lines = await self.conn.execute_write(lq_drex.build_artifact_choice_query())
        return lq_drex.parse_artifact_choice(lines)

    async def choose_artifact_player(self, player_id: int) -> str:
        """InGame: credit the pending artifact to a player the engine offers."""
        lines = await self.conn.execute_write(
            lq_drex.build_choose_artifact_player(player_id)
        )
        return _action_result(lines)
```

- [ ] **Step 5: Candidates, inputs, enumeration, question, executor, refresh, spectator, scheduler, probe**

`candidates.py`: `DecisionCategory.ARTIFACT = "artifact"`; `ActionKind.CHOOSE_ARTIFACT_PLAYER = "choose_artifact_player"`;

```python
@dataclass(frozen=True)
class ArtifactParams:
    archaeologist_unit_id: int
    player_id: int
    player_name: str
```

union, `PARAMS_FOR_KIND`, id `case ActionKind.CHOOSE_ARTIFACT_PLAYER, ArtifactParams(archaeologist_unit_id=u, player_id=pid): return f"artifact:{u}:{pid}"`.

`observation.py`: `artifact: Any = None  # lua.drex_queries.ArtifactChoice`.

`enumerate.py`:

```python
def artifact_candidates(choice: Any) -> list[Candidate]:
    """One candidate per civilization the artifact can be credited to (the
    acting player, plus the target when the engine offers a choice)."""
    return [
        Candidate.create(
            ActionKind.CHOOSE_ARTIFACT_PLAYER,
            ArtifactParams(choice.unit_id, pid, name),
            label=f"Credit the artifact to {name}",
            facts={"role": role, "origin": choice.kind, "era": choice.era},
        )
        for pid, name, role in choice.players
    ]
```

`points.py`: `QUESTIONS[DecisionCategory.ARTIFACT] = "Which civilization should this artifact be credited to?"`; branch on `inputs.artifact`.

`live.py`: `case DecisionCategory.ARTIFACT: return DecisionInputs(artifact=await gs.get_artifact_choice())`.

`executor.py`: dispatch `DispatchCall("choose_artifact_player", (p.player_id,))`; precheck:

```python
            case ActionKind.CHOOSE_ARTIFACT_PLAYER:
                known = self._known.artifact if self._known is not None else None
                art = known if known is not None else await gs.get_artifact_choice()
                if art is None:
                    return _no("no_artifact_pending")
                if art.unit_id != p.archaeologist_unit_id:
                    return _no("different_archaeologist")
                if p.player_id not in {pid for pid, _, _ in art.players}:
                    return _no("player_not_offered")
                return _ok()
```

verify:

```python
            case ActionKind.CHOOSE_ARTIFACT_PLAYER:
                if raw.startswith("ARTIFACT_CHOSEN|"):
                    return confirmed("artifact_confirmed_from_dispatch")
                if _game_error(raw):
                    return self._unconfirmed(raw, "artifact_refused")

                async def gone():
                    return (await gs.get_artifact_choice()) is None

                if await self._poll(gone):
                    return confirmed("artifact_no_longer_pending")
                return self._unconfirmed(raw, "artifact_not_observed")
```

`refresh.py`: `ActionKind.CHOOSE_ARTIFACT_PLAYER: frozenset({"units", "overview"})`.

`spectate.py`: `case ActionKind.CHOOSE_ARTIFACT_PLAYER, ArtifactParams(): unit = core.unit(p.archaeologist_unit_id); return None if unit is None else (unit.x, unit.y, candidate.label)` (written as an if/return like the escape case).

`scheduler.py`: `ARTIFACT_BLOCKER = "ENDTURN_BLOCKING_ARTIFACT"`; append `(frozenset({ARTIFACT_BLOCKER}), DecisionCategory.ARTIFACT)` to `PROMPT_BLOCKERS`; add to `SUPPORTED_BLOCKERS`; remove from `PHASE_LATER_BLOCKERS`; `key_for`, `_limit` (4), `note` non-resolving; `SCHEDULER_ORDER` line `"artifact: when ARTIFACT blocks (credit the find to a claimant)",`.

`cli.py`: `"artifact": lambda gs: gs.get_artifact_choice(),`.

- [ ] **Step 6: Run the tests, then the suite**

Run: `uv run pytest tests/drex/test_drex_artifact.py tests/drex/test_drex_refresh.py tests/drex/test_drex_scheduler.py tests/drex/test_drex_runner.py -q 2>&1 | tail -5`
Expected: PASS.

Run: `uv run pytest tests -q 2>&1 | tail -3`
Expected: all pass.

- [ ] **Step 7: Lint and commit**

```bash
uvx ruff format src/civ_mcp/lua/drex_queries.py src/civ_mcp/drex tests/drex && uvx ruff check src/civ_mcp/lua/drex_queries.py src/civ_mcp/drex tests/drex
git add src/civ_mcp/lua/drex_queries.py src/civ_mcp/game_state.py src/civ_mcp/drex/candidates.py src/civ_mcp/drex/observation.py src/civ_mcp/drex/live.py src/civ_mcp/drex/points.py src/civ_mcp/drex/enumerate.py src/civ_mcp/drex/executor.py src/civ_mcp/drex/refresh.py src/civ_mcp/drex/spectate.py src/civ_mcp/drex/scheduler.py src/civ_mcp/drex/cli.py tests/drex/drex_fixtures.py tests/drex/drex_fakes.py tests/drex/test_drex_refresh.py tests/drex/test_drex_scheduler.py tests/drex/test_drex_runner.py tests/drex/test_drex_artifact.py
git commit -m "Artifact claimant is a Drex decision; ARTIFACT blocker supported

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: Classification, docs, live probes

**Files:**
- Modify: `tests/drex/test_blocker_coverage.py`, `docs/drex.md`

- [ ] **Step 1: Write the failing test**

Append to `tests/drex/test_blocker_coverage.py`:

```python
def test_only_types_this_game_lacks_remain_phase_later():
    from civ_mcp.drex.scheduler import PHASE_LATER_BLOCKERS

    assert PHASE_LATER_BLOCKERS == {
        "ENDTURN_BLOCKING_EMERGENCY_NEEDS_ATTENTION",
        "ENDTURN_BLOCKING_WORLD_CONGRESS_SESSION",
        "ENDTURN_BLOCKING_WORLD_CONGRESS_SPECIAL_SESSION",
    }
    # every type the live Base-ruleset dump holds is decided or housekeeping
    from civ_mcp.drex.scheduler import HOUSEKEEPING_BLOCKERS, SUPPORTED_BLOCKERS

    assert _names() <= SUPPORTED_BLOCKERS | HOUSEKEEPING_BLOCKERS
```

- [ ] **Step 2: Run it**

Run: `uv run pytest tests/drex/test_blocker_coverage.py -q 2>&1 | tail -3`
Expected: PASS already if Tasks 1–3 removed all five names from `PHASE_LATER_BLOCKERS` (this test pins the end state; if it fails, a name was left behind — fix the set, not the test). Note the result in the ledger either way.

- [ ] **Step 3: Docs**

`docs/drex.md`:
- "Scheduler order": insert after item 2: `2a. Modal prompts, decided before anything else while their blocker stands (up to 4 per turn each): captured or rebelled city (keep / raze / liberate to founder / liberate to previous owner / reject, exactly the directives the engine accepts), a caught spy's escape route (escape-route and dragnet-priority blockers; one candidate per escape district the city has), the civilization an excavated artifact is credited to (a single claimant is forced). A prompt blocker that stands once nothing is pending is dismissed as housekeeping.`
- "Supported decisions" table: add three rows:
  - `| Captured / rebelled city | get_captured_city (new Lua over GetNextRebelledCity / GetNextCapturedCity and CanStartCommand(DESTROY) per directive) | resolve_city_capture(action) | <ACTION>| from dispatch, else city no longer pending |`
  - `| Spy escape route | get_spy_escape_choice (new Lua; asks for the escaping spy only after the escape/dragnet notification is found standing) | choose_spy_escape(district) (new Lua, SET_ESCAPE_ROUTE) | ESCAPE_ROUTE| from dispatch, else spy no longer escaping |`
  - `| Artifact claimant | get_artifact_choice (new Lua over GetNextExtractingArchaeologist / Game.GetArtifactByIndex) | choose_artifact_player(player) (new Lua, CHOOSE_ARTIFACT_PLAYER) | ARTIFACT_CHOSEN| from dispatch, else artifact no longer pending |`
- "End-turn blockers" paragraph: rewrite the "Supported:" list to name every supported family including captured/disloyal city, spy escape and dragnet, artifact; the "Everything else" sentence becomes: `Everything else (emergencies, World Congress sessions — types this Base-ruleset game does not have) waits with unsupported_blocker and never stops the run.`
- "Limitations": replace the "Not supported yet (classified as PHASE_LATER_BLOCKERS...)" bullet with: `Not supported yet (PHASE_LATER_BLOCKERS): emergencies and World Congress sessions, which this game's ruleset does not raise. Phase 4 prompts (captured city, spy escape, artifact) are verified by the suite; their queries return "nothing pending" live until the game raises the prompt. Also not yet: multi-turn movement, espionage missions, purchases, unit upgrades, trade route re-plotting.`
- Add `captured_city|spy_escape|artifact` to the `probe --kind` mention in "Install and run" if the kinds are listed there.

- [ ] **Step 4: Live probes (game open, run stopped)**

```bash
pkill -TERM -f "[b]in/civ-drex play"; sleep 3
for k in captured_city spy_escape artifact; do uv run civ-drex probe --kind $k; done
```
Expected: each prints `{"kind": ..., "result": null}` (no prompt pending) without a tuner error; if the tuner is busy, record `probe skipped: tuner busy` in the ledger. Then restart the run: `nohup uv run civ-drex play --turns 500 > logs/drex/play-phase4.out 2>&1 &`.

- [ ] **Step 5: Suite, lint, commit**

Run: `uv run pytest tests -q 2>&1 | tail -3` — Expected: all pass.

```bash
uvx ruff format tests/drex/test_blocker_coverage.py && uvx ruff check tests/drex/test_blocker_coverage.py
git add tests/drex/test_blocker_coverage.py docs/drex.md
git commit -m "Document Phase 4 prompts; only emergency and Congress types remain phase-later

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```
