# civ-drex Phase 1: Resilience and Speed — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `civ-drex play` run no longer ends on Drex API errors, tuner errors, or loop guards, and a decision costs at most three tuner round trips instead of about fifteen.

**Architecture:** The decision loop (`observe → schedule → enumerate → select → execute → verify`) is unchanged. Speed comes from batching the observation into two Lua scripts, refreshing only what an action could change, reusing the decision's own reads for prechecks, and confirming outcomes from dispatch output. Resilience comes from the selector waiting instead of pausing, the runner reconnecting instead of stopping, and every remaining `Stop()` mapped to an end turn, a wait, or housekeeping.

**Tech Stack:** Python 3.12, asyncio, httpx, pytest (`uv run pytest tests`), ruff via `uvx ruff`. Lua snippets executed in Civ 6 through the FireTuner TCP protocol (`GameConnection`).

**Spec:** `docs/superpowers/specs/2026-09-30-civ-drex-never-stop-design.md` (Sections 3 "A1–A4" and 5 "C1–C8" are Phase 1).

## Global Constraints

- Only reachable stop reasons after this phase: `game_over`, `turn_budget_reached`, `interrupted`, `dry_run_complete` (spec Section 2, principle 2).
- No decision is made without Drex; forced execution only for exactly one legal option (`forced_single_candidate` / `forced_turn_ending_order`), unchanged.
- Fresh precheck before dispatch and single dispatch per candidate per turn are unchanged (spec principle 3). Speed work removes round trips, never checks.
- Dry-run must make no mutating calls: `game.calls == []` in the dry-run tests must keep passing.
- Speed targets: at most 3 tuner round trips per decision (excluding the Drex call), 2 per full observation; a turn of eight decisions under 15 s plus the engine's AI turn.
- New env settings: `DREX_MAX_BACKOFF_S` (default 60), `GAME_DEAD_AFTER_S` (default 120). Existing names unchanged.
- The API key is redacted in logs (existing `DecisionLog(secrets=...)`); any new log record that could carry an error message must go through the same log.
- Run the full suite (`uv run pytest tests -q`) before every commit; 364 tests pass at the start of this plan.
- Format and lint with `uvx ruff format <files>` and `uvx ruff check <files>` before committing. Four `BLE001` warnings pre-exist in `cli.py` and `runner.py`; do not add new ones (use narrow exception tuples).
- Do not commit the unrelated uncommitted files `src/civ_mcp/lua/{cities,overview,tech}.py` and `tests/test_base_game_compat.py`; they belong to someone else's work in progress.

## Review Focus

1. **Batched Lua where one section errors.** A `pcall` failure in the cities section must not lose the units section or hang the read; the parser must return the other sections and record the error. Pinned in Task 3 (`test_split_batch_reports_section_error_and_keeps_others`).
2. **Partial refresh masking a change.** A unit move that captures a civilian or founds a city changes cities too; the refresh table must include cities for `FOUND_CITY` and `ATTACK`, and the once-per-turn full observe before end turn must run even when no unit acted. Pinned in Task 5 (`test_refresh_parts_for_found_city_includes_cities`, `test_full_observe_runs_before_end_turn_even_without_actions`).
3. **Selector waiting forever on a misconfigured key.** The wait loop must re-read the env file on each non-retryable error and must log every wait, or a bad key looks like a hang. Pinned in Task 10 (`test_auth_error_refreshes_client_and_continues`, `test_every_wait_is_reported`).
4. **Reconnect during dispatch.** A socket drop while a mutation is in flight must still never re-send; the runner may reconnect only around observation, precheck and verify, and must reconcile a raised dispatch by reading state. Pinned in Task 12 (`test_dispatch_error_is_reconciled_not_replayed`).
5. **Popup watcher during the AI turn.** InGame queries during AI processing can hang the engine (comment in `end_turn.py:1220`); the spectator must go quiet while an end turn is in flight. Pinned in Task 8 (`test_spectator_is_quiet_during_end_turn`).

---

## File Structure

| File | Responsibility |
|---|---|
| `src/civ_mcp/connection.py` (modify) | Round-trip counter and timing; shorter drain waits. |
| `src/civ_mcp/lua/batch.py` (create) | Build one Lua script from named sections; split the output back into sections. |
| `src/civ_mcp/game_state.py` (modify) | `get_core_snapshot(parts)`; identity side effects factored into `_apply_identity`; `move_unit(..., predismiss=True)`. |
| `src/civ_mcp/lua/cities.py` (modify, one function) | `build_verify_production` guarded with `pcall`. |
| `src/civ_mcp/drex/refresh.py` (create) | Table: action kind → observation parts to refresh. |
| `src/civ_mcp/drex/live.py` (modify) | `core()` via snapshot when available; `refresh(core, parts)`; `inputs()` carries `progress` for research/civic. |
| `src/civ_mcp/drex/observation.py` (modify) | `CoreObservation.popup_state`; `DecisionInputs.progress`. |
| `src/civ_mcp/drex/executor.py` (modify) | Reuse `inputs` in prechecks; confirm moves and production from dispatch output; `roundtrips` in outcome. |
| `src/civ_mcp/drex/selectors.py` (modify) | `DrexSelector` waits indefinitely; `SelectionPaused` removed; `select()` forced-rule case unchanged. |
| `src/civ_mcp/drex/runner.py` (modify) | Timing fields, partial refresh, prefetch, recovery loop, loop guards, spectator quiet/popup feed, per-turn speed record. |
| `src/civ_mcp/drex/spectate.py` (modify) | `popup_status()`, `set_critical()`, `quiet()`; no self-polling. |
| `src/civ_mcp/spectator.py` (modify) | `PopupWatcher(poll=False)` external-status mode; `CameraController.set_critical()`. |
| `src/civ_mcp/drex/scheduler.py` (modify) | Remove `Stop` for budget and sessions; add `EXIT` candidate rule flag. |
| `src/civ_mcp/drex/enumerate.py` (modify) | Diplomacy candidates gain "Close the screen" (`EXIT`) once the failure budget is spent. |
| `src/civ_mcp/end_turn.py` (modify) | Phase timings; skip narration-only checks in decision-only mode; 0.5 s sleeps. |
| `src/civ_mcp/drex/cli.py` (modify) | Per-turn progress line; `refresh_client` for the selector; `GAME_DEAD_AFTER_S`; exit codes. |
| `scripts/drex_timing.py` (create) | Summarise a decision log's timing. |
| `docs/drex.md` (modify) | Stop reasons, settings, speed numbers, `FullScreen 2`. |
| Tests: `tests/drex/test_drex_batch.py`, `test_drex_refresh.py`, `test_drex_recovery.py`, `test_drex_speed.py` (create); `test_drex_runner.py`, `test_drex_selectors.py`, `test_drex_scheduler.py`, `test_drex_executor.py`, `test_drex_spectate.py`, `drex_fakes.py` (modify). |

Task order: 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10 → 11 → 12 → 13 → 14 → 15. Tasks 1–9 are speed (C0–C8), 10–13 resilience (A1–A4), 14–15 wiring and docs.

---

### Task 1: Round-trip counter and timing on the connection

**Files:**
- Modify: `src/civ_mcp/connection.py:150-215`
- Test: `tests/drex/test_drex_speed.py` (create)

**Interfaces:**
- Produces: `GameConnection.roundtrips: int`, `GameConnection.roundtrip_ms: float` (cumulative), `GameConnection.snapshot_counters() -> tuple[int, float]`. Later tasks read these to attribute round trips per decision.

- [ ] **Step 1: Write the failing test**

```python
# tests/drex/test_drex_speed.py
"""Round-trip accounting and budgets for the decision loop."""

import asyncio

from civ_mcp.connection import GameConnection


class _Msg:
    def __init__(self, payload):
        self.payload = payload


def _fake_wire(monkeypatch, outputs):
    """Replace tuner_client I/O: every command returns `outputs` then the sentinel."""
    from civ_mcp import connection as conn_mod

    sent = []

    async def send_message(writer, tag, text):
        sent.append(text)
        writer.queue = [_Msg(f"O\x00InGame: {o}") for o in outputs] + [
            _Msg("O\x00InGame: ---END---")
        ]

    async def recv_message_timeout(reader, timeout=2.0):
        if reader.writer.queue:
            return reader.writer.queue.pop(0)
        return None

    async def drain_messages(reader, timeout=0.5):
        return []

    monkeypatch.setattr(conn_mod.tuner_client, "send_message", send_message)
    monkeypatch.setattr(conn_mod.tuner_client, "recv_message_timeout", recv_message_timeout)
    monkeypatch.setattr(conn_mod.tuner_client, "drain_messages", drain_messages)
    return sent


class _Writer:
    queue: list = []

    def is_closing(self):
        return False


class _Reader:
    def __init__(self, writer):
        self.writer = writer


def _connected():
    c = GameConnection()
    w = _Writer()
    c._writer, c._reader = w, _Reader(w)
    c.gamecore_index, c.ingame_index = 0, 1
    return c


def test_each_execute_counts_one_roundtrip_and_records_time(monkeypatch):
    _fake_wire(monkeypatch, ["A", "B"])
    c = _connected()
    lines = asyncio.run(c.execute_write("print('x')"))
    assert lines == ["A", "B"]
    assert c.roundtrips == 1 and c.roundtrip_ms >= 0.0
    asyncio.run(c.execute_read("print('y')"))
    assert c.snapshot_counters()[0] == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/drex/test_drex_speed.py -v`
Expected: FAIL with `AttributeError: 'GameConnection' object has no attribute 'roundtrips'`

- [ ] **Step 3: Implement**

In `GameConnection.__init__` add:

```python
        self.roundtrips = 0
        self.roundtrip_ms = 0.0
```

Add the method:

```python
    def snapshot_counters(self) -> tuple[int, float]:
        """(round trips so far, cumulative milliseconds) for attribution."""
        return self.roundtrips, self.roundtrip_ms
```

In `_locked_execute`, wrap the body: record `t0 = asyncio.get_running_loop().time()` before the drain, and in a `finally` at the end increment `self.roundtrips += 1` and `self.roundtrip_ms += (loop.time() - t0) * 1000.0`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/drex/test_drex_speed.py -v`
Expected: PASS

- [ ] **Step 5: Run the suite, format, commit**

```bash
uv run pytest tests -q
uvx ruff format src/civ_mcp/connection.py tests/drex/test_drex_speed.py && uvx ruff check src/civ_mcp/connection.py tests/drex/test_drex_speed.py
git add src/civ_mcp/connection.py tests/drex/test_drex_speed.py
git commit -m "Count tuner round trips and their time on GameConnection"
```

---

### Task 2: Shorter drain waits (C0)

Each `_locked_execute` sleeps 0.1 s draining before the command and 0.2 s after, even when nothing arrives: 0.3 s of the ~0.35 s round trip. The sentinel already delimits output, so the drains only need to catch messages that are already buffered.

**Files:**
- Modify: `src/civ_mcp/connection.py:172-215`
- Test: `tests/drex/test_drex_speed.py`

**Interfaces:**
- Produces: class attributes `GameConnection.PRE_DRAIN_S = 0.01`, `GameConnection.POST_DRAIN_S = 0.02`.

- [ ] **Step 1: Write the failing test**

```python
def test_drain_waits_are_short(monkeypatch):
    from civ_mcp import connection as conn_mod

    waits = []

    async def drain_messages(reader, timeout=0.5):
        waits.append(timeout)
        return []

    _fake_wire(monkeypatch, ["A"])
    monkeypatch.setattr(conn_mod.tuner_client, "drain_messages", drain_messages)
    c = _connected()
    asyncio.run(c.execute_write("print('x')"))
    assert waits == [GameConnection.PRE_DRAIN_S, GameConnection.POST_DRAIN_S]
    assert GameConnection.PRE_DRAIN_S <= 0.02 and GameConnection.POST_DRAIN_S <= 0.05
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/drex/test_drex_speed.py::test_drain_waits_are_short -v`
Expected: FAIL with `AttributeError: type object 'GameConnection' has no attribute 'PRE_DRAIN_S'`

- [ ] **Step 3: Implement**

```python
class GameConnection:
    # A round trip used to spend 0.3 s in these two drains alone. The sentinel
    # delimits every response, so the drains only need to catch bytes that are
    # already buffered.
    PRE_DRAIN_S = 0.01
    POST_DRAIN_S = 0.02
```

Replace `timeout=0.1` with `timeout=self.PRE_DRAIN_S` and `timeout=0.2` with `timeout=self.POST_DRAIN_S` in `_locked_execute`.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/drex/test_drex_speed.py tests/test_hang_recovery.py -v`
Expected: PASS

- [ ] **Step 5: Live check and commit**

With the game loaded and no other tuner client running:

```bash
uv run python scripts/test_connection.py
```

Expected: handshake and Lua state list as before. Then:

```bash
uv run pytest tests -q
uvx ruff format src/civ_mcp/connection.py && uvx ruff check src/civ_mcp/connection.py
git add src/civ_mcp/connection.py tests/drex/test_drex_speed.py
git commit -m "Cut fixed drain waits per tuner round trip from 0.3s to 0.03s"
```

---

### Task 3: Lua batch builder and splitter (C1 foundation)

**Files:**
- Create: `src/civ_mcp/lua/batch.py`
- Test: `tests/drex/test_drex_batch.py` (create)

**Interfaces:**
- Produces: `build_batch(sections: list[tuple[str, str]]) -> str`, `split_batch(lines: list[str]) -> tuple[dict[str, list[str]], dict[str, str]]` (sections, errors), constants `SECTION_MARK = "@@SECTION|"`, `ERROR_MARK = "@@ERR|"`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/drex/test_drex_batch.py
"""One Lua round trip for many queries: build and split."""

from civ_mcp.lua._helpers import SENTINEL
from civ_mcp.lua.batch import ERROR_MARK, SECTION_MARK, build_batch, split_batch


def test_build_strips_inner_sentinels_and_wraps_each_section():
    a = 'print("A1")\nprint("---END---")\n'
    b = 'if x then print("B0"); print("---END---"); return end\nprint("B1")\nprint("---END---")'
    lua = build_batch([("a", a), ("b", b)])
    assert lua.count(SENTINEL) == 1 and lua.rstrip().endswith(f'print("{SENTINEL}")')
    assert lua.count(f'print("{SECTION_MARK}a")') == 1
    assert lua.count(f'print("{SECTION_MARK}b")') == 1
    assert "pcall(function()" in lua and lua.count("pcall(function()") == 2
    assert '"---END---"' not in lua.split(f"{SECTION_MARK}b")[1].rsplit(SENTINEL, 1)[0]


def test_split_groups_lines_by_section():
    lines = [f"{SECTION_MARK}a", "A1", "A2", f"{SECTION_MARK}b", "B1"]
    sections, errors = split_batch(lines)
    assert sections == {"a": ["A1", "A2"], "b": ["B1"]} and errors == {}


def test_split_batch_reports_section_error_and_keeps_others():
    lines = [f"{SECTION_MARK}a", f"{ERROR_MARK}a|attempt to index nil", f"{SECTION_MARK}b", "B1"]
    sections, errors = split_batch(lines)
    assert sections == {"a": [], "b": ["B1"]}
    assert errors == {"a": "attempt to index nil"}


def test_split_ignores_noise_before_first_section():
    lines = ["BulkHide debug", f"{SECTION_MARK}a", "A1"]
    sections, _ = split_batch(lines)
    assert sections == {"a": ["A1"]}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/drex/test_drex_batch.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'civ_mcp.lua.batch'`

- [ ] **Step 3: Implement**

```python
# src/civ_mcp/lua/batch.py
"""Run several query scripts in one tuner round trip.

Every builder in :mod:`civ_mcp.lua` ends with ``print("---END---")`` and may
bail early with ``print(...); print("---END---"); return``. A batch strips
those sentinel prints, wraps each script in ``pcall(function() ... end)`` so a
``return`` or an error only ends its own section, prints a section marker
before each, and one sentinel at the very end.
"""

from __future__ import annotations

from civ_mcp.lua._helpers import SENTINEL

SECTION_MARK = "@@SECTION|"
ERROR_MARK = "@@ERR|"
_SENTINEL_PRINT = f'print("{SENTINEL}")'


def build_batch(sections: list[tuple[str, str]]) -> str:
    parts: list[str] = []
    for name, lua in sections:
        body = lua.replace(_SENTINEL_PRINT, "").replace(f"print('{SENTINEL}')", "")
        parts.append(
            f'print("{SECTION_MARK}{name}")\n'
            f"do local __ok, __err = pcall(function()\n{body}\nend)\n"
            f'if not __ok then print("{ERROR_MARK}{name}|" .. tostring(__err)) end end\n'
        )
    parts.append(_SENTINEL_PRINT)
    return "\n".join(parts)


def split_batch(lines: list[str]) -> tuple[dict[str, list[str]], dict[str, str]]:
    sections: dict[str, list[str]] = {}
    errors: dict[str, str] = {}
    current: str | None = None
    for raw in lines:
        line = raw.strip()
        if line.startswith(SECTION_MARK):
            current = line[len(SECTION_MARK) :]
            sections.setdefault(current, [])
            continue
        if line.startswith(ERROR_MARK):
            name, _, msg = line[len(ERROR_MARK) :].partition("|")
            errors[name] = msg
            sections.setdefault(name, [])
            continue
        if current is not None:
            sections[current].append(raw)
    return sections, errors
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/drex/test_drex_batch.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
uvx ruff format src/civ_mcp/lua/batch.py tests/drex/test_drex_batch.py && uvx ruff check src/civ_mcp/lua/batch.py tests/drex/test_drex_batch.py
git add src/civ_mcp/lua/batch.py tests/drex/test_drex_batch.py
git commit -m "Add Lua batch builder/splitter for single-round-trip observation"
```

---

### Task 4: Batched core snapshot in GameState and LiveObserver (C1, C6 data)

**Files:**
- Modify: `src/civ_mcp/game_state.py:72-108` (identity), add `get_core_snapshot` near line 1683
- Modify: `src/civ_mcp/drex/observation.py` (`CoreObservation.popup_state`)
- Modify: `src/civ_mcp/drex/live.py:31-56`
- Modify: `src/civ_mcp/spectator.py` (export `_POPUP_POLL_LUA` as `POPUP_STATUS_LUA`)
- Test: `tests/drex/test_drex_speed.py`, `tests/drex/test_drex_observation.py`

**Interfaces:**
- Produces: 

```python
CORE_PARTS = frozenset({"identity", "overview", "cities", "units", "sessions", "deals", "blockers", "tech", "progress", "popup"})

@dataclass
class CoreSnapshot:
    civ: str; seed: int
    overview: lq.GameOverview | None
    tech: lq.TechCivicStatus | None
    progress: lq.ProgressTypes | None
    cities: list[lq.CityInfo] | None
    units: list[lq.UnitInfo] | None
    sessions: list[lq.DiplomacySession] | None
    deals: list[lq.PendingDeal] | None
    blockers: list[tuple[str, str]] | None
    popup_state: str | None          # "CLEAR" | "POPUP" | "CRITICAL"
    errors: dict[str, str]           # section name -> Lua error
    roundtrips: int                  # 1 or 2

async def GameState.get_core_snapshot(self, parts: frozenset[str] = CORE_PARTS) -> CoreSnapshot
```

  `None` for a part not requested or whose section errored (error recorded in `errors`). `CoreObservation` gains `popup_state: str = "CLEAR"`. `LiveObserver.core()` uses `gs.get_core_snapshot` when the object has that attribute, otherwise the existing nine calls (so `FakeGame` keeps working unchanged).

- [ ] **Step 1: Write the failing tests**

Add to `tests/drex/test_drex_speed.py`:

```python
from civ_mcp.drex.live import LiveObserver


class _SnapshotGame:
    """A game that offers the batched snapshot; counts how often it is used."""

    def __init__(self, base):
        self.base = base
        self.snapshot_calls = []

    def __getattr__(self, name):
        return getattr(self.base, name)

    async def get_core_snapshot(self, parts=None):
        from civ_mcp.game_state import CORE_PARTS, CoreSnapshot

        parts = parts or CORE_PARTS
        self.snapshot_calls.append(frozenset(parts))
        b = self.base
        civ, seed = await b.get_game_identity()
        return CoreSnapshot(
            civ=civ,
            seed=seed,
            overview=await b.get_game_overview() if "overview" in parts else None,
            tech=await b.get_tech_civics() if "tech" in parts else None,
            progress=await b.get_progress_types() if "progress" in parts else None,
            cities=(await b.get_cities())[0] if "cities" in parts else None,
            units=await b.get_units() if "units" in parts else None,
            sessions=await b.get_diplomacy_sessions() if "sessions" in parts else None,
            deals=await b.get_pending_deals() if "deals" in parts else None,
            blockers=await b.get_end_turn_blockers() if "blockers" in parts else None,
            popup_state="POPUP" if "popup" in parts else None,
            errors={},
            roundtrips=2,
        )


def test_live_observer_uses_snapshot_when_available():
    from drex_fakes import FakeGame

    game = _SnapshotGame(FakeGame())
    obs = LiveObserver(game)
    core = asyncio.run(obs.core())
    assert game.snapshot_calls and core.civ == "rome" and core.turn == 5
    assert core.popup_state == "POPUP"
    assert len(core.units) == 3 and core.cities[0].name == "Roma"


def test_live_observer_falls_back_to_individual_queries_without_snapshot():
    from drex_fakes import FakeGame

    game = FakeGame()
    core = asyncio.run(LiveObserver(game).core())
    assert core.popup_state == "CLEAR" and core.turn == 5
```

Add to `tests/drex/test_drex_game_state_additions.py` (it already tests `GameState` against a fake connection; follow its existing `_Conn` pattern for `execute_read`/`execute_write` capture):

```python
def test_get_core_snapshot_uses_two_round_trips_and_splits_sections():
    from civ_mcp.game_state import CORE_PARTS, GameState
    from civ_mcp.lua.batch import SECTION_MARK

    class Conn:
        def __init__(self):
            self.calls = []
            self.roundtrips = 0

        async def execute_write(self, lua, timeout=5.0):
            self.calls.append(("write", lua))
            self.roundtrips += 1
            return [
                f"{SECTION_MARK}identity", "GAMESEED|CIVILIZATION_ROME|42",
                f"{SECTION_MARK}overview", f"{SECTION_MARK}cities", f"{SECTION_MARK}units",
                f"{SECTION_MARK}sessions", f"{SECTION_MARK}deals",
                f"{SECTION_MARK}blockers", "NONE",
                f"{SECTION_MARK}popup", "CLEAR",
            ]

        async def execute_read(self, lua, timeout=5.0):
            self.calls.append(("read", lua))
            self.roundtrips += 1
            return [f"{SECTION_MARK}tech", f"{SECTION_MARK}progress", "RESEARCH|NONE", "CIVIC|CIVIC_CODE_OF_LAWS"]

    conn = Conn()
    gs = GameState(conn)
    snap = asyncio.run(gs.get_core_snapshot(CORE_PARTS))
    assert [c[0] for c in conn.calls] == ["write", "read"]
    assert snap.civ == "rome" and snap.seed == 42
    assert snap.blockers == [] and snap.popup_state == "CLEAR" and snap.roundtrips == 2
    assert gs._game_identity == ("rome", 42)


def test_get_core_snapshot_with_units_only_is_one_write_round_trip():
    ...  # same Conn; parts=frozenset({"units", "blockers"}); assert calls == [("write", ...)] and snap.tech is None
```

(Write the second test fully: construct `Conn` as above, call `gs.get_core_snapshot(frozenset({"units", "blockers"}))`, assert exactly one call and that it was a write, `snap.tech is None`, `snap.units == []`.)

Check `parse_progress_types` input format in `src/civ_mcp/lua/action_space.py:273` and adjust the two `RESEARCH|`/`CIVIC|` lines in the fake to match what it parses.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/drex/test_drex_speed.py tests/drex/test_drex_game_state_additions.py -v`
Expected: FAIL with `ImportError: cannot import name 'CORE_PARTS'` and `AttributeError: 'GameState' object has no attribute 'get_core_snapshot'`

- [ ] **Step 3: Implement in `game_state.py`**

Factor the identity side effects out of `get_game_identity`:

```python
    def _apply_identity(self, lines: list[str]) -> tuple[str, int]:
        for line in lines:
            if line.startswith("GAMESEED|"):
                parts = line.split("|")
                civ = parts[1].replace("CIVILIZATION_", "").lower()
                new_id = (civ, int(parts[2]))
                if self._game_identity is not None and new_id != self._game_identity:
                    log.info("Game changed: %s → %s", self._game_identity, new_id)
                    self._last_snapshot = None
                    self._diary_written_turn = None
                    self._last_game_over = None
                    self._save_load_history = []
                    self._run_aborted = False
                    self._advisor_calls_this_turn = 0
                    self._advisor_budget_warning = None
                self._game_identity = new_id
                return new_id
        return ("unknown", 0)
```

Make `get_game_identity` build `code` as today (extract it to module constant `IDENTITY_LUA`) and `return self._apply_identity(lines)`.

Add near `get_progress_types`:

```python
CORE_PARTS = frozenset(
    {"identity", "overview", "cities", "units", "sessions", "deals", "blockers",
     "tech", "progress", "popup"}
)
_INGAME_SECTIONS = (
    ("identity", lambda: IDENTITY_LUA),
    ("overview", lq.build_overview_query),
    ("cities", lq.build_cities_query),
    ("units", lq.build_units_query),
    ("sessions", lq.build_diplomacy_session_query),
    ("deals", lq.build_pending_deals_query),
    ("blockers", lq.build_end_turn_blocking_query),
    ("popup", lambda: POPUP_STATUS_LUA),
)
_GAMECORE_SECTIONS = (
    ("tech", lq.build_tech_civics_query),
    ("progress", lq.build_progress_types_query),
)


@dataclass
class CoreSnapshot:
    civ: str
    seed: int
    overview: lq.GameOverview | None = None
    tech: lq.TechCivicStatus | None = None
    progress: lq.ProgressTypes | None = None
    cities: list[lq.CityInfo] | None = None
    units: list[lq.UnitInfo] | None = None
    sessions: list[lq.DiplomacySession] | None = None
    deals: list[lq.PendingDeal] | None = None
    blockers: list[tuple[str, str]] | None = None
    popup_state: str | None = None
    errors: dict[str, str] = field(default_factory=dict)
    roundtrips: int = 0
```

and the method:

```python
    async def get_core_snapshot(self, parts: frozenset[str] = CORE_PARTS) -> CoreSnapshot:
        """Everything the decision loop observes, in at most two round trips
        (one InGame, one GameCore). Unrequested parts are None."""
        want = set(parts) | {"identity"}
        sections: dict[str, list[str]] = {}
        errors: dict[str, str] = {}
        trips = 0
        ingame = [(n, b()) for n, b in _INGAME_SECTIONS if n in want]
        if ingame:
            s, e = split_batch(await self.conn.execute_write(build_batch(ingame), timeout=10.0))
            sections.update(s); errors.update(e); trips += 1
        core = [(n, b()) for n, b in _GAMECORE_SECTIONS if n in want]
        if core:
            s, e = split_batch(await self.conn.execute_read(build_batch(core), timeout=10.0))
            sections.update(s); errors.update(e); trips += 1

        def got(name):
            return name in sections and name not in errors

        civ, seed = self._apply_identity(sections.get("identity", []))
        snap = CoreSnapshot(civ=civ, seed=seed, errors=errors, roundtrips=trips)
        if got("overview"):
            snap.overview = lq.parse_overview_response(sections["overview"])
        if got("tech"):
            snap.tech = lq.parse_tech_civics_response(sections["tech"])
        if got("progress"):
            snap.progress = lq.parse_progress_types(sections["progress"])
        if got("cities"):
            snap.cities = lq.parse_cities_response(sections["cities"])[0]
        if got("units"):
            snap.units = lq.parse_units_response(sections["units"])
        if got("sessions"):
            snap.sessions = lq.parse_diplomacy_sessions(sections["sessions"])
        if got("deals"):
            snap.deals = lq.parse_pending_deals_response(sections["deals"])
        if got("blockers"):
            snap.blockers = lq.parse_end_turn_blocking(sections["blockers"])
        if got("popup"):
            snap.popup_state = next(
                (l.strip() for l in sections["popup"] if l.strip() in ("CLEAR", "POPUP", "CRITICAL")),
                "CLEAR",
            )
        return snap
```

`get_game_overview` today applies post-processing after `parse_overview_response` (look at lines 109-150 and replicate anything beyond parsing, e.g. the player id or score fields, inside the snapshot path, or factor it into a `_finish_overview(ov)` helper used by both). `get_cities` returns `(cities, ghost_notes)` where ghost queue entries are removed; reuse the same helper if one exists, else call the same cleanup.

In `spectator.py` rename `_POPUP_POLL_LUA` to `POPUP_STATUS_LUA` (keep `_POPUP_POLL_LUA = POPUP_STATUS_LUA` alias) and import it in `game_state.py`.

- [ ] **Step 4: Implement in `observation.py` and `live.py`**

`CoreObservation`: add field `popup_state: str = "CLEAR"` after `blockers`.

`LiveObserver.core()`:

```python
    async def core(self) -> CoreObservation:
        gs = self.gs
        if hasattr(gs, "get_core_snapshot"):
            snap = await gs.get_core_snapshot()
            missing = [p for p in ("overview", "tech", "progress", "cities", "units",
                                   "sessions", "deals", "blockers") if getattr(snap, p) is None]
            if missing:
                raise ConnectionError(f"core snapshot incomplete: {missing}; errors={snap.errors}")
            self._counter += 1
            self._version = f"{snap.civ}:{snap.seed}:T{snap.overview.turn}:{self._counter}"
            return CoreObservation(
                version=self._version, civ=snap.civ, seed=snap.seed,
                local_player_id=snap.overview.player_id, overview=snap.overview,
                tech=snap.tech, progress=snap.progress, cities=snap.cities,
                units=snap.units, diplomacy_sessions=snap.sessions,
                pending_deals=snap.deals,
                blockers=[Blocker(t, m) for t, m in snap.blockers],
                popup_state=snap.popup_state or "CLEAR",
            )
        ...existing nine-call path unchanged...
```

Raising `ConnectionError` on an incomplete snapshot hands the failure to the recovery loop (Task 12) instead of building a half observation.

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests -q`
Expected: all pass (existing observation tests unchanged; new ones pass).

- [ ] **Step 6: Live check**

Stop the current run only if you must; otherwise wait for a natural restart. With the tuner free:

```bash
uv run python - <<'EOF'
import asyncio, time
from civ_mcp.connection import GameConnection
from civ_mcp.game_state import GameState
async def main():
    c = GameConnection(); await c.connect(); gs = GameState(c)
    t0=time.perf_counter(); s = await gs.get_core_snapshot(); dt=time.perf_counter()-t0
    print("roundtrips", s.roundtrips, "ms", round(dt*1000), "errors", s.errors)
    print("turn", s.overview.turn, "units", len(s.units), "cities", len(s.cities), "popup", s.popup_state)
    await c.disconnect()
asyncio.run(main())
EOF
```

Expected: `roundtrips 2`, well under 1000 ms, `errors {}`.

- [ ] **Step 7: Commit**

```bash
uvx ruff format src/civ_mcp/game_state.py src/civ_mcp/drex/live.py src/civ_mcp/drex/observation.py src/civ_mcp/spectator.py tests/drex/test_drex_speed.py tests/drex/test_drex_game_state_additions.py
uvx ruff check src/civ_mcp/game_state.py src/civ_mcp/drex/live.py src/civ_mcp/drex/observation.py
git add src/civ_mcp/game_state.py src/civ_mcp/drex/live.py src/civ_mcp/drex/observation.py src/civ_mcp/spectator.py tests/drex/test_drex_speed.py tests/drex/test_drex_game_state_additions.py
git commit -m "Observe the whole core state in two batched round trips"
```

---

### Task 5: Partial refresh after an action (C2)

**Files:**
- Create: `src/civ_mcp/drex/refresh.py`
- Modify: `src/civ_mcp/drex/live.py` (add `refresh`)
- Modify: `src/civ_mcp/drex/runner.py` (`_observe(refresh=...)`, once-per-turn full observe before end turn)
- Modify: `src/civ_mcp/drex/scheduler.py` (`TurnLedger.full_observed: bool = False`)
- Test: `tests/drex/test_drex_refresh.py` (create), `tests/drex/test_drex_runner.py`

**Interfaces:**
- Produces:

```python
# refresh.py
def refresh_parts(kind: ActionKind) -> frozenset[str]   # subset of CORE_PARTS, always includes "blockers" and "popup"
FULL = CORE_PARTS
# live.py
async def LiveObserver.refresh(self, previous: CoreObservation, parts: frozenset[str]) -> CoreObservation
```

  `refresh` calls `gs.get_core_snapshot(parts)` when available (else the individual getters for those parts), replaces only those fields on a copy of `previous`, bumps the version. Runner: `await self._observe(refresh=(parts, core))`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/drex/test_drex_refresh.py
"""Refresh only what the last action could have changed."""

import asyncio

from drex_fakes import FakeGame

from civ_mcp.drex.candidates import ActionKind
from civ_mcp.drex.live import LiveObserver
from civ_mcp.drex.refresh import FULL, refresh_parts


def test_unit_orders_refresh_units_and_blockers_only():
    for k in (ActionKind.MOVE_UNIT, ActionKind.FORTIFY_UNIT, ActionKind.HEAL_UNIT,
              ActionKind.SKIP_UNIT, ActionKind.IMPROVE_TILE):
        assert refresh_parts(k) == frozenset({"units", "blockers", "popup"}), k


def test_refresh_parts_for_found_city_includes_cities():
    assert {"units", "cities", "blockers"} <= refresh_parts(ActionKind.FOUND_CITY)
    assert {"units", "cities", "blockers"} <= refresh_parts(ActionKind.ATTACK)


def test_production_refreshes_cities_research_refreshes_progress():
    assert refresh_parts(ActionKind.SET_PRODUCTION) == frozenset({"cities", "blockers", "popup"})
    assert refresh_parts(ActionKind.SET_RESEARCH) == frozenset({"progress", "blockers", "popup"})
    assert refresh_parts(ActionKind.SET_CIVIC) == frozenset({"progress", "blockers", "popup"})


def test_diplomacy_and_deals_refresh_sessions_deals_and_overview():
    for k in (ActionKind.DIPLOMACY_RESPOND, ActionKind.DEAL_RESPOND):
        assert refresh_parts(k) == frozenset({"sessions", "deals", "overview", "blockers", "popup"})


def test_every_action_kind_has_a_refresh_rule():
    for k in ActionKind:
        parts = refresh_parts(k)
        assert parts and parts <= FULL and "blockers" in parts, k


def test_refresh_replaces_only_requested_parts_and_bumps_version():
    game = FakeGame()
    obs = LiveObserver(game)
    core = asyncio.run(obs.core())
    game.units[list(game.units)[0]].moves_remaining = 0
    game.cities[list(game.cities)[0]].population = 99
    fresh = asyncio.run(obs.refresh(core, frozenset({"units", "blockers", "popup"})))
    assert fresh.version != core.version
    assert fresh.units[0].moves_remaining == 0
    assert fresh.cities[0].population == core.cities[0].population  # not refreshed
    assert game.query_counts["get_units"] == 2
```

In `test_drex_runner.py` add:

```python
def test_move_is_followed_by_partial_refresh_not_full_observe(tmp_path):
    game = FakeGame()
    runner, _ = _runner(game, tmp_path, selector=PreferSelector(prefixes=("move:", "research:", "produce:", "skip:")))
    asyncio.run(runner.run())
    names = [c[0] for c in game.calls]
    # After the first unit move, cities are not re-read until the pre-end-turn full observe.
    first_move = names.index("move_unit")
    tail = names[first_move + 1 :]
    assert "get_units" in tail
    assert tail.index("get_cities") > tail.index("get_units")


def test_full_observe_runs_before_end_turn_even_without_actions(tmp_path):
    game = FakeGame()
    game.research = "TECH_MINING"
    game.cities[fx.CAPITAL_ID].currently_building = "UNIT_SCOUT"
    for u in game.units.values():
        u.moves_remaining = 0
    runner, _ = _runner(game, tmp_path)
    asyncio.run(runner.run())
    assert game.query_counts["get_units"] >= 2  # initial observe + pre-end-turn full observe
```

Note: `FakeGame.get_cities` and others must record into `query_counts` for these assertions. Add `self.query_counts[<name>] += 1` to `get_cities`, `get_game_overview`, `get_progress_types`, `get_diplomacy_sessions`, `get_pending_deals`, `get_end_turn_blockers` in `drex_fakes.py` (units already does). Also make `FakeGame` record queries in `self.calls` via `_record` only where it already does; do not change mutation recording.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/drex/test_drex_refresh.py tests/drex/test_drex_runner.py -k "refresh or full_observe" -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'civ_mcp.drex.refresh'`

- [ ] **Step 3: Implement `refresh.py`**

```python
"""Which parts of the observation an action can change."""

from __future__ import annotations

from civ_mcp.drex.candidates import ActionKind
from civ_mcp.game_state import CORE_PARTS

FULL = CORE_PARTS
_ALWAYS = frozenset({"blockers", "popup"})
_TABLE: dict[ActionKind, frozenset[str]] = {
    ActionKind.MOVE_UNIT: frozenset({"units"}),
    ActionKind.FORTIFY_UNIT: frozenset({"units"}),
    ActionKind.HEAL_UNIT: frozenset({"units"}),
    ActionKind.SKIP_UNIT: frozenset({"units"}),
    ActionKind.IMPROVE_TILE: frozenset({"units"}),
    ActionKind.FOUND_CITY: frozenset({"units", "cities", "overview"}),
    ActionKind.ATTACK: frozenset({"units", "cities"}),
    ActionKind.SET_PRODUCTION: frozenset({"cities"}),
    ActionKind.SET_RESEARCH: frozenset({"progress"}),
    ActionKind.SET_CIVIC: frozenset({"progress"}),
    ActionKind.SET_POLICY: frozenset({"overview"}),
    ActionKind.CHANGE_GOVERNMENT: frozenset({"overview"}),
    ActionKind.KEEP_GOVERNMENT: frozenset(),
    ActionKind.SEND_ENVOY: frozenset({"overview"}),
    ActionKind.CHOOSE_PANTHEON: frozenset({"overview"}),
    ActionKind.DIPLOMACY_RESPOND: frozenset({"sessions", "deals", "overview"}),
    ActionKind.DEAL_RESPOND: frozenset({"sessions", "deals", "overview"}),
}


def refresh_parts(kind: ActionKind) -> frozenset[str]:
    return _TABLE[kind] | _ALWAYS
```

Add a test-time guard: `test_every_action_kind_has_a_refresh_rule` above fails when a Phase 2 kind is added without a rule.

- [ ] **Step 4: Implement `LiveObserver.refresh`**

```python
    async def refresh(self, previous: CoreObservation, parts: frozenset[str]) -> CoreObservation:
        gs = self.gs
        changes: dict[str, Any] = {}
        if hasattr(gs, "get_core_snapshot"):
            snap = await gs.get_core_snapshot(frozenset(parts))
            if "overview" in parts and snap.overview is not None: changes["overview"] = snap.overview
            if "tech" in parts and snap.tech is not None: changes["tech"] = snap.tech
            if "progress" in parts and snap.progress is not None: changes["progress"] = snap.progress
            if "cities" in parts and snap.cities is not None: changes["cities"] = snap.cities
            if "units" in parts and snap.units is not None: changes["units"] = snap.units
            if "sessions" in parts and snap.sessions is not None: changes["diplomacy_sessions"] = snap.sessions
            if "deals" in parts and snap.deals is not None: changes["pending_deals"] = snap.deals
            if "blockers" in parts and snap.blockers is not None:
                changes["blockers"] = [Blocker(t, m) for t, m in snap.blockers]
            if "popup" in parts and snap.popup_state is not None: changes["popup_state"] = snap.popup_state
            wanted = {p for p in parts if p not in ("identity", "popup")}
            failed = [p for p in wanted if p in snap.errors]
            if failed:
                raise ConnectionError(f"refresh failed for {failed}: {snap.errors}")
        else:
            if "overview" in parts: changes["overview"] = await gs.get_game_overview()
            if "tech" in parts: changes["tech"] = await gs.get_tech_civics()
            if "progress" in parts: changes["progress"] = await gs.get_progress_types()
            if "cities" in parts: changes["cities"] = (await gs.get_cities())[0]
            if "units" in parts: changes["units"] = await gs.get_units()
            if "sessions" in parts: changes["diplomacy_sessions"] = await gs.get_diplomacy_sessions()
            if "deals" in parts: changes["pending_deals"] = await gs.get_pending_deals()
            if "blockers" in parts:
                changes["blockers"] = [Blocker(t, m) for t, m in await gs.get_end_turn_blockers()]
        self._counter += 1
        turn = changes.get("overview", previous.overview).turn
        self._version = f"{previous.civ}:{previous.seed}:T{turn}:{self._counter}"
        return dataclasses.replace(previous, version=self._version, **changes)
```

Keep `reactive()` as a thin wrapper: `return await self.refresh(previous, frozenset({"sessions", "deals", "blockers", "popup"}))`. (This is also what the in-flight end-turn path needs; the existing `test_drex_observation.py` reactive test should still pass.)

- [ ] **Step 5: Implement in `runner.py`**

Change `_observe`:

```python
    async def _observe(
        self,
        *,
        reactive_from: CoreObservation | None = None,
        refresh: tuple[frozenset[str], CoreObservation] | None = None,
    ) -> CoreObservation:
        t0 = time.perf_counter()
        if reactive_from is not None:
            core = await self.observer.reactive(reactive_from)
        elif refresh is not None:
            core = await self.observer.refresh(refresh[1], refresh[0])
        else:
            core = await self.observer.core()
        ...
```

After a decision (the line `core = await self._observe(reactive_from=core if in_flight else None)` at the end of the decision branch):

```python
            if in_flight:
                core = await self._observe(reactive_from=core)
            elif outcome.dispatched:
                core = await self._observe(refresh=(refresh_parts(candidate.kind), core))
            else:
                core = await self._observe(refresh=(frozenset({"blockers", "popup"}), core))
```

In `TurnLedger` (scheduler.py) add `full_observed: bool = False`. In the runner's `EndTurn` branch, before `unsupported = ...`:

```python
            if isinstance(step, EndTurn):
                if not in_flight and not ledger.full_observed:
                    ledger.full_observed = True
                    core = await self._observe()
                    continue
```

Set `ledger.full_observed = True` right after the initial `core = await self._observe()` at the top of `_run` (the first observation is full), and it resets naturally when a new `TurnLedger` is created for the next turn.

- [ ] **Step 6: Run tests**

Run: `uv run pytest tests -q`
Expected: all pass. If `test_drex_runner.py` tests that count `get_units` queries (`seen["units_queries"]` near line 357) change, update the expected count and explain in the assertion message.

- [ ] **Step 7: Commit**

```bash
uvx ruff format src/civ_mcp/drex/refresh.py src/civ_mcp/drex/live.py src/civ_mcp/drex/runner.py src/civ_mcp/drex/scheduler.py tests/drex/test_drex_refresh.py tests/drex/test_drex_runner.py tests/drex/drex_fakes.py
uvx ruff check src/civ_mcp/drex/refresh.py src/civ_mcp/drex/live.py src/civ_mcp/drex/runner.py src/civ_mcp/drex/scheduler.py
git add src/civ_mcp/drex/refresh.py src/civ_mcp/drex/live.py src/civ_mcp/drex/runner.py src/civ_mcp/drex/scheduler.py tests/drex/test_drex_refresh.py tests/drex/test_drex_runner.py tests/drex/drex_fakes.py
git commit -m "Refresh only the observation parts an action can change"
```

---

### Task 6: Prechecks reuse the decision's own reads (C3)

**Files:**
- Modify: `src/civ_mcp/drex/observation.py` (`DecisionInputs.progress`)
- Modify: `src/civ_mcp/drex/live.py` (`inputs()` fills `progress` for RESEARCH/CIVIC)
- Modify: `src/civ_mcp/drex/executor.py:180-262`
- Modify: `src/civ_mcp/drex/runner.py` (pass `inputs=` to `execute`)
- Test: `tests/drex/test_drex_executor.py`

**Interfaces:**
- Produces: `Executor.execute(candidate, point, *, current_version, turn, inputs: DecisionInputs | None = None)`. When `inputs` is given and `point.observation_version == current_version`, `_unit_space` uses `inputs.action_space`, the production precheck uses `inputs.city` and `inputs.production_options`, and research/civic uses `inputs.progress`. Eligibility (`check_eligibility`) is still queried (one round trip) because it is not part of the observation.

- [ ] **Step 1: Write the failing tests**

Add to `tests/drex/test_drex_executor.py` (follow the file's existing helpers for building a candidate/point from `FakeGame`; they are named like `_point_for` / `_candidate` there, reuse them):

```python
def test_precheck_reuses_provided_action_space_without_a_query():
    game = FakeGame()
    obs = LiveObserver(game)
    core = asyncio.run(obs.core())
    spec = DecisionSpec(DecisionCategory.UNIT, f"unit:{fx.WARRIOR_ID}")
    inputs = asyncio.run(obs.inputs(spec, core))
    point, _ = build_decision_point(spec, core, inputs, DecisionMemory(), objective="o", decision_id="T5#1", max_options=255)
    cand = next(c for c in point.candidates if c.kind is ActionKind.MOVE_UNIT)
    before = [c[0] for c in game.calls].count("get_unit_action_space")
    outcome = asyncio.run(Executor(game, sleep=_no_sleep).execute(cand, point, current_version=obs.version, turn=5, inputs=inputs))
    after = [c[0] for c in game.calls].count("get_unit_action_space")
    assert outcome.status in (OutcomeStatus.CONFIRMED, OutcomeStatus.PENDING)
    assert after == before  # no fresh action-space read for the precheck


def test_precheck_queries_when_inputs_are_from_an_older_version():
    game = FakeGame()
    obs = LiveObserver(game)
    core = asyncio.run(obs.core())
    spec = DecisionSpec(DecisionCategory.UNIT, f"unit:{fx.WARRIOR_ID}")
    inputs = asyncio.run(obs.inputs(spec, core))
    point, _ = build_decision_point(spec, core, inputs, DecisionMemory(), objective="o", decision_id="T5#1", max_options=255)
    cand = next(c for c in point.candidates if c.kind is ActionKind.MOVE_UNIT)
    asyncio.run(obs.core())  # version moves on
    outcome = asyncio.run(Executor(game, sleep=_no_sleep).execute(cand, point, current_version=obs.version, turn=5, inputs=inputs))
    assert outcome.status is OutcomeStatus.REJECTED and outcome.reason == "stale_observation"


def test_research_precheck_reuses_progress_from_inputs():
    game = FakeGame()
    obs = LiveObserver(game)
    core = asyncio.run(obs.core())
    spec = DecisionSpec(DecisionCategory.RESEARCH, "empire")
    inputs = asyncio.run(obs.inputs(spec, core))
    assert inputs.progress is not None
    point, _ = build_decision_point(spec, core, inputs, DecisionMemory(), objective="o", decision_id="T5#2", max_options=255)
    cand = point.candidates[0]
    n = game.query_counts["get_progress_types"]
    asyncio.run(Executor(game, sleep=_no_sleep).execute(cand, point, current_version=obs.version, turn=5, inputs=inputs))
    # one read for verify (postcondition), none for the precheck
    assert game.query_counts["get_progress_types"] == n + 1
```

(`FakeGame.get_progress_types` must count into `query_counts` — done in Task 5.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/drex/test_drex_executor.py -k "reuses or older_version" -v`
Expected: FAIL with `TypeError: Executor.execute() got an unexpected keyword argument 'inputs'`

- [ ] **Step 3: Implement**

`observation.py`: add `progress: lq.ProgressTypes | None = None` to `DecisionInputs`.

`live.py` `inputs()`: for `DecisionCategory.RESEARCH | DecisionCategory.CIVIC` return `DecisionInputs(progress=core.progress)` (today they return `DecisionInputs()`; check the `match` and add the case).

`executor.py`:

```python
    async def execute(self, candidate, point, *, current_version, turn, inputs=None):
        started = time.perf_counter()
        self._known = inputs if inputs is not None and point.observation_version == current_version else None
        try:
            outcome = await self._execute(candidate, point, current_version, turn)
        finally:
            self._known = None
        outcome.elapsed_ms = ...
```

`_unit_space`: if `self._known is not None and self._known.action_space is not None and self._known.action_space.unit_id == ref.unit_id`, use it instead of querying; then apply the same identity/position/moves checks. Production precheck: if `self._known is not None and self._known.city is not None and self._known.city.city_id == p.city_id`, use `self._known.city` and `self._known.production_options`; else query as today. Research/civic: use `self._known.progress` when present, else `await gs.get_progress_types()`.

- [ ] **Step 4: Runner passes inputs**

In `runner.py` the call `self.executor.execute(candidate, point, current_version=self.observer.version, turn=core.turn)` becomes `... , inputs=inputs)`.

- [ ] **Step 5: Run tests, commit**

```bash
uv run pytest tests -q
uvx ruff format src/civ_mcp/drex/executor.py src/civ_mcp/drex/live.py src/civ_mcp/drex/observation.py src/civ_mcp/drex/runner.py tests/drex/test_drex_executor.py
git add src/civ_mcp/drex/executor.py src/civ_mcp/drex/live.py src/civ_mcp/drex/observation.py src/civ_mcp/drex/runner.py tests/drex/test_drex_executor.py
git commit -m "Prechecks reuse the decision's own reads when the observation is current"
```

---

### Task 7: Confirm moves and production from dispatch output; skip redundant pre-dismiss (C4)

`GameState.move_unit` already dismisses popups (1+ round trips), moves (1), and reads the position back (1), returning `MOVING_TO|...|now_at:x,y`. The executor then polls `get_unit_state` up to six times. Production verify raises `LuaError`.

**Files:**
- Modify: `src/civ_mcp/game_state.py:234-262` (`move_unit(..., predismiss: bool = True)`)
- Modify: `src/civ_mcp/lua/cities.py:526-544`
- Modify: `src/civ_mcp/drex/executor.py` (verify MOVE_UNIT, SET_PRODUCTION; dispatch passes `predismiss=False` when the observation says `popup_state == "CLEAR"`)
- Modify: `src/civ_mcp/drex/candidates.py`/`executor.py` `dispatch_call` for MOVE_UNIT kwargs
- Test: `tests/drex/test_drex_executor.py`, `tests/drex/drex_fakes.py`

**Interfaces:**
- Produces: `DispatchCall.kwargs: dict[str, Any]` (default empty) used by `Executor._execute` as `getattr(gs, call.method)(*call.args, **call.kwargs)`; `Executor(gs, ..., popup_state_provider: Callable[[], str] | None = None)`; `ActionOutcome.reason == "arrived_from_dispatch"` for moves confirmed without polling; `"production_confirmed_from_dispatch"` for production.

- [ ] **Step 1: Write the failing tests**

```python
def test_move_confirmed_from_dispatch_output_without_polling():
    game = FakeGame()
    game.move_result_suffix = "|now_at:{x},{y}"  # fake returns MOVING_TO|...|now_at:x,y like the real game
    ... build a MOVE_UNIT candidate as in Task 6 ...
    n = [c[0] for c in game.calls].count("get_unit_state")
    outcome = asyncio.run(Executor(game, sleep=_no_sleep).execute(cand, point, current_version=obs.version, turn=5, inputs=inputs))
    assert outcome.status is OutcomeStatus.CONFIRMED and outcome.reason == "arrived_from_dispatch"
    assert [c[0] for c in game.calls].count("get_unit_state") == n  # no poll


def test_move_without_position_in_output_still_polls():
    game = FakeGame()
    game.move_result_suffix = ""
    ... same candidate ...
    outcome = asyncio.run(Executor(game, sleep=_no_sleep).execute(...))
    assert outcome.reason in ("arrived", "moved_partially", "no_position_change")


def test_production_confirmed_from_dispatch_when_readback_raises():
    from civ_mcp.connection import LuaError
    game = FakeGame()
    game.fail["verify_production"] = (LuaError("ERR: attempt to call nil"), False)
    ... build a SET_PRODUCTION candidate for the capital ...
    outcome = asyncio.run(Executor(game, sleep=_no_sleep).execute(...))
    assert outcome.status is OutcomeStatus.CONFIRMED
    assert outcome.reason == "production_confirmed_from_dispatch"


def test_move_skips_predismiss_when_observation_says_no_popup():
    game = FakeGame()
    ex = Executor(game, sleep=_no_sleep, popup_state_provider=lambda: "CLEAR")
    ... MOVE candidate ...
    asyncio.run(ex.execute(...))
    move = next(c for c in game.calls if c[0] == "move_unit")
    assert move[1][-1] is False  # predismiss flag recorded by the fake


def test_move_keeps_predismiss_when_a_popup_is_visible():
    game = FakeGame()
    ex = Executor(game, sleep=_no_sleep, popup_state_provider=lambda: "POPUP")
    ... MOVE candidate ...
    asyncio.run(ex.execute(...))
    move = next(c for c in game.calls if c[0] == "move_unit")
    assert move[1][-1] is True
```

Update `FakeGame.move_unit(self, unit_index, x, y, predismiss=True)` to record `(unit_index, x, y, predismiss)` and to return `f"MOVING_TO|{x},{y}|from:{ox},{oy}" + self.move_result_suffix.format(x=x, y=y)` with `self.move_result_suffix = "|now_at:{x},{y}"` default. Update `FakeGame.set_city_production` to return `f"PRODUCING|{item_name}|3 turns"` if it does not already. Look at the current fake return strings first and keep any existing tests green.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/drex/test_drex_executor.py -k "from_dispatch or predismiss" -v`
Expected: FAIL (`TypeError` on `popup_state_provider`, and `reason == "arrived"` instead of `arrived_from_dispatch`).

- [ ] **Step 3: Implement**

`game_state.py`:

```python
    async def move_unit(self, unit_index: int, target_x: int, target_y: int, predismiss: bool = True) -> str:
        if predismiss:
            try:
                await self.dismiss_popup()
            except Exception:
                pass
        ...unchanged...
```

`cities.py` `build_verify_production`: wrap the two engine calls:

```lua
local bq = pCity:GetBuildQueue()
local ok, cur = pcall(function() return bq:CurrentlyBuilding() end)
if not ok then print("NOT_SET|readback_error"); print("{SENTINEL}"); return end
if cur == "{item_name}" then
    local okT, turns = pcall(function() return bq:GetTurnsLeft() end)
    print("CONFIRMED|" .. (okT and tostring(turns) or "?") .. " turns")
else
    print("NOT_SET|current=" .. tostring(cur) .. "|expected={item_name}")
end
```

`executor.py`:

- `DispatchCall` gains `kwargs: dict[str, Any] = field(default_factory=dict)`; `to_record` includes it when non-empty. `_execute` calls `getattr(self.gs, call.method)(*call.args, **call.kwargs)`.
- `Executor.__init__(..., popup_state_provider=None)` stores it. In `_execute`, after `call = dispatch_call(candidate)`: `if candidate.kind is ActionKind.MOVE_UNIT and self._popup_state and self._popup_state() == "CLEAR": call = dataclasses.replace(call, kwargs={**call.kwargs, "predismiss": False})`.
- Verify MOVE_UNIT: before polling,

```python
                m = re.search(r"\|now_at:(\d+),(\d+)", raw)
                if m and (int(m.group(1)), int(m.group(2))) == (p.to_x, p.to_y):
                    return confirmed("arrived_from_dispatch", position=[p.to_x, p.to_y])
```

then the existing poll path.
- Verify SET_PRODUCTION: wrap the poll in `try/except LuaError`; on success as before; on exception or `False`, if `raw.startswith("PRODUCING|") and f"|{p.item_name}|" in raw + "|"` return `confirmed("production_confirmed_from_dispatch")`, else `_unconfirmed(raw, "production_not_observed")`.

Runner: construct `Executor(gs, popup_state_provider=lambda: self._last_core.popup_state if self._last_core else "POPUP")` when no executor is injected.

- [ ] **Step 4: Run tests, commit**

```bash
uv run pytest tests -q
uvx ruff format src/civ_mcp/game_state.py src/civ_mcp/lua/cities.py src/civ_mcp/drex/executor.py src/civ_mcp/drex/runner.py tests/drex/test_drex_executor.py tests/drex/drex_fakes.py
git add src/civ_mcp/game_state.py src/civ_mcp/drex/executor.py src/civ_mcp/drex/runner.py tests/drex/test_drex_executor.py tests/drex/drex_fakes.py
git add -p src/civ_mcp/lua/cities.py   # stage ONLY the build_verify_production hunk; leave the GetCulturalIdentity hunk unstaged
git commit -m "Confirm moves and production from dispatch output; skip pre-dismiss when no popup"
```

---

### Task 8: Spectator without contention and quiet during end turn (C6)

**Files:**
- Modify: `src/civ_mcp/spectator.py` (`PopupWatcher(conn, poll=True)`; `report(status)`; `CameraController.set_critical(bool)`, `quiet(bool)`)
- Modify: `src/civ_mcp/drex/spectate.py` (`popup_status`, `quiet`)
- Modify: `src/civ_mcp/drex/runner.py` (feed status after each observe; quiet around end turn)
- Test: `tests/drex/test_drex_spectate.py`

**Interfaces:**
- Produces: `Spectator.popup_status(state: str) -> None`, `Spectator.quiet(on: bool) -> None` added to the Protocol; `LiveSpectator(conn)` constructs `PopupWatcher(conn, poll=False)`; `PopupWatcher.report(status)` implements the same 1 s delay rule from reported statuses and calls `dismiss_popup` once; `CameraController.set_critical(flag)` replaces the per-hop diplomacy round trip; `CameraController.quiet(on)` holds hops while on.

- [ ] **Step 1: Write the failing tests**

```python
def test_spectator_is_quiet_during_end_turn(tmp_path):
    game = FakeGame()
    spec = RecordingSpectator()
    asyncio.run(_runner(game, tmp_path, spec).run())
    kinds = [e[0] for e in spec.events]
    i = kinds.index("quiet_on")
    assert "quiet_off" in kinds[i:]
    # no camera focus between quiet_on and quiet_off
    assert "focus" not in kinds[i : kinds.index("quiet_off", i)]


def test_runner_feeds_popup_status_from_each_observation(tmp_path):
    game = FakeGame()
    spec = RecordingSpectator()
    asyncio.run(_runner(game, tmp_path, spec).run())
    statuses = [e for e in spec.events if e[0] == "popup"]
    assert statuses and all(e[1] in ("CLEAR", "POPUP", "CRITICAL") for e in statuses)


def test_popup_watcher_external_mode_dismisses_after_delay(monkeypatch):
    from civ_mcp import spectator as sp

    calls = []

    async def fake_dismiss(conn):
        calls.append("dismiss")
        return "Dismissed X"

    monkeypatch.setattr("civ_mcp.game_lifecycle.dismiss_popup", fake_dismiss)

    async def scenario():
        w = sp.PopupWatcher(conn=None, poll=False)
        await w.report("POPUP", now=0.0)
        await w.report("POPUP", now=0.4)
        assert calls == []
        await w.report("POPUP", now=1.1)
        assert calls == ["dismiss"]
        await w.report("CRITICAL", now=2.0)
        await w.report("POPUP", now=2.5)
        await w.report("POPUP", now=3.0)
        assert calls == ["dismiss"]  # timer reset by CRITICAL; not yet 1s

    asyncio.run(scenario())


def test_camera_holds_hops_while_critical_or_quiet():
    class Conn:
        def __init__(self): self.lua = []
        async def execute_write(self, lua, timeout=5.0):
            self.lua.append(lua); return ["---END---"]

    async def scenario():
        conn = Conn()
        cam = CameraController(conn)
        cam.set_critical(True)
        cam.start()
        cam.push(1, 2)
        await asyncio.sleep(0.05)
        assert conn.lua == []          # held: diplomacy screen up
        cam.set_critical(False)
        await asyncio.sleep(0.05)
        assert any("LookAtPlot" in l for l in conn.lua)
        assert not any("DiplomacyActionView" in l for l in conn.lua)  # no per-hop check round trip
        await cam.stop()

    asyncio.run(scenario())
```

Extend `RecordingSpectator` with `popup_status(self, state)` → `("popup", state)` and `quiet(self, on)` → `("quiet_on",)`/`("quiet_off",)`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/drex/test_drex_spectate.py -v`
Expected: FAIL (`TypeError: __init__() got an unexpected keyword argument 'poll'`, missing `quiet_on`).

- [ ] **Step 3: Implement in `spectator.py`**

`CameraController`: add `self._critical = False`, `self._quiet = False`, `set_critical(flag)`, `quiet(on)`. In `_run`, replace the `_is_diplomacy_active()` loop with `while self._critical or self._quiet: await asyncio.sleep(0.1)`. Keep `_is_diplomacy_active` for the MCP server path by making the constructor take `check_diplomacy: bool = True` and using the old loop when it is `True`; `LiveSpectator` passes `check_diplomacy=False`.

`PopupWatcher.__init__(self, conn, poll: bool = True)`; `start()` creates the task only when `poll`. Add:

```python
    async def report(self, status: str, now: float | None = None) -> None:
        """External mode: the caller observed the popup state in its own read."""
        from civ_mcp.game_lifecycle import dismiss_popup

        now = asyncio.get_running_loop().time() if now is None else now
        if status != "POPUP":
            self._first_seen = None
            return
        if self._first_seen is None:
            self._first_seen = now
        elif now - self._first_seen >= POPUP_DISMISS_DELAY:
            self._first_seen = None
            await dismiss_popup(self._conn)
```

Refactor `_run` to call the same logic (`await self.report(status)`) so both modes share the rule.

- [ ] **Step 4: Implement in `spectate.py` and `runner.py`**

`Spectator` Protocol: add `def popup_status(self, state: str) -> None: ...` and `def quiet(self, on: bool) -> None: ...`. `LiveSpectator`: `self.popups = PopupWatcher(conn, poll=False)`, `self.camera = CameraController(conn, check_diplomacy=False)`; `popup_status(state)`: `self.camera.set_critical(state == "CRITICAL")` and schedule `asyncio.ensure_future(self.popups.report(state))` (dismissal must not block the decision loop; keep a reference to the task and await it in `stop()`); `quiet(on)`: `self.camera.quiet(on)` and set `self._quiet = on` so `popup_status` ignores reports while quiet.

Runner: in `_observe`, after the observation is built, `if self.spectator is not None: self.spectator.popup_status(core.popup_state)`. Around `outcome = await self._end_turn(self.gs)`: `spectator.quiet(True)` before, `spectator.quiet(False)` in a `finally`.

- [ ] **Step 5: Run tests, commit**

```bash
uv run pytest tests -q
uvx ruff format src/civ_mcp/spectator.py src/civ_mcp/drex/spectate.py src/civ_mcp/drex/runner.py tests/drex/test_drex_spectate.py
git add src/civ_mcp/spectator.py src/civ_mcp/drex/spectate.py src/civ_mcp/drex/runner.py tests/drex/test_drex_spectate.py
git commit -m "Spectator reads popup state from the observation and stays quiet during end turn"
```

---

### Task 9: End-turn trims and phase timing (C7) plus timing records and per-turn summary (C8)

**Files:**
- Modify: `src/civ_mcp/end_turn.py` (phase timings in decision-only mode; skip narration-only checks; 2.0 s sleeps → 0.5 s in the post-timeout and final-verification paths)
- Modify: `src/civ_mcp/drex/executor.py` (`ActionOutcome.roundtrips`)
- Modify: `src/civ_mcp/drex/runner.py` (`timing_ms.observe`, `timing_ms.roundtrips`, `turn.roundtrips`, `turn.phase_ms`, `speed` record, `on_turn` callback)
- Modify: `src/civ_mcp/drex/cli.py` (print one progress line per turn to stderr)
- Create: `scripts/drex_timing.py`
- Test: `tests/drex/test_drex_runner.py`, `tests/drex/test_drex_end_turn.py`, `tests/drex/test_drex_speed.py`

**Interfaces:**
- Produces: `EndTurnOutcome.phase_ms: dict[str, float]` (keys `pre_checks`, `pre_dismiss`, `request`, `poll`, `post`); `Runner(..., on_turn: Callable[[dict], None] | None = None)` called with the `speed` record each advanced turn; `ActionOutcome.roundtrips: int`.

- [ ] **Step 1: Write the failing tests**

```python
# test_drex_runner.py
def test_decision_records_carry_observe_time_and_roundtrips(tmp_path):
    game = FakeGame()
    game.conn.roundtrips, game.conn.roundtrip_ms = 0, 0.0   # FakeConn gains these counters, incremented by FakeGame on every query/action
    runner, _ = _runner(game, tmp_path)
    asyncio.run(runner.run())
    rec = next(r for r in _records(tmp_path) if r["type"] == "decision")
    assert set(rec["timing_ms"]) >= {"api", "execute", "observe", "roundtrips"}
    assert rec["timing_ms"]["roundtrips"] >= 1


def test_speed_record_written_per_advanced_turn_and_on_turn_called(tmp_path):
    game = FakeGame()
    seen = []
    log = DecisionLog(tmp_path / "run.jsonl", run_id="drex-test", secrets=[])
    runner = Runner(game, PreferSelector(), log, RunConfig(turns=1), end_turn=_fake_end_turn(game), on_turn=seen.append)
    asyncio.run(runner.run())
    speed = [r for r in _records(tmp_path) if r["type"] == "speed"]
    assert len(speed) == 1 and seen == [ {k: v for k, v in speed[0].items() if k not in ("seq", "ts", "run_id", "type")} ] or seen[0]["turn"] == speed[0]["turn"]
    assert {"turn", "decisions", "seconds", "roundtrips", "drex_seconds", "end_turn_seconds"} <= set(speed[0])
```

```python
# test_drex_end_turn.py (follow the file's existing fake GameState for execute_end_turn_typed)
def test_typed_outcome_reports_phase_timings():
    outcome = asyncio.run(execute_end_turn_typed(gs, decision_only=True))
    assert {"pre_checks", "pre_dismiss", "request", "poll", "post"} <= set(outcome.phase_ms)


def test_decision_only_mode_skips_narration_only_checks(monkeypatch):
    called = []
    monkeypatch.setattr(end_turn_mod, "_check_victory_proximity", _record("victory", called))
    monkeypatch.setattr(end_turn_mod, "_check_empire_warnings", _record("warnings", called))
    asyncio.run(execute_end_turn_typed(gs, decision_only=True))
    assert called == []
```

(`_record(name, sink)` returns an async function that appends `name` and returns `[]`.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/drex/test_drex_runner.py tests/drex/test_drex_end_turn.py -k "timing or speed or phase or narration" -v`
Expected: FAIL (`KeyError: 'observe'`, `AttributeError: phase_ms`).

- [ ] **Step 3: Implement `end_turn.py`**

- Add `phase_ms: dict[str, float] = field(default_factory=dict)` to `EndTurnOutcome`.
- In `execute_end_turn`, when `_decision_only(gs)`: skip the calls to `_check_victory_proximity` and `_check_empire_warnings` (their output only feeds the narration string). Keep `_check_save_scumming` (it detects wrong-save loads) and the World Congress gate.
- Record `gs.end_turn_phase_ms = {}` at the start of `execute_end_turn_typed`, and inside `execute_end_turn` stamp `gs.end_turn_phase_ms[name] = elapsed` around: the pre-checks block (sessions/deals), the pre-dismiss call, the end-turn request send, the polling loops (sum), and everything after advance (autosave, game-over check). Copy into `EndTurnOutcome.phase_ms`.
- Change the `await asyncio.sleep(2.0)` at lines ≈1370 and ≈1384 (post-timeout dismiss loop and final verification) to `0.5`, keeping loop counts so the total wait stays within a quarter of the old maximum. Leave the phase-2 30 s AI-turn schedule untouched.

- [ ] **Step 4: Implement runner and executor timing**

`ActionOutcome`: add `roundtrips: int = 0`. In `Executor.execute`, if `getattr(self.gs, "conn", None)` has `snapshot_counters`, take the counter before and after and set `outcome.roundtrips`. `FakeConn` gains `roundtrips = 0`, `roundtrip_ms = 0.0`, `snapshot_counters()`; `FakeGame._record` and each query method increment `self.conn.roundtrips += 1`.

Runner: in `_observe` measure `observe_ms` and round trips (via `snapshot_counters`) into `self._last_observe = {"ms": ..., "roundtrips": ...}`; the `decision` record's `timing_ms` gains `"observe": round(ms, 1)` (the observe that preceded this decision) and `"roundtrips": observe_rt + outcome.roundtrips`. Per turn, accumulate `decisions`, `drex_ms`, `roundtrips`, wall time; on `advanced`, write:

```python
            speed = {
                "turn": outcome.turn_before, "decisions": turn_decisions,
                "seconds": round(turn_wall_s, 2), "roundtrips": turn_roundtrips,
                "drex_seconds": round(turn_api_ms / 1000.0, 2),
                "end_turn_seconds": round(elapsed / 1000.0, 2),
                "phase_ms": outcome.phase_ms,
            }
            self.log.write("speed", speed)
            if self._on_turn: self._on_turn(speed)
```

`turn` records gain `"roundtrips": turn_roundtrips` and `"phase_ms": outcome.phase_ms`.

CLI: pass `on_turn=lambda s: _err(f"T{s['turn']}: {s['decisions']} decisions in {s['seconds']}s, {s['roundtrips']} round trips, Drex {s['drex_seconds']}s, end turn {s['end_turn_seconds']}s")`.

- [ ] **Step 5: `scripts/drex_timing.py`**

```python
#!/usr/bin/env python3
"""Summarise timing from a civ-drex decision log: python scripts/drex_timing.py logs/drex/<run>.jsonl"""
import json, statistics, sys

def main(path):
    decisions, speed = [], []
    for line in open(path):
        r = json.loads(line)
        if r["type"] == "decision": decisions.append(r["timing_ms"])
        elif r["type"] == "speed": speed.append(r)
    if decisions:
        for k in ("api", "execute", "observe", "roundtrips"):
            vals = [d.get(k, 0) for d in decisions]
            print(f"{k:>10}: median {statistics.median(vals):8.1f}  p90 {sorted(vals)[int(0.9*(len(vals)-1))]:8.1f}  n={len(vals)}")
    for s in speed:
        print(f"T{s['turn']:>4}: {s['decisions']:3d} dec {s['seconds']:6.1f}s {s['roundtrips']:4d} rt drex {s['drex_seconds']:5.1f}s end_turn {s['end_turn_seconds']:5.1f}s")

if __name__ == "__main__":
    main(sys.argv[1])
```

- [ ] **Step 6: Run tests, commit**

```bash
uv run pytest tests -q
uvx ruff format src/civ_mcp/end_turn.py src/civ_mcp/drex/executor.py src/civ_mcp/drex/runner.py src/civ_mcp/drex/cli.py scripts/drex_timing.py tests/drex/test_drex_runner.py tests/drex/test_drex_end_turn.py tests/drex/drex_fakes.py
git add src/civ_mcp/end_turn.py src/civ_mcp/drex/executor.py src/civ_mcp/drex/runner.py src/civ_mcp/drex/cli.py scripts/drex_timing.py tests/drex/test_drex_runner.py tests/drex/test_drex_end_turn.py tests/drex/drex_fakes.py
git commit -m "Time every decision and turn; trim end-turn narration checks and sleeps in decision-only mode"
```

- [ ] **Step 7: Live measurement gate**

Restart the live run on this code (`uv run civ-drex play --turns 500`), let it play 5 turns, then `uv run python scripts/drex_timing.py logs/drex/<newest>.jsonl`. Expected: median `roundtrips` per decision ≤ 3, median `observe` ≤ 400 ms, turns with ~8 decisions under 15 s excluding `end_turn_seconds`. If `roundtrips` median is above 3, list which decision kinds exceed it (add `kind` to the print) and fix before Task 10; the remaining source will be dispatch methods that do their own extra reads (e.g. `move_unit`'s position read, which is what makes the dispatch confirmable and stays).

---

### Task 10: Selector waits instead of pausing (A1)

**Files:**
- Modify: `src/civ_mcp/drex/selectors.py:26-31, 88-140, 182-190`
- Modify: `src/civ_mcp/drex/client.py` (`DrexConfig.max_backoff_s`, env `DREX_MAX_BACKOFF_S`)
- Modify: `src/civ_mcp/drex/runner.py` (remove `SelectionPaused` handling; `selection_waiting` records)
- Modify: `src/civ_mcp/drex/cli.py` (`refresh_client`)
- Test: `tests/drex/test_drex_selectors.py`, `tests/drex/test_drex_runner.py`, `tests/drex/test_drex_client.py`

**Interfaces:**
- Produces:

```python
class DrexSelector:
    def __init__(self, client, *, max_backoff_s: float = 60.0, backoff_s: float = 1.0,
                 warn_after: int = 2, sleep=asyncio.sleep,
                 on_wait: Callable[[dict[str, Any]], None] | None = None,
                 refresh_client: Callable[[], Awaitable[ChoiceClient]] | None = None)
```

  `choose()` never raises `DrexError`/`DecisionError`; it loops until a valid decision. `on_wait` receives `{"attempt": n, "error": str, "error_class": str, "sleep_s": float, "retryable": bool}` before each sleep. `SelectionPaused` is kept only for `select()`'s single-candidate-without-forced-rule case, renamed `ControllerFilteringError` (it is a controller bug, not an API state) and handled in the runner as a `no_candidates` record plus `scheduler.exhaust`. `DrexConfig.max_backoff_s: float = 60.0` from `DREX_MAX_BACKOFF_S`.

- [ ] **Step 1: Write the failing tests**

Replace `test_exhausted_retries_pause_without_a_decision` and `test_invalid_answers_are_retried_then_pause` in `test_drex_selectors.py` with:

```python
def _drex(client, **kw):
    kw.setdefault("backoff_s", 1.0); kw.setdefault("max_backoff_s", 60.0)
    kw.setdefault("sleep", _no_sleep)
    return DrexSelector(client, **kw)


def test_429_storm_ends_in_a_decision_not_a_pause():
    client = _ScriptedClient(*(DrexUnavailable("HTTP 429") for _ in range(5)), GOOD)
    waits = []
    result = asyncio.run(_drex(client, on_wait=waits.append).choose(_point()))
    assert result.decision.candidate_id == "research:TECHNOLOGY_POTTERY"
    assert len(client.calls) == 6 and len(waits) == 5
    assert [w["sleep_s"] for w in waits] == [1.0, 2.0, 4.0, 8.0, 16.0]
    assert all(w["retryable"] for w in waits)


def test_backoff_is_capped_and_honours_retry_after():
    errs = [DrexUnavailable("HTTP 429", retry_after_s=90.0)] + [DrexUnavailable("HTTP 529") for _ in range(7)]
    client = _ScriptedClient(*errs, GOOD)
    waits = []
    asyncio.run(_drex(client, on_wait=waits.append).choose(_point()))
    assert waits[0]["sleep_s"] == 60.0            # retry-after 90 capped to max_backoff
    assert max(w["sleep_s"] for w in waits) == 60.0


def test_auth_error_refreshes_client_and_continues():
    bad = _ScriptedClient(DrexAuthError("HTTP 401: bad key"))
    good = _ScriptedClient(GOOD)
    refreshed = []

    async def refresh():
        refreshed.append(True)
        return good

    waits = []
    result = asyncio.run(_drex(bad, refresh_client=refresh, on_wait=waits.append).choose(_point()))
    assert result.decision.candidate_id == "research:TECHNOLOGY_POTTERY"
    assert refreshed == [True] and waits[0]["retryable"] is False and waits[0]["sleep_s"] == 60.0


def test_every_wait_is_reported():
    client = _ScriptedClient(MISSING_OPTION, DrexUnavailable("timeout"), GOOD)
    waits = []
    asyncio.run(_drex(client, on_wait=waits.append).choose(_point()))
    assert [w["error_class"] for w in waits] == ["DecisionError", "DrexUnavailable"]


def test_single_filtered_candidate_is_a_controller_error():
    from civ_mcp.drex.selectors import ControllerFilteringError
    point = _point(("TECHNOLOGY_POTTERY",))
    with pytest.raises(ControllerFilteringError):
        asyncio.run(select(point, _drex(_ScriptedClient())))
```

Rewrite `test_selector_pause_checkpoints_and_stops_without_fallback` in `test_drex_runner.py` as:

```python
def test_selector_waits_are_logged_and_the_run_continues(tmp_path):
    game = FakeGame()

    class FlakySelector(PreferSelector):
        def __init__(self):
            super().__init__(); self.failed = False
        async def choose(self, point):
            if not self.failed:
                self.failed = True
                self.on_wait({"attempt": 1, "error": "HTTP 429", "error_class": "DrexUnavailable", "sleep_s": 1.0, "retryable": True})
            return await super().choose(point)

    sel = FlakySelector()
    runner, checkpoints = _runner(game, tmp_path, selector=sel)
    sel.on_wait = runner.selection_waiting
    result = asyncio.run(runner.run())
    assert result.stop_reason == "turn_budget_reached" and checkpoints == []
    waits = [r for r in _records(tmp_path) if r["type"] == "selection_waiting"]
    assert waits and waits[0]["error_class"] == "DrexUnavailable" and "decision_id" in waits[0]
```

Add to `test_drex_client.py`: `DrexConfig.from_env({"DREX_API_KEY": "nace_sk_x", "DREX_MAX_BACKOFF_S": "30"}, env_file=None).max_backoff_s == 30.0` and default `60.0`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/drex/test_drex_selectors.py tests/drex/test_drex_client.py -v`
Expected: FAIL (`TypeError` for `max_backoff_s`/`on_wait`, `SelectionPaused` raised).

- [ ] **Step 3: Implement `selectors.py`**

```python
class ControllerFilteringError(Exception):
    """Exactly one candidate survived controller filtering with no forced rule.
    A controller bug: the decision is dropped, never executed."""


class DrexSelector:
    name = "drex"

    def __init__(self, client, *, backoff_s=1.0, max_backoff_s=60.0, warn_after=2,
                 sleep=asyncio.sleep, on_wait=None, refresh_client=None):
        self._client = client
        self._backoff_s = backoff_s
        self._max_backoff_s = max_backoff_s
        self._warn_after = warn_after
        self._sleep = sleep
        self._on_wait = on_wait
        self._refresh_client = refresh_client

    async def choose(self, point: DecisionPoint) -> SelectionResult:
        request = build_request(point)
        attempts: list[dict[str, Any]] = []
        n = 0
        while True:
            try:
                answer = await self._client.choose(**request)
                decision = resolve_choice(point, answer, selector=self.name)
            except DecisionError as e:
                wait, retryable, err = self._wait_for(n), True, f"answer: {e}"
                cls = "DecisionError"
            except DrexError as e:
                retryable = bool(getattr(e, "retryable", False))
                retry_after = getattr(e, "retry_after_s", None) or 0.0
                wait = min(max(self._wait_for(n), retry_after), self._max_backoff_s) if retryable else self._max_backoff_s
                err, cls = f"{type(e).__name__}: {e}", type(e).__name__
            else:
                attempts.append({"attempt": n + 1, "ok": True, "latency_ms": answer.latency_ms, "request_id": answer.request_id})
                return SelectionResult(decision, request, attempts)
            n += 1
            attempts.append({"attempt": n, "ok": False, "error": err})
            if self._on_wait is not None:
                self._on_wait({"attempt": n, "error": err, "error_class": cls, "sleep_s": wait, "retryable": retryable,
                               "warn": n >= self._warn_after})
            await self._sleep(wait)
            if not retryable and self._refresh_client is not None:
                self._client = await self._refresh_client()
            if len(attempts) > 50:
                attempts = attempts[-50:]   # keep the record bounded on long outages

    def _wait_for(self, n: int) -> float:
        return min(self._backoff_s * (2 ** n), self._max_backoff_s)
```

`select()`: replace `raise SelectionPaused(...)` with `raise ControllerFilteringError(...)`. Delete `SelectionPaused`. Update the module docstring.

`client.py`: `DrexConfig.max_backoff_s: float = 60.0`, parsed from `DREX_MAX_BACKOFF_S`.

- [ ] **Step 4: Runner and CLI**

Runner: add method

```python
    def selection_waiting(self, info: dict[str, Any]) -> None:
        self.log.write("selection_waiting", {**info, "turn": self._last_core.turn if self._last_core else None,
                                             "decision_id": self._current_decision_id})
```

Set `self._current_decision_id = decision_id` before `select(...)`. Replace the `except SelectionPaused` block with:

```python
            try:
                result = await select(point, self.selector)
            except ControllerFilteringError as e:
                self.scheduler.exhaust(ledger, step)
                self.log.write("no_candidates", {"turn": core.turn, "decision_id": decision_id,
                               "category": str(step.category), "entity": step.entity,
                               "exclusions": [dataclasses.asdict(x) for x in excluded],
                               "controller_error": str(e)})
                core = await self._observe(refresh=(frozenset({"blockers", "popup"}), core))
                continue
```

CLI `_run_live`: build the selector as

```python
        selector = DrexSelector(client, backoff_s=cfg.backoff_s, max_backoff_s=cfg.max_backoff_s,
                                warn_after=cfg.max_retries, refresh_client=_refresh)
```

where

```python
        async def _refresh():
            nonlocal client, cfg
            new_cfg = _load_config(args)          # re-reads the env file
            if client is not None:
                await client.aclose()
            cfg = new_cfg
            client = DrexClient(cfg)
            secrets.append(cfg.api_key); log.add_secret(cfg.api_key)
            return client
```

(`DecisionLog.add_secret(value)` — add this small method to `decision_log.py`: append to the redaction list.) After constructing the runner: `selector._on_wait = runner.selection_waiting` (or pass `on_wait=` by constructing the runner first; choose the order that avoids the private attribute: create `log`, `runner` with `selector=None` placeholder is not allowed, so build selector with `on_wait=lambda info: runner.selection_waiting(info)` referencing the later-bound name — Python closures resolve `runner` at call time, so this works). Remove `"selector_paused"` from any `SUCCESS_STOPS`/exit-code logic; the stdout line from `on_wait` with `warn=True` prints `waiting for Drex: <error> (retry in Ns)` to stderr.

- [ ] **Step 5: Run tests, commit**

```bash
uv run pytest tests -q
uvx ruff format src/civ_mcp/drex/selectors.py src/civ_mcp/drex/client.py src/civ_mcp/drex/runner.py src/civ_mcp/drex/cli.py src/civ_mcp/drex/decision_log.py tests/drex/test_drex_selectors.py tests/drex/test_drex_runner.py tests/drex/test_drex_client.py
git add src/civ_mcp/drex/selectors.py src/civ_mcp/drex/client.py src/civ_mcp/drex/runner.py src/civ_mcp/drex/cli.py src/civ_mcp/drex/decision_log.py tests/drex/test_drex_selectors.py tests/drex/test_drex_runner.py tests/drex/test_drex_client.py
git commit -m "Selector waits for Drex indefinitely with capped backoff; never pauses the run"
```

---

### Task 11: Loop guards become end turn, decisions or housekeeping (A3)

**Files:**
- Modify: `src/civ_mcp/drex/scheduler.py:164-190` (no `Stop` for budget/sessions), `TurnLedger`
- Modify: `src/civ_mcp/drex/enumerate.py` (diplomacy `EXIT` candidate when failure budget is spent)
- Modify: `src/civ_mcp/drex/runner.py` (informational stuck, unsupported blockers wait, blocked end turn repeats, identity change)
- Test: `tests/drex/test_drex_scheduler.py`, `tests/drex/test_drex_runner.py`, `tests/drex/test_drex_enumerate.py`

**Interfaces:**
- Produces: `Scheduler.next()` returns `DecisionSpec | EndTurn` only (`Stop` class deleted). `Scheduler.session_exhausted(ledger, spec) -> bool`. `build_diplomacy_candidates(..., allow_exit: bool)` adds `Candidate(kind=DIPLOMACY_RESPOND, params=DiplomacyParams(player, "EXIT"), label="Close the screen")`. `RunConfig.max_decisions_per_turn = 200`, `RunConfig.unsupported_wait_s = 30.0`, `RunConfig.blocked_repeats_before_dismiss = 3`. New log records `budget_end_turn`, `unsupported_blocker`, `identity_changed`, `informational_session_stuck`, `end_turn_blocked_repeat`.

- [ ] **Step 1: Write the failing tests**

`test_drex_scheduler.py`: change the two `Stop` assertions (lines ≈173 and ≈192):

```python
def test_budget_exhausted_ends_the_turn_instead_of_stopping():
    ...same setup as before...
    assert isinstance(step, EndTurn)


def test_unresolved_session_after_failures_offers_exit_then_ends_turn():
    ...same setup: session open, failures at max...
    step = sched.next(core, ledger)
    assert isinstance(step, DecisionSpec) and step.category is DecisionCategory.DIPLOMACY
    assert sched.session_exhausted(ledger, step) is True
    # once the EXIT decision has also been noted, the scheduler moves on
    sched.note(ledger, step, ActionKind.DIPLOMACY_RESPOND, ActionOutcome(OutcomeStatus.REJECTED, "x", True))
    assert isinstance(sched.next(core, ledger), EndTurn)
```

`test_drex_enumerate.py`:

```python
def test_diplomacy_candidates_include_exit_only_when_allowed():
    ...build a session as the file does...
    plain = build_diplomacy_candidates(session, allow_exit=False)
    with_exit = build_diplomacy_candidates(session, allow_exit=True)
    assert not any(c.params.response == "EXIT" for c in plain)
    exit_c = next(c for c in with_exit if c.params.response == "EXIT")
    assert exit_c.label == "Close the screen" and exit_c.kind is ActionKind.DIPLOMACY_RESPOND
```

`test_drex_runner.py`: rewrite the four stop tests:

```python
def test_game_identity_change_is_logged_and_the_run_continues(tmp_path):
    ...same setup that flips game.civ mid-run...
    assert result.stop_reason == "turn_budget_reached"
    assert any(r["type"] == "identity_changed" for r in _records(tmp_path))


def test_persistently_blocked_end_turn_dismisses_then_keeps_going(tmp_path):
    game = FakeGame()
    game.extra_blockers = [("ENDTURN_BLOCKING_UNITS", "ghost")]   # the fake end turn stays blocked
    ...use a selector that skips everything; limit with RunConfig(turns=1, unsupported_wait_s=0.0) and a fake sleep...
    runner.max_loop_iterations = 40   # test-only guard attribute honoured by the runner: after N iterations it returns "interrupted"
    result = asyncio.run(runner.run())
    types = [r["type"] for r in _records(tmp_path)]
    assert "end_turn_blocked_repeat" in types
    assert any(c[0] == "dismiss_popup" for c in game.calls)
    assert result.stop_reason == "interrupted"


def test_unsupported_blocker_waits_and_logs_instead_of_stopping(tmp_path):
    game = FakeGame()
    game.extra_blockers = [("ENDTURN_BLOCKING_ARTIFACT", "Choose artifact")]
    slept = []
    runner, checkpoints = _runner(game, tmp_path, unsupported_wait_s=30.0)
    runner._sleep = lambda s: _record_sleep(slept, s)
    runner.max_loop_iterations = 10
    result = asyncio.run(runner.run())
    assert result.stop_reason == "interrupted" and checkpoints == []
    assert any(r["type"] == "unsupported_blocker" and r["blockers"] == ["ENDTURN_BLOCKING_ARTIFACT"] for r in _records(tmp_path))
    assert slept and slept[0] == 30.0


def test_informational_session_stuck_is_logged_not_fatal(tmp_path):
    ...same setup as the existing stuck-session test...
    assert result.stop_reason == "turn_budget_reached"
    assert any(r["type"] == "informational_session_stuck" for r in _records(tmp_path))
```

Delete `test_blocked_end_turn_with_nothing_new_to_decide_is_not_retried` and the `unsupported_blocker:world_congress` assertion test at line ≈413 (World Congress pending now takes the `unsupported_blocker` wait path until Phase 4).

`FakeGame` needs `async def dismiss_popup(self)` recording `("dismiss_popup", ())` and returning `"Dismissed nothing"`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/drex/test_drex_scheduler.py tests/drex/test_drex_runner.py tests/drex/test_drex_enumerate.py -v`
Expected: FAIL on the rewritten tests (Stop still returned; `allow_exit` unknown; stop reasons still produced).

- [ ] **Step 3: Implement `scheduler.py`**

- Delete `class Stop`. `next_reactive` returns `DecisionSpec | None`:
  - budget: `if ledger.decisions >= self.max_decisions_per_turn: ledger.budget_hit = True; return None` (`TurnLedger.budget_hit: bool = False`); `_next_proactive` returns `EndTurn()` immediately when `ledger.budget_hit`.
  - sessions: drop the `deal_summary` stop (a session with a deal summary is a normal DIPLOMACY decision). When `not self._open(ledger, spec)`: if `key_for(spec) not in ledger.exit_offered`: `ledger.exit_offered.add(key)`; return `spec` (the runner asks `session_exhausted` to enumerate with `allow_exit=True`); else `continue` to the next session.
  - deals: when `not self._open`: `continue`.
- `def session_exhausted(self, ledger, spec) -> bool: return key_for(spec) in ledger.exit_offered`.
- `TurnLedger` gains `exit_offered: set[str] = field(default_factory=set)`, `budget_hit: bool = False`.

`enumerate.py`: `build_diplomacy_candidates(session, *, allow_exit=False)`; when `allow_exit`, append the EXIT candidate. Thread the flag through `points.build_decision_point(..., allow_exit=False)`; the runner passes `allow_exit=self.scheduler.session_exhausted(ledger, step)`. The executor's `DIPLOMACY_RESPOND` precheck must accept `"EXIT"` (look at `_precheck` line ≈349 where responses are validated against `("POSITIVE", "NEGATIVE")` and add `"EXIT"`); dispatch is the existing `diplomacy_respond(player, "EXIT")`, which the runner already uses for informational sessions.

- [ ] **Step 4: Implement `runner.py`**

- `RunConfig`: `max_decisions_per_turn: int = 200`, `unsupported_wait_s: float = 30.0`, `blocked_repeats_before_dismiss: int = 3`. Runner gains `self._sleep = asyncio.sleep` and `self.max_loop_iterations: int | None = None` (tests only; when set, the loop returns `self._stop("interrupted", core, checkpoint=False)` after that many iterations).
- Identity: replace the `game_identity_changed` stop with: log `identity_changed` `{"from": [civ, seed], "to": [...]}`, `identity = core.game_identity`, `self.memory = DecisionMemory(); self.memory.bind_game(identity)`, `self.observer._wonders = None`, `ledger = TurnLedger(turn=core.turn)`, continue.
- Informational stuck: replace the stop with `self.log.write("informational_session_stuck", {...})`; `continue` past that player for this turn by adding it to `ledger.stuck_sessions: set[int]` and having `informational_sessions` calls skip those ids (pass `exclude=ledger.stuck_sessions`).
- Budget: when `ledger.budget_hit` first becomes true, log `budget_end_turn` `{"turn", "decisions"}`.
- Remove `if isinstance(step, Stop)`.
- Unsupported blockers: replace the `unsupported_blocker:` stop with

```python
                    if unsupported:
                        self.log.write("unsupported_blocker", {"turn": core.turn, "blockers": unsupported,
                                       "wait_s": self.cfg.unsupported_wait_s})
                        if self.cfg.dry_run:
                            return await self._stop("dry_run_complete", core, checkpoint=False)
                        await self._sleep(self.cfg.unsupported_wait_s)
                        core = await self._observe()
                        continue
```

  (Phase 2 turns these into decisions; until then the run waits, visibly, and a restart on newer code picks up.)
- World Congress pending in the end-turn outcome: same wait path with `blockers=["world_congress"]`.
- Blocked end turn with no progress: replace the `end_turn_blocked` stop with a counter `ledger.blocked_repeats += 1`; when it reaches `blocked_repeats_before_dismiss`: `raw = await self.gs.dismiss_popup()`, log `end_turn_blocked_repeat` `{"turn", "blockers": _describe_block(blocked), "dismissed": raw}`, reset the counter, set `retry_allowed = True`, `core = await self._observe()`, continue. Below the threshold: `core = await self._observe()` and continue (the fresh observation may reveal a new decision).
- `end_turn_<other status>`: log `end_turn_status` `{"status": outcome.status}` and continue after a full observe (no stop).

- [ ] **Step 5: Run tests, commit**

```bash
uv run pytest tests -q
uvx ruff format src/civ_mcp/drex/scheduler.py src/civ_mcp/drex/enumerate.py src/civ_mcp/drex/points.py src/civ_mcp/drex/executor.py src/civ_mcp/drex/runner.py tests/drex/test_drex_scheduler.py tests/drex/test_drex_runner.py tests/drex/test_drex_enumerate.py tests/drex/drex_fakes.py
git add src/civ_mcp/drex/scheduler.py src/civ_mcp/drex/enumerate.py src/civ_mcp/drex/points.py src/civ_mcp/drex/executor.py src/civ_mcp/drex/runner.py tests/drex/test_drex_scheduler.py tests/drex/test_drex_runner.py tests/drex/test_drex_enumerate.py tests/drex/drex_fakes.py
git commit -m "Loop guards end the turn, offer Drex the exit, or wait; no stops"
```

---

### Task 12: Game connection recovery and relaunch (A2)

**Files:**
- Modify: `src/civ_mcp/drex/runner.py` (`run()` recovery loop, `_recover()`)
- Modify: `src/civ_mcp/drex/cli.py` (`GAME_DEAD_AFTER_S` from env; `relaunch` hook wiring)
- Test: `tests/drex/test_drex_recovery.py` (create)

**Interfaces:**
- Produces:

```python
Runner(..., relaunch: Callable[[], Awaitable[str]] | None = None, clock: Callable[[], float] = time.monotonic)
RunConfig.game_dead_after_s: float = 120.0
RunConfig.reconnect_max_backoff_s: float = 30.0
```

  `run()` catches `(ConnectionError, OSError, LuaError, asyncio.TimeoutError)` from `_run()`; logs `game_io_error` `{"error", "phase": "observe|execute|end_turn|unknown", "attempt"}`; calls `await self.gs.conn.reconnect()` with backoff 1, 2, 4 … capped at `reconnect_max_backoff_s`; after `game_dead_after_s` of failed reconnects calls `relaunch()` once per outage, then keeps reconnecting; then re-enters `_run()` **resuming state** (turn counters, `_start_turn`, header already written: `_run(resume=True)` skips the header). Any other `Exception` is logged as `runner_error` with `traceback`, a checkpoint is written, and the loop re-enters after the same backoff. Dispatch errors are untouched (the executor reconciles them; they never reach `run()`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/drex/test_drex_recovery.py
"""Tuner errors reconnect; the run never ends for them."""

import asyncio

import drex_fixtures as fx
from drex_fakes import FakeGame
from test_drex_runner import PreferSelector, _fake_end_turn, _records

from civ_mcp.connection import LuaError
from civ_mcp.drex.decision_log import DecisionLog
from civ_mcp.drex.runner import RunConfig, Runner


class _Clock:
    def __init__(self): self.t = 0.0
    def __call__(self): return self.t


def _runner(game, tmp_path, **kw):
    log = DecisionLog(tmp_path / "run.jsonl", run_id="drex-test", secrets=[])
    slept = []

    async def sleep(s):
        slept.append(s)
        kw["clock"].t += s

    r = Runner(game, PreferSelector(), log, RunConfig(turns=1), end_turn=_fake_end_turn(game), **kw)
    r._sleep = sleep
    return r, slept


def test_lua_error_during_observe_reconnects_and_continues(tmp_path):
    game = FakeGame()
    game.fail["get_units"] = (LuaError("ERR: boom"), False)
    game.conn.reconnects = 0

    async def reconnect(): game.conn.reconnects += 1
    game.conn.reconnect = reconnect

    clock = _Clock()
    runner, slept = _runner(game, tmp_path, clock=clock)
    result = asyncio.run(runner.run())
    assert result.stop_reason == "turn_budget_reached"
    assert game.conn.reconnects == 1 and slept[:1] == [1.0]
    recs = _records(tmp_path)
    assert [r["type"] for r in recs].count("header") == 1
    err = next(r for r in recs if r["type"] == "game_io_error")
    assert err["phase"] == "observe" and "LuaError" in err["error"]


def test_game_dead_triggers_relaunch_once_then_continues(tmp_path):
    game = FakeGame()
    failures = {"n": 0}
    orig = game.get_units

    async def flaky():
        if failures["n"] < 200:
            failures["n"] += 1
            raise ConnectionError("socket closed")
        return await orig()
    game.get_units = flaky

    async def reconnect():
        if failures["n"] < 200:
            raise ConnectionError("refused")
    game.conn.reconnect = reconnect

    relaunched = []

    async def relaunch():
        relaunched.append(True)
        failures["n"] = 200
        return "relaunched"

    clock = _Clock()
    runner, slept = _runner(game, tmp_path, clock=clock, relaunch=relaunch)
    runner.cfg.game_dead_after_s = 60.0
    result = asyncio.run(runner.run())
    assert result.stop_reason == "turn_budget_reached"
    assert relaunched == [True]
    assert max(slept) <= 30.0
    assert any(r["type"] == "game_relaunch" for r in _records(tmp_path))


def test_unexpected_exception_is_logged_with_checkpoint_and_run_continues(tmp_path):
    game = FakeGame()
    game.fail["get_cities"] = (RuntimeError("weird"), False)
    clock = _Clock()
    checkpoints = []

    async def checkpoint(gs, turn):
        checkpoints.append(turn); return f"DREX_CHECKPOINT_T{turn:04d}"

    log = DecisionLog(tmp_path / "run.jsonl", run_id="drex-test", secrets=[])
    runner = Runner(game, PreferSelector(), log, RunConfig(turns=1), end_turn=_fake_end_turn(game), checkpoint=checkpoint, clock=clock)
    runner._sleep = _no_sleep_async
    result = asyncio.run(runner.run())
    assert result.stop_reason == "turn_budget_reached"
    assert checkpoints == [5]
    err = next(r for r in _records(tmp_path) if r["type"] == "runner_error")
    assert "RuntimeError" in err["error"] and "traceback" in err


def test_dispatch_error_is_reconciled_not_replayed(tmp_path):
    """A raised dispatch never reaches the recovery loop and is never re-sent."""
    game = FakeGame()
    game.fail["set_research"] = (ConnectionError("dropped mid-send"), False)
    clock = _Clock()
    runner, _ = _runner(game, tmp_path, clock=clock)
    result = asyncio.run(runner.run())
    assert result.stop_reason == "turn_budget_reached"
    assert [c[0] for c in game.calls].count("set_research") == 1
    dec = next(r for r in _records(tmp_path) if r["type"] == "decision" and r["dispatch"]["method"] == "set_research")
    assert dec["outcome"]["reconciled"] is True
    assert not any(r["type"] == "game_io_error" for r in _records(tmp_path))
```

Check how `FakeGame.fail` raises for `get_units`/`get_cities` (it goes through `_record` only for actions today; add `_record`-style failure hooks to the query methods: `if fail := self.fail.pop("get_units", None): raise fail[0]`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/drex/test_drex_recovery.py -v`
Expected: FAIL with `TypeError: Runner.__init__() got an unexpected keyword argument 'clock'` then stop reasons `error:...`.

- [ ] **Step 3: Implement**

```python
_IO_ERRORS = (ConnectionError, OSError, LuaError, asyncio.TimeoutError)


class Runner:
    def __init__(..., relaunch=None, clock=time.monotonic):
        ...
        self._relaunch = relaunch
        self._clock = clock
        self._sleep = asyncio.sleep
        self._phase = "unknown"
        self._header_written = False

    async def run(self) -> RunResult:
        self._t0 = time.perf_counter()
        if self.spectator is not None:
            self.spectator.start()
        outage_started: float | None = None
        relaunched = False
        attempt = 0
        try:
            while True:
                try:
                    return await self._run()
                except asyncio.CancelledError:
                    self._write_stop(self._result("interrupted", self._last_core, None))
                    raise
                except _IO_ERRORS as e:
                    attempt += 1
                    now = self._clock()
                    outage_started = outage_started if outage_started is not None else now
                    self.log.write("game_io_error", {"error": f"{type(e).__name__}: {e}",
                                   "phase": self._phase, "attempt": attempt,
                                   "turn": self._last_core.turn if self._last_core else None})
                    wait = min(self.cfg.reconnect_backoff_s * (2 ** (attempt - 1)), self.cfg.reconnect_max_backoff_s)
                    await self._sleep(wait)
                    if (not relaunched and self._relaunch is not None
                            and self._clock() - outage_started >= self.cfg.game_dead_after_s):
                        relaunched = True
                        result = await self._relaunch()
                        self.log.write("game_relaunch", {"result": result})
                    try:
                        await self.gs.conn.reconnect()
                    except _IO_ERRORS:
                        continue
                    attempt = 0 if False else attempt  # attempts reset only after a successful observe (below)
                except Exception as e:  # noqa: BLE001 — logged with traceback, run continues
                    self.log.write("runner_error", {"error": f"{type(e).__name__}: {e}",
                                   "traceback": traceback.format_exc()[-4000:], "phase": self._phase})
                    if self._last_core is not None and not self.cfg.dry_run:
                        try:
                            await self._checkpoint(self.gs, self._last_core.turn)
                        except Exception as ce:  # noqa: BLE001
                            self.log.write("checkpoint_failed", {"error": f"{type(ce).__name__}: {ce}"})
                    await self._sleep(self.cfg.reconnect_backoff_s)
                else:
                    outage_started, relaunched, attempt = None, False, 0
        finally:
            if self.spectator is not None:
                await self.spectator.stop()
```

(Reset `outage_started`, `relaunched`, `attempt` inside `_run` after the first successful `_observe()` instead of the unreachable `else`; simplest: make `_observe` set `self._io_ok = True`, and in the loop after catching an error check `if self._io_ok: attempt = 0; outage_started = None; relaunched = False; self._io_ok = False` before handling — write it that way and drop the placeholder line.)

`_run(self)` writes the header only when `not self._header_written`; it re-uses `self._turns`, `self._decisions`, `self._start_turn` (only set when `None`). Set `self._phase = "observe"` in `_observe`, `"execute"` around `executor.execute`, `"end_turn"` around `_end_turn`, `"schedule"` otherwise.

`RunConfig`: `game_dead_after_s: float = 120.0`, `reconnect_backoff_s: float = 1.0`, `reconnect_max_backoff_s: float = 30.0`.

CLI: read `GAME_DEAD_AFTER_S` from `os.environ` (default 120), pass into `RunConfig`; pass `relaunch=_relaunch` where

```python
        async def _relaunch() -> str:
            from civ_mcp.autosave import get_autosave_for_turn
            from civ_mcp.game_launcher import restart_and_load
            latest = get_autosave_for_turn(gs._high_water_turn) if gs._high_water_turn else None
            return await restart_and_load(latest)
```

(Check `get_autosave_for_turn`'s signature in `src/civ_mcp/autosave.py` and `gs._high_water_turn`'s name in `game_state.py`; if the attribute differs, use the one `end_turn.py:1480` uses.) `probe` gains a warning when `AppOptions.txt` has `FullScreen 1`: read the file at the documented macOS path, print `{"warning": "FullScreen 1: game relaunch after a crash needs windowed mode (FullScreen 2)"}`.

- [ ] **Step 4: Run tests, commit**

```bash
uv run pytest tests -q
uvx ruff format src/civ_mcp/drex/runner.py src/civ_mcp/drex/cli.py tests/drex/test_drex_recovery.py tests/drex/drex_fakes.py
git add src/civ_mcp/drex/runner.py src/civ_mcp/drex/cli.py tests/drex/test_drex_recovery.py tests/drex/drex_fakes.py
git commit -m "Reconnect and relaunch on tuner failures; log unexpected errors and continue"
```

---

### Task 13: Stop-reason scan and prefetch (A3 check, C5)

**Files:**
- Modify: `src/civ_mcp/drex/runner.py` (prefetch next inputs during the Drex call)
- Test: `tests/drex/test_drex_runner.py`, `tests/drex/test_drex_speed.py`

**Interfaces:**
- Produces: `Runner._prefetch: asyncio.Task | None`; `Scheduler.peek(core, ledger, after: DecisionSpec) -> DecisionSpec | EndTurn` returning what `next` would return if `after` were resolved now (a copy of the ledger with `after`'s key added to `resolved`).

- [ ] **Step 1: Write the failing tests**

```python
# test_drex_runner.py
def test_only_terminal_stop_reasons_are_reachable():
    import inspect, re
    from civ_mcp.drex import runner, scheduler
    src = inspect.getsource(runner) + inspect.getsource(scheduler)
    reasons = set(re.findall(r'_stop\(\s*f?"([a-z_:]+)', src))
    assert reasons <= {"turn_budget_reached", "game_over", "dry_run_complete", "interrupted"}, reasons
    assert "class Stop" not in src


# test_drex_speed.py
def test_next_inputs_are_prefetched_during_selection(tmp_path):
    from drex_fakes import FakeGame
    from test_drex_runner import PreferSelector, _fake_end_turn
    from civ_mcp.drex.decision_log import DecisionLog
    from civ_mcp.drex.runner import RunConfig, Runner

    game = FakeGame()
    order = []
    orig_space = game.get_unit_action_space

    async def spaced(idx):
        order.append(("space", idx)); return await orig_space(idx)
    game.get_unit_action_space = spaced

    class SlowSelector(PreferSelector):
        async def choose(self, point):
            order.append(("choose", point.entity))
            await asyncio.sleep(0)   # let the prefetch task run
            order.append(("chosen", point.entity))
            return await super().choose(point)

    log = DecisionLog(tmp_path / "run.jsonl", run_id="t", secrets=[])
    runner = Runner(game, SlowSelector(prefixes=("skip:", "research:", "produce:")), log, RunConfig(turns=1), end_turn=_fake_end_turn(game))
    asyncio.run(runner.run())
    # at least one action-space read happened between a 'choose' and its 'chosen'
    assert any(order[i][0] == "choose" and order[i + 1][0] == "space" for i in range(len(order) - 1))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/drex/test_drex_runner.py::test_only_terminal_stop_reasons_are_reachable tests/drex/test_drex_speed.py::test_next_inputs_are_prefetched_during_selection -v`
Expected: the scan passes only if Tasks 10–12 removed every other reason (fix any leftovers now); the prefetch test FAILS.

- [ ] **Step 3: Implement**

`scheduler.py`:

```python
    def peek(self, core, ledger, after: DecisionSpec) -> DecisionSpec | EndTurn:
        trial = dataclasses.replace(ledger, resolved=set(ledger.resolved) | {key_for(after)},
                                    counts=Counter(ledger.counts), failures=Counter(ledger.failures))
        return self.next(core, trial)
```

(`TurnLedger` is a dataclass; verify field names `resolved`, `counts`, `failures`, `failed_candidates`, `exit_offered` and copy each mutable one.)

Runner, before `result = await select(point, self.selector)`:

```python
            nxt = self.scheduler.peek(core, ledger, step)
            prefetch = (
                asyncio.create_task(self.observer.inputs(nxt, core))
                if isinstance(nxt, DecisionSpec) and nxt.category is DecisionCategory.UNIT else None
            )
```

After the decision and refresh, when the next scheduled step equals `nxt` and `core.version` differs only by the refresh (i.e. `nxt.entity`'s unit is unchanged: same `x, y, moves_remaining` in the refreshed `core` as in the previous one), use `await prefetch` as `inputs` for that step instead of calling `observer.inputs`. Otherwise cancel the task (`prefetch.cancel()`; `with contextlib.suppress(asyncio.CancelledError): await prefetch`). Prefetched inputs are only valid together with the observation version they were read for; if they are used, the decision point must be built against the refreshed `core` **and** the executor must receive `inputs=None` when the unit's position or moves changed (the Task 6 rule then falls back to a fresh precheck read). Keep this logic in one helper `_take_prefetch(prefetch, nxt, before, after) -> DecisionInputs | None`.

- [ ] **Step 4: Run tests, commit**

```bash
uv run pytest tests -q
uvx ruff format src/civ_mcp/drex/runner.py src/civ_mcp/drex/scheduler.py tests/drex/test_drex_runner.py tests/drex/test_drex_speed.py
git add src/civ_mcp/drex/runner.py src/civ_mcp/drex/scheduler.py tests/drex/test_drex_runner.py tests/drex/test_drex_speed.py
git commit -m "Prefetch the next unit's inputs during the Drex call; assert only terminal stops remain"
```

---

### Task 14: Round-trip budget test and live measurement

**Files:**
- Test: `tests/drex/test_drex_speed.py`

- [ ] **Step 1: Write the budget test**

```python
def test_decision_round_trip_budget(tmp_path):
    """At most 3 round trips per decision and 2 per full observation, counted on the fake connection."""
    from drex_fakes import FakeGame
    from test_drex_runner import PreferSelector, _fake_end_turn, _records
    from civ_mcp.drex.decision_log import DecisionLog
    from civ_mcp.drex.runner import RunConfig, Runner

    game = _SnapshotGame(FakeGame())   # batched observation, like the live game
    game.base.conn.roundtrips = 0
    log = DecisionLog(tmp_path / "run.jsonl", run_id="t", secrets=[])
    runner = Runner(game, PreferSelector(prefixes=("move:", "research:", "produce:", "skip:")), log, RunConfig(turns=1), end_turn=_fake_end_turn(game.base))
    asyncio.run(runner.run())
    for rec in (r for r in _records(tmp_path) if r["type"] == "decision"):
        assert rec["timing_ms"]["roundtrips"] <= 3, (rec["decision_id"], rec["dispatch"]["method"], rec["timing_ms"])
```

`_SnapshotGame.get_core_snapshot` must count as `2` round trips when it has both InGame and GameCore parts, else `1`: set `self.base.conn.roundtrips += 2 if parts & {"tech", "progress"} else 1` and make the base's individual getters **not** count while called from the snapshot (wrap with a flag `self.base._in_snapshot = True`; `FakeGame` query counters check the flag).

- [ ] **Step 2: Run and fix until green**

Run: `uv run pytest tests/drex/test_drex_speed.py -v`
Expected: PASS. If a kind exceeds 3, the assertion names the dispatch method; the usual culprit is a verify poll that could confirm from dispatch output. Fix in `executor.py` following Task 7's pattern and add the kind to that task's tests.

- [ ] **Step 3: Live run and numbers**

```bash
uv run civ-drex play --turns 500
```

After 10 turns: `uv run python scripts/drex_timing.py logs/drex/<newest>.jsonl`. Record the medians and the per-turn lines in `docs/drex.md` (Task 15). Targets: decision `roundtrips` median ≤ 3; `observe` median ≤ 400 ms; a turn of ~8 decisions ≤ 15 s excluding `end_turn_seconds`.

- [ ] **Step 4: Commit**

```bash
git add tests/drex/test_drex_speed.py tests/drex/drex_fakes.py
git commit -m "Enforce the 3-round-trip budget per decision"
```

---

### Task 15: Documentation and exit codes

**Files:**
- Modify: `docs/drex.md`
- Modify: `src/civ_mcp/drex/cli.py` (exit codes)

- [ ] **Step 1: Update `docs/drex.md`**

- Status table: add a Phase 1 row with date and the measured numbers from Task 14.
- Replace the "Failures" paragraph: transient errors retry forever with backoff capped at `DREX_MAX_BACKOFF_S` (60 s); non-retryable errors wait the same and re-read the env file; every wait is a `selection_waiting` record. There is no pause and no checkpoint stop for API errors.
- Exit codes: `0` game over or turn budget, `2` configuration error before the run starts, `3` reserved for a `runner_error` that recurred without progress for an hour (not implemented in Phase 1; document as "not produced"). Remove "run stopped early".
- Settings: add `DREX_MAX_BACKOFF_S`, `GAME_DEAD_AFTER_S`.
- Game setup: `FullScreen 2` (borderless) required for automatic relaunch; the probe warns otherwise.
- New section "Speed": the round-trip model, the budgets, `scripts/drex_timing.py`, and the numbers.
- Decision log: list new record types `selection_waiting`, `game_io_error`, `game_relaunch`, `runner_error`, `checkpoint_failed`, `unsupported_blocker`, `budget_end_turn`, `identity_changed`, `informational_session_stuck`, `end_turn_blocked_repeat`, `end_turn_status`, `speed`; `timing_ms` fields; `turn.roundtrips` and `turn.phase_ms`.
- "Who decides what": the `EXIT` (close the screen) candidate is offered to Drex after the failure budget; the controller never closes a decision-bearing screen on its own.

- [ ] **Step 2: Exit codes in `cli.py`**

`SUCCESS_STOPS = frozenset({"turn_budget_reached", "game_over", "dry_run_complete"})`; `interrupted` returns 130.

- [ ] **Step 3: Run suite, format, commit**

```bash
uv run pytest tests -q
uvx ruff format src/civ_mcp/drex/cli.py && uvx ruff check src/civ_mcp/drex/cli.py
git add docs/drex.md src/civ_mcp/drex/cli.py
git commit -m "Document Phase 1: no infrastructure stops, speed budgets and settings"
```

---

## Self-review notes

- **Spec coverage.** A1 → Task 10. A2 → Task 12 (reconnect, relaunch, identity in Task 11). A3 → Task 11 (budget, sessions, deals, blocked end turn, unsupported wait, identity, other statuses) and Task 12 (unexpected exceptions). A4 → Task 7. C1 → Tasks 3, 4. C2 → Task 5. C3 → Task 6. C4 → Task 7. C5 → Task 13. C6 → Task 8. C7 → Task 9 (autosave already runs after the advance at `end_turn.py:1528`, so the spec's autosave item needs no change). C8 → Tasks 1, 9, 14. Spec Section 6 settings → Tasks 10, 12, 15.
- **Deviation from spec, recorded:** spec A3 says an unsupported blocker "is logged and treated like a blocked end turn"; in Phase 1 it is a logged wait of `unsupported_wait_s` (30 s) with re-observation, because the blocker cannot be resolved until its Phase 2 decision kind exists and hammering end turn would only spam the log. The spec's stop-reason rule still holds.
- **Type consistency.** `refresh_parts(kind) -> frozenset[str]` (Task 5) is what Task 5's runner change and Task 11's `frozenset({"blockers","popup"})` refreshes use. `Executor.execute(..., inputs=)` (Task 6) is what Task 13's prefetch feeds. `Spectator.popup_status/quiet` (Task 8) match `RecordingSpectator` in the tests. `CoreSnapshot` fields (Task 4) match `_SnapshotGame` in Tasks 4 and 14. `on_wait` payload keys (Task 10) match `selection_waiting` assertions.
- **Review Focus pins.** 1 → Task 3 test. 2 → Task 5 tests. 3 → Task 10 tests. 4 → Task 12 test. 5 → Task 8 test.
