# civ-drex: play to the end, every decision by Drex, at speed

Status: draft for review, 2026-09-30. Base: civ6-mcp 1.1.11 plus the uncommitted
spectator integration (`src/civ_mcp/drex/spectate.py`).

## 1. Goal

A `civ-drex play` run ends only at game over, at the `--turns` budget, or on
Ctrl-C. Nothing else ends it: not an unsupported game prompt, not a Drex API
error, not a dropped tuner socket. Drex makes every choice among two or more
legal options. The controller acts on its own only when the engine offers
exactly one legal option (the existing `forced_single_candidate` rule) or when
the act has no game-rule content (closing a popup, saving).

The run must also be fast enough to watch comfortably: a decision in about one
second of wall time excluding the Drex call, and a full turn in well under 20
seconds excluding the AI players' own turn processing.

### Non-goals

- Gold purchases, unit upgrades, espionage missions, city ranged attacks,
  religious unit actions (spread, inquisition) and trade route re-plotting are
  not added. None of them blocks end turn; the game never forces them. They can
  be added later as ordinary decision kinds without changing this design.
- No generative model anywhere. Option labels and context stay fixed templates.
- No change to what Drex is shown per decision beyond the new decision kinds'
  own subjects (Section 4 keeps the current visibility filtering).

### Observed failures this design removes

| Date | Stop reason | Cause |
|---|---|---|
| 2026-09-29 run 1, T12 | `selector_paused: HTTP 429 Too many concurrent requests` after 3 attempts over 7 s | Free-tier Drex key: 2 in-flight slots, a leaked slot is reclaimed after 90 s |
| 2026-09-29 runs 2 and 3 | same, after 1 to 2 decisions | same |
| every production decision | outcome `unknown`, `verify_error:LuaError` | `build_verify_production` readback raises in GameCore |
| would have happened by ~T30 | `unsupported_blocker:ENDTURN_BLOCKING_UNIT_PROMOTION` (and governor, dedication...) | decision kinds not implemented |

## 2. Principles

1. **Drex chooses.** Every selection among two or more candidates goes to Drex.
   Waiting for Drex is always preferred to deciding without it.
2. **Stop is not an error path.** Every current `Stop(...)` in `scheduler.py`
   and `runner.py` maps to a decision, a wait, or housekeeping. The only
   reachable stop reasons after this work are `game_over`,
   `turn_budget_reached`, `interrupted` and `dry_run_complete`.
3. **Fresh precheck, single dispatch, verified postcondition** stays the
   contract for every new kind, exactly as for the existing seventeen.
4. **Speed comes from fewer tuner round trips, not from skipping checks.**
   Correctness rules (stale-observation rejection, precheck before dispatch)
   are untouched; they are made cheaper by batching, not removed.

## 3. Section A: no stops for infrastructure

### A1. Drex API waits instead of pausing

`DrexSelector.choose` currently retries `max_retries` times with 1, 2, 4 s
backoff and raises `SelectionPaused`. New behaviour:

- Retryable errors (`DrexUnavailable`: 429, 5xx, 529, timeout, transport;
  `DrexProtocolError`; `DecisionError` on a malformed answer) retry
  indefinitely. Backoff doubles from 1 s, capped at `DREX_MAX_BACKOFF_S`
  (default 60), honouring `retry-after` when larger.
- Non-retryable errors (`DrexAuthError`, `DrexRequestRejected`,
  `DrexRequestInvalid`, `DrexModelMismatch`) also do not end the run. The
  selector waits `DREX_MAX_BACKOFF_S`, re-reads the env file (so a rotated key
  or changed base URL is picked up without a restart), rebuilds the client and
  tries again. A request over local limits (more than 255 options) is a
  controller bug, not a Drex outage: it is logged and the decision is rebuilt
  with the nearest-first shortlist that already exists.
- Every wait writes a `selection_waiting` record: decision id, attempt number,
  error class and message, sleep seconds. The stdout progress line shows the
  same so a watcher can tell "waiting for Drex" from "thinking".
- `SelectionPaused` and the `selector_paused` stop reason are removed. The
  `DREX_MAX_RETRIES` setting becomes the number of attempts before the first
  `selection_waiting` record is escalated to WARNING level in the log; it no
  longer ends anything.
- The game side is not left idle while waiting: the popup watcher keeps the
  screen clean, and the camera stays where it is.

### A2. Game connection recovers

- Any `ConnectionError`, `LuaError` or timeout raised while observing, prechecking
  or verifying is caught in the runner's loop, logged as `game_io_error`, and
  followed by reconnect with backoff (1 s doubling to 30 s). Observation then
  restarts from a fresh `core()`. Dispatch keeps its existing rule: a dispatch
  that raises is reconciled by reading state, never re-sent.
- If reconnect fails for `GAME_DEAD_AFTER_S` (default 120) and the Civ 6
  process is not running, the runner calls the existing lifecycle helpers to
  relaunch the game and load the newest `0_MCP_NNNN` autosave, then reconnects.
  That path drives the menu by screen OCR and requires the game in windowed or
  borderless mode; `docs/drex.md` gains `FullScreen 2` as a requirement and
  `civ-drex probe` warns when `AppOptions.txt` has `FullScreen 1`.
- If the game process is alive but the tuner never answers (the known
  post-kill hang), the same relaunch path is used after `GAME_DEAD_AFTER_S`.
- `game_identity_changed` no longer stops. The runner logs it, resets
  per-run caches (wonder types, memory) and continues on the new identity.

### A3. Loop guards become decisions or waits

| Current stop | New behaviour |
|---|---|
| `decision_budget_exhausted` | End the turn. The per-turn cap rises to 200; hitting it is logged as `budget_end_turn`. |
| `diplomacy_session_unresolved` / `deal_unresolved` | After the failure budget for that session is spent, Drex is offered the remaining legal UI actions for the screen (the visible buttons plus "close the screen"). Only if that leaves exactly one option is it executed as `forced_single_candidate`. |
| `unsupported_session:deal_without_pending_items` | Becomes a DEAL decision whose candidates are accept and reject of the session's deal summary, dispatched through `diplomacy_respond`. |
| `end_turn_blocked:<types>` with no progress | Re-observe and re-schedule; if the same blocker set repeats three times with no decision in between, run `dismiss_popup` and the end-turn typed request once more, then continue the loop. Never stop. |
| `unsupported_blocker:<type>` | Removed: every blocker type the engine can raise is either supported (Section B) or housekeeping. An unknown type is logged `unknown_blocker` and treated like a blocked end turn above. |
| `end_turn_<other status>` | Logged, then the loop continues with a fresh observation. |
| `error:<type>` (unexpected exception) | Logged with traceback as `runner_error`, checkpoint saved, loop continues after reconnect (A2). A checkpoint is still written so a crash can be studied. |

### A4. Fix the production verify error

`build_verify_production` raises `LuaError` after a successful set (raw shows
`PRODUCING|UNIT_SCOUT|2 turns`). The readback is rewritten to use the same
`CurrentlyBuilding()` call guarded with `pcall`, and the executor's verify for
`SET_PRODUCTION` accepts the dispatch's own `PRODUCING|<item>` line as
confirmation when the readback is unavailable. Outcome becomes `confirmed`.

## 4. Section B: new decision kinds

Each kind follows the existing pattern: a `DecisionCategory`, an `ActionKind`
with typed params, a candidate builder in `enumerate.py`, a `LiveObserver.inputs`
branch, a `dispatch_call` mapping, a `_precheck` and a `_verify` branch, a
`FakeGame` implementation, and tests. All `GameState` queries and dispatch
methods below exist today unless marked **new**.

| Kind | Trigger | Candidates from | Dispatch | Postcondition |
|---|---|---|---|---|
| Unit promotion | blocker `UNIT_PROMOTION` | `get_unit_promotions(unit_id).promotions` | `promote_unit(unit_id, promotion_type)` | `promotion_count` increased or blocker gone |
| Governor appoint | blocker `GOVERNOR_IDLE` with `can_appoint` | `get_governors().available_to_appoint` | `appoint_governor(type)` | appears in `appointed` |
| Governor assign | blocker `GOVERNOR_IDLE`, appointed governor without city | appointed governors × own cities | `assign_governor(type, city_id)` | governor's city set |
| Governor promote | blocker `GOVERNOR_IDLE`, promotion points | `promotions` of each appointed governor | `promote_governor(type, promotion)` | promotion present |
| Era dedication | blocker for dedications (type confirmed live in Phase 2) | `get_dedications().choices` | `choose_dedication(index)` | in `active` |
| Religion: pick religion | blocker for religion founding | `get_religion_founding_status().available_religions` | none (stored in turn ledger) | — |
| Religion: follower belief | after pick | `beliefs_by_class["FOLLOWER"]` | none (stored) | — |
| Religion: founder belief, then found | after beliefs | `beliefs_by_class["FOUNDER"]` | `found_religion(religion, follower, founder)` | `has_religion` |
| Great Person | proactive, any `can_recruit` or affordable patronage | per person: recruit, patronize gold, patronize faith, plus one "wait" | `recruit_great_person` / `patronize_great_person` / none | person claimed by us / unchanged |
| World Congress vote | blocker `WORLD_CONGRESS_SESSION` | per resolution: option × target × votes 1..max with favor cost as fact | `vote_world_congress(hash, option, target, votes)` | vote recorded |
| World Congress submit | after all resolutions voted | single option | `submit_congress()` (forced) | session not pending |
| Captured city | blocker `CONSIDER_RAZE_CITY` | keep, raze, liberate where allowed | **new** `keep_city` / `raze_city` / `liberate_city`, wrapping the Lua `end_turn.py` runs today | blocker gone, city state matches |
| Disloyal city | blocker `CONSIDER_DISLOYAL_CITY` | keep or release as engine allows | **new** `resolve_disloyal_city(city_id, keep)` | blocker gone |
| Spy escape | blocker `SPY_CHOOSE_ESCAPE_ROUTE` | **new** `get_spy_escape_routes()` | **new** `choose_spy_escape(index)` | blocker gone |
| District placement | production for empty queue | for each buildable district: top 3 tiles from `get_district_advisor(city, district)` | `set_city_production(city, "DISTRICT", type, x, y)` | queue readback |
| Wonder placement | production for empty queue | for each buildable wonder: top 3 tiles from `get_wonder_advisor` | `set_city_production(city, "BUILDING", type, x, y)` | queue readback |
| Trade route | unit decision for idle trader | reachable destination cities from `get_trade_routes` and the engine's destination list | `make_trade_route(unit_index, x, y)` | route active for that trader |

Notes:

- Multi-step decisions (religion, Congress) keep partial choices in the
  `TurnLedger`; if the turn ends or the blocker disappears the partial state is
  discarded and logged.
- The religion decomposition keeps each request under 255 options. Congress
  vote counts are capped so option × target × votes stays under 255; if not,
  votes are decided in a second step.
- District and wonder candidates increase production option counts. The
  existing nearest-first shortlist does not apply; instead districts and wonders
  are limited to three tiles each and the total is capped at 255 by dropping the
  lowest-adjacency placements first.
- The "wait" option for Great People is a real, legal choice, so those
  decisions always reach Drex.

## 5. Section C: speed

Measured on 2026-09-30, turn 15: about 10 s of wall time per decision, of
which 0.6 s Drex, 1.5 to 3 s execute, 5 to 6 s re-observing. Turns 13 to 15
took 54 s each. One tuner round trip costs roughly 350 ms, and a decision
currently makes about fifteen of them: nine in `LiveObserver.core()`, two in
`inputs()`, one or two in precheck, one dispatch, one to six verify polls, plus
the popup watcher's own poll every 0.5 s contending for the same connection
lock.

Targets: a decision in at most 1.0 s of game time (three round trips) plus the
Drex call; a turn of eight decisions in under 15 s plus the engine's AI turn.

### C1. One round trip per observation

`core()` becomes a single batched Lua script per Lua state: one GameCore read
returning identity, overview, tech, progress, cities, units and blockers; one
InGame read returning sessions and deals. Parsers are unchanged; the batch
prints the same sections separated by markers and the Python side splits them.
Nine round trips become two.

### C2. Refresh only what the action could change

After a dispatch, the runner refreshes by category instead of calling `core()`:

| Action | Refresh |
|---|---|
| unit move, attack, fortify, heal, skip, improve, found city | units, blockers; cities too after found city |
| set production, district, wonder | that city's queue, blockers |
| research, civic, policy, government, pantheon, dedication | progress, blockers |
| envoy, Great Person, governor, promotion | the relevant status, blockers |
| diplomacy, deal | sessions, deals, blockers |
| end turn | full `core()` |

Each partial refresh still bumps the observation version, so stale-decision
rejection works exactly as now. Once per turn, before end turn, a full `core()`
runs as a safety net. Combined with C1 the typical post-action refresh is one
round trip.

### C3. Fold precheck into the decision's own read

The `inputs()` read for a unit (action space plus map area) already contains
what the precheck needs. The executor's precheck accepts a state that was read
within the same observation version and skips its own query when so. Dispatch
remains a separate, single round trip.

### C4. Verify without polling delays

Verify polls at 0.25 s intervals up to six times. Moves are the common case;
the dispatch result already reports the unit's new position and remaining
moves, so verify confirms from the dispatch output first and polls only when
that is inconclusive. Confirmed-from-dispatch outcomes are labelled as such so
the log distinguishes them.

### C5. Overlap the Drex call

While waiting for Drex (0.6 s), the runner pre-reads the next scheduled
entity's `inputs()` for the same observation version. If the executed action
invalidates that entity (it moved, or the version changed under C2 rules), the
prefetch is discarded. Saves one round trip per decision on the critical path.

### C6. Spectator without contention

The popup watcher stops polling on its own timer. Instead the observation batch
in C1 and C2 appends the popup status check to its script and the runner hands
the result to the spectator, which dismisses when needed (one extra round trip
only when a popup is actually visible). The camera keeps its queue but drops the
diplomacy-active pre-check round trip; the batch reports that flag too.

### C7. End turn

`execute_end_turn_typed` is profiled on the live game. Known costs to cut: the
pre-dismiss `dismiss_popup` phase 2 scan (up to 30 Lua states at 350 ms each)
runs only when phase 1's pre-check saw an exclusive popup; the 2 s fixed sleeps
before polling become 0.5 s with the same overall timeout; the per-turn
autosave stays (it is the checkpoint) but runs after the end-turn request is
sent, not before.

### C8. Measurement

Every `decision` record gains `timing_ms.observe` and `timing_ms.roundtrips`,
and every `turn` record gains `roundtrips`. `civ-drex play` prints a one-line
summary per turn: decisions, seconds, round trips, Drex seconds. A
`scripts/drex_timing.py` summarises a log. Phase 1 exits with these numbers
compared against the targets above.

## 6. Section D: scheduler, logging, configuration

- Scheduler order stays as documented, with the new blocker-driven kinds in
  step 3 in the order the engine lists them, and Great People and trade routes
  in the proactive list after production and before units.
- New log record types: `selection_waiting`, `game_io_error`, `runner_error`,
  `unknown_blocker`, `budget_end_turn`, `identity_changed`, `speed` (per-turn
  timing summary). All existing record types are unchanged.
- New settings in `drex.env`: `DREX_MAX_BACKOFF_S` (60), `GAME_DEAD_AFTER_S`
  (120). Existing settings keep their names.
- `docs/drex.md` is updated: stop reasons, supported decisions table, speed
  numbers, `FullScreen 2` requirement.

## 7. Section E: testing

- Every new kind gets the same coverage the current seventeen have: unknown or
  duplicate options never execute, identity survives reordering, stale
  observation rejected before dispatch, dispatch maps to the intended
  `GameState` call with the right argument types, postcondition confirmed and
  unconfirmed paths, `FakeGame` support.
- Resilience: a 429 storm with a fake clock ends in a decision, not a stop;
  auth error then key rotation in the env file resumes; a `LuaError` in
  `core()` reconnects and continues; the game-dead path calls the relaunch
  helper once.
- Stop-reason scan: a test drives the fake game through every blocker type
  and every selector failure and asserts the only stop reasons produced are
  `game_over` and `turn_budget_reached`.
- Speed: unit tests count fake-connection round trips per decision and assert
  the C1 to C5 budgets (at most three per decision, two per full observation).
- Live checks before relying on any new query: `civ-drex probe` gains a
  `--kind <name>` flag that runs the new query read-only against the loaded
  save and prints the parsed result, mirroring how the current action space
  was validated.

## 8. Rollout

Each phase lands on the live game and runs at least 20 turns before the next
starts. The run keeps going between phases; a restart picks up from the live
turn.

1. **Resilience and speed** (A1, A2, A3, A4, C1 to C8). Ends the stops that
   actually happened, and gets turns under the target.
2. **Early-game blockers**: unit promotion, governors, dedication, Great
   People, religion. These are what the game raises first, around turns 20 to
   60.
3. **Growth**: district and wonder placement, trade routes. Without these Rome
   never builds a Campus.
4. **Late and rare**: World Congress, captured and disloyal cities, spy escape.

## 9. External dependency: the Drex service

Not part of this repo, recorded for the owner. `lib/rate-limit.ts` in the Drex
service allows 2 in-flight requests per free account, reclaims a leaked slot
after 90 s, and answers 429 with retry-after 1 s. A sequential client on a
free key hit 429 within seconds after an earlier request was aborted, which
points at slots not being released when the function ends abnormally. The
controller now tolerates this (A1), but the retry-after hint should match the
real wait, and release should be guaranteed on abort.
