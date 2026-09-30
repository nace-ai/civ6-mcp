# Drex decision-only controller

`civ-drex` lets [Drex](https://nace.ai/drex), a decision model, control the local player. Code enumerates every legal candidate; Drex returns a probability per candidate; the controller resolves the choice to a stored, typed action, executes it through `GameState`, verifies the result, and observes again. No generative model is used anywhere in this loop: context and option descriptions come from fixed templates over typed game data, and memory is a bounded list of factual outcomes.

```
observe (typed) -> scheduler picks one entity -> enumerate candidates -> Drex chooses
   -> validate -> fresh precheck -> dispatch once -> verify postcondition -> log -> observe
```

## Status (2026-09-29, base commit `dd20190`, civ6-mcp 1.1.11)

| Milestone | State |
|---|---|
| A — offline adapter | Done. 252 new tests, full suite 351 passing, offline replay working. An independent review's findings (below) are fixed with regression tests. |
| B — one real decision of each kind | Not run. No Civilization VI install on the development machine, and no working Drex key (below). |
| C — 20 consecutive turns | Not run, same reasons. The runner, logging and stop/checkpoint paths are implemented and tested against an in-memory game. |
| D — broader coverage, random baseline comparison | Not started beyond the labeled `random-baseline` selector. |
| Phase 1 — resilience and speed (spec `docs/superpowers/specs/2026-09-30-civ-drex-never-stop-design.md`) | Done 2026-09-30 on branch `drex-phase1`. A run no longer ends for Drex API errors, tuner errors or loop guards; a full observation is 2 batched round trips; live turns 47–51 took 13–17 s wall including the AI turn (was ~54 s). See "Speed" and "No stops for infrastructure" below. |
| Phase 2 — early-game blockers (plan `docs/superpowers/plans/2026-09-30-civ-drex-phase2-early-blockers.md`) | Implemented 2026-09-30 on branch `drex-phase2`, offline (the game was closed): unit promotion, governors, era dedication, Great People, religion founding and added beliefs, city and district ranged attacks, plus the engine blocker coverage test. Not yet run against a live game; `civ-drex probe --kind <name>` checks each new query read-only before relying on it. |
| Phase 4 — late and rare prompts (plan `docs/superpowers/plans/2026-09-30-civ-drex-phase4-prompts.md`) | Implemented 2026-09-30 on `drex-decision-runner`: captured/rebelled city (keep, raze, liberate, reject), spy escape route (escape and dragnet blockers), artifact claimant. Only emergency and World Congress session blockers remain phase-later; this Base-ruleset game has neither. |
| Phase 3 — growth (plan `docs/superpowers/plans/2026-09-30-civ-drex-phase3-growth.md`) | Implemented 2026-09-30 on branch `drex-phase3`: district and wonder placement inside production decisions (one batched advisor read per decision, top 3 tiles per item), trade routes for idle traders, and a once-per-turn ranged-attack check per city (the Base ruleset raises no city-attack blocker). Phase 2 queries were probed live the same day: promotion, Great People, religion and city attack parse; governor and dedication Lua does not exist in the Base ruleset. |

A Drex key (`nace_sk_...`, created at https://drex.nace.ai) is required; `apikey_` keys belong to TypeSafe and are rejected by drex.nace.ai with 401.

## Verified API contract

Sources: [nace.ai/drex.md](https://nace.ai/drex.md) (states the API is wire-compatible with TypeSafe's Jev) and the [TypeSafe API reference](https://docs.typesafe.ai/api.md), both read 2026-09-29.

| Item | Value | Verified how |
|---|---|---|
| Endpoint | `POST https://drex.nace.ai/v1/systemone`, `GET /v1/models` | Nace docs; live 401 from drex.nace.ai |
| Auth | `Authorization: Bearer <nace_sk_...>` | Nace docs; live 401 message |
| Model | `drex-latest` → `drex-v1.1` (also `drex-v1.0`) | Live `GET /v1/models` and responses, 2026-09-29 |
| Request | `{"model","state","questions":{"decision":{"type":"choice","instructions","criteria":{label: string\|null}}}}` | Live: Drex returns 422 for object descriptions (`must be a string or null`), unlike TypeSafe, so facts are sent as one `key: value; ...` line |
| Response | `answers.decision = {"type":"choice","choice","probabilities":{label:p},"confidence"}`, `usage.{input_tokens,output_tokens}`, header `x-typesafe-request-id` | Docs; live against TypeSafe (`jev-1.13.0`, 300 input tokens, 312 ms) |
| Errors | 401 key, 422 body, 429 rate limit, 529 overloaded | Docs; 401 observed live |
| Limits | ≤255 options per Choice; context limits unknown (Jev: 64k tokens/request) | Live on Drex: 255 accepted, 256 rejected (422); context limit not probed |

Live Drex checks (2026-09-29): the 2-option probe and four realistic fixture decisions (research, production, warrior, settler) all returned valid distributions from `drex-v1.1` in 300–700 ms with 22–552 input tokens. An earlier request against TypeSafe used Jev, not Drex, and only checked the client plumbing. `civ-drex` refuses any response whose `model` does not start with `drex` unless `--allow-non-drex-model` is passed explicitly.

**Selection rule.** Take the options with maximum probability (within 1e-9). If the service's reported `choice` is one of them, select it; if it reported a choice that is not a maximum, reject the answer. With no reported choice, the lowest candidate id wins. Answers are rejected if any option is unknown, missing or duplicated (duplicate JSON keys included), if a probability is non-numeric, outside [0, 1] or non-finite (JSON `NaN`/`Infinity` are refused at the wire), or if they do not sum to 1 within 0.01 + 0.0005 per option. Scores are option probabilities, not win probabilities.

**No choice without Drex.** A candidate is executed without asking Drex only when the engine offered exactly one legal option (`forced_single_candidate`), or when the per-unit turn-ending rule leaves exactly one order (`forced_turn_ending_order`); both are logged with `selector: controller`. If Drex's choice fails and only one alternative is left (e.g. accept after a failed reject), it is never executed: the decision is dropped (`no_candidates`) and the loop moves on. The one exception is "Close the screen" once every response to a diplomacy session has failed: closing a screen is housekeeping, and it is executed as `forced_close_screen`.

**Failures.** There is no pause and no checkpoint stop for API errors. Transient errors (429/5xx/529, timeouts, transport errors, malformed bodies or answers) retry forever with exponential backoff capped at `DREX_MAX_BACKOFF_S` (default 60 s), honouring `retry-after`. Non-retryable errors (401/403, 400/422, model mismatch, a request over local limits) wait the same cap, re-read the env file and rebuild the client, so a rotated key or changed base URL is picked up without a restart. Every wait is a `selection_waiting` record and, after `DREX_MAX_RETRIES` attempts, a stderr line `waiting for Drex: ...`. There is no fallback model and no random fallback; nothing is decided while waiting.

## No stops for infrastructure

`civ-drex play` ends only at game over, at the `--turns` budget, or on Ctrl-C (exit code 130). Everything else is recovered:

- A dropped tuner socket, a Lua error or a timeout while observing, prechecking or verifying reconnects with backoff (1 s doubling to 30 s) and resumes from a fresh observation (`game_io_error` records). A dispatch that raises is still never re-sent; the executor reconciles it by reading state.
- If the outage lasts `GAME_DEAD_AFTER_S` (default 120 s), the game is relaunched and the newest `0_MCP_NNNN` autosave loaded (`game_relaunch` record). That path drives the menu by screen OCR and needs the game in windowed or borderless mode: set `FullScreen 2` in `AppOptions.txt`; `civ-drex probe` warns when it is `1`.
- Any other exception is logged with its traceback (`runner_error`), a `DREX_CHECKPOINT_TNNNN` save is written, and the loop resumes.
- Loop guards no longer stop: the per-turn decision cap (200) ends the turn (`budget_end_turn`); a diplomacy session whose failure budget is spent gets one more Drex decision that includes "Close the screen" (`EXIT`), then is left alone; an unresolved deal is left alone; a blocked end turn with nothing new to decide re-observes and, after three repeats, dismisses popups and requests once more (`end_turn_blocked_repeat`); a game identity change is logged (`identity_changed`) and the run continues; an informational session that will not close is logged (`informational_session_stuck`) and skipped for the turn.
- A blocker no decision kind handles yet (Phase 2 adds them; see the coverage matrix in the spec) is logged as `unsupported_blocker` and the run waits 30 s and re-observes, forever if need be. Nothing is decided without Drex. A restart on newer code picks the game up from that point.

## Install and run

```bash
git clone https://github.com/lmwilki/civ6-mcp.git && cd civ6-mcp   # or your fork/branch
uv sync --group dev
uv run pytest tests                      # offline; no game, no API

mkdir -p ~/.config/civ-drex
cp docs/drex.env.example ~/.config/civ-drex/drex.env && chmod 600 ~/.config/civ-drex/drex.env
# edit DREX_API_KEY (nace_sk_...)

uv run civ-drex verify-api --probe       # lists models, sends one 2-option Choice
```

Optional settings in `drex.env`: `DREX_MAX_BACKOFF_S` (60) caps the wait between Drex retries; `GAME_DEAD_AFTER_S` (120, environment only) is how long a tuner outage may last before the game is relaunched.

Game setup (Milestone B/C): Steam Civilization VI with Gathering Storm; set `EnableTuner 1` (macOS: `~/Library/Application Support/Sid Meier's Civilization VI/Firaxis Games/Sid Meier's Civilization VI/AppOptions.txt`; Windows: in-game option); disable Auto End Turn; set `FullScreen 2` (borderless) in `AppOptions.txt` so the game can be relaunched automatically after a crash; close FireTuner and any other tuner client (the game accepts one connection; do not run `civ-mcp` at the same time); load a single-player save manually. Tuner disables achievements. Then:

```bash
uv run python scripts/test_connection.py            # handshake smoke test
uv run civ-drex probe                               # read-only: identity, blockers, per-unit action spaces
uv run civ-drex dry-run --save-fixture t.json       # next decision's context + options, no API, no mutation
uv run civ-drex dry-run --call-drex                 # same, plus Drex's choice and the planned dispatch
uv run civ-drex play --turns 20                     # Milestone C run; log in logs/drex/<run_id>.jsonl
uv run civ-drex play --turns 20 --selector random-baseline --seed 1   # labeled baseline, same candidates
uv run civ-drex replay --fixture t.json --answer response.json        # offline re-validation
```

`fixtures/drex/` holds synthetic observations for offline replay, e.g. `uv run civ-drex replay --fixture fixtures/drex/t5_research.json --answer fixtures/drex/answer_t5_research.SYNTHETIC.json`. The answer file is synthetic, not a Drex response.

Exit codes: 0 turn budget reached or game over, 2 configuration or connection error before the run starts, 4 replayed answer rejected (offline `replay`), 130 interrupted with Ctrl-C. A running game never exits early for an API or tuner error.

### Recording the starting save (Milestone C)

Record in the run notes: save file name and SHA-256, game build (main menu), DLC and mods, civilization/leader, map type/size, difficulty, speed, start turn, and the `game.civ`/`game.seed` from the log header (from `civ-drex probe`). Keep the save untouched; the run writes `DREX_CHECKPOINT_TNNNN` saves only when it stops early, plus the existing `0_MCP_NNNN` per-turn autosaves.

## Speed

The tuner is the bottleneck, not Drex: one round trip costs about 57 ms (it was ~350 ms before the fixed drain waits in `GameConnection` were cut from 0.3 s to 0.03 s). Phase 1 removed round trips rather than checks:

- The whole observation is two batched Lua scripts (`GameState.get_core_snapshot`, `src/civ_mcp/lua/batch.py`): one InGame (identity, overview, cities, units, sessions, deals, blockers, popup state), one GameCore (tech, progress). Nine round trips became two, about 300 ms live.
- After an action only the parts it can change are re-read (`src/civ_mcp/drex/refresh.py`); once per turn, before end turn, a full read runs as a safety net whenever a partial refresh happened.
- Prechecks reuse the decision's own reads while the observation is current; moves, production, research, civics and skips are confirmed from the dispatch output instead of readback polls.
- While Drex chooses, the next unit's inputs are prefetched; the reads finish before anything is dispatched.
- The popup watcher no longer polls: the observation carries the popup state, and camera and dismissals hold while the engine processes the AI turn.
- In decision-only mode the end turn skips the narration-only victory and empire-warning reads and uses 0.5 s polling sleeps.

A unit promotion costs 3 execute round trips (promotable-units precheck, GameCore promote, and the stale-notification check inside `promote_unit`); it is exempt from the budget test. Budget, enforced by `tests/drex/test_drex_speed.py`: at most 2 round trips to execute a decision and 1 for the partial refresh that follows. Every `decision` record carries `timing_ms.observe`, `observe_roundtrips`, `execute_roundtrips` and `roundtrips`; every advanced turn writes a `speed` record (decisions, seconds, round trips, Drex seconds, end-turn seconds, end-turn `phase_ms`) and a stderr line. `uv run python scripts/drex_timing.py logs/drex/<run>.jsonl` summarises a log. Measured live 2026-09-30 (turns 47–51, before Tasks 10–14): observe median 65 ms, execute 104 ms, Drex 522 ms, round trips median 3; turns 13–17 s wall including 2–8 s end turn.

## Blocker coverage

`fixtures/drex/end_turn_blocking_types.txt` lists every member of the engine's `EndTurnBlockingTypes` enum (from the game's UI sources; `civ-drex probe --kind blockers` refreshes it with live values). `tests/drex/test_blocker_coverage.py` fails if any member is not in `SUPPORTED_BLOCKERS`, `HOUSEKEEPING_BLOCKERS` or `PHASE_LATER_BLOCKERS` in `scheduler.py`, so a new engine type cannot go unclassified.

## Scheduler order

The controller picks what is decided next; Drex only chooses within that entity's candidates. The order affects results and is written into every log header.

1. Open diplomacy sessions (ascending player id). Informational ones are closed and logged as housekeeping (never in dry-run): goodbye phases, and sessions from a player we are at war with that carry no deal. `is_at_war` describes the relationship, not the session, so an at-war session with a deal (e.g. a peace offer) is decided as a deal.
2. Pending incoming deals.
2a. Modal prompts, decided before anything else while their blocker stands (up to 4 per turn each): captured or rebelled city (keep / raze / liberate to founder / liberate to previous owner / reject, exactly the directives the engine accepts), a caught spy's escape route (escape-route and dragnet-priority blockers; one candidate per escape district the city has), the civilization an excavated artifact is credited to (a single claimant is forced). A prompt blocker that stands once nothing is pending is dismissed as housekeeping.
3. Blocker-driven: government prompt, empty policy slot (lowest index), envoys, pantheon, governor actions (any `GOVERNOR_*` blocker; up to 5 per turn), unit promotion (ascending unit id; the unit list comes from a GameCore XP-threshold query read only while the blocker stands), era dedication, forced Great Person claim, religion founding (three steps: religion, follower belief, founder belief + found), added belief, city or district ranged attack (per city, ascending id).
4. Research, only when none is selected. 5. Civic, only when none is selected.
6. Production for empty queues (ascending city id).
6a. Each city's ranged attack, once per turn: attack a listed target or hold fire (forced, no Drex call, when there is no target).
6b. Great People, once per turn when someone is recruitable or affordable (the pool is read every 5 turns or when a claim is forced); "wait" is a real option unless the claim is forced.
7. Units with moves left (ascending composite unit id), at most 3 decisions per unit per turn; a unit's last permitted decision offers only turn-ending orders.
8. End turn (typed outcome).

## Supported decisions

| Decision | Candidates from | Dispatch (`GameState`) | Postcondition checked |
|---|---|---|---|
| Research | `get_tech_civics().available_techs` + engine `CanResearch` recheck | `set_research(tech_type)` | researching type == target |
| Civic | `available_civics` + `CanProgress` recheck (prerequisite fallback, logged) | `set_civic(civic_type)` | progressing type == target |
| Production | `list_city_production`; districts and wonders excluded (need placement), district repairs kept with coordinates | `set_city_production(city_id, type, name, x, y)` | `CurrentlyBuilding()` readback |
| Move | new `get_unit_action_space`: `UnitManager.GetReachableMovement`, minus own same-class stacks and visible foreign units | `move_unit(unit_index, x, y)` | position polled (bounded) |
| Attack (units only) | action space: visible hostile units on hex neighbours (`Map.GetAdjacentPlot` BFS), engine `CanStartOperation` for RANGE_ATTACK / MELEE move | `attack_unit(unit_index, x, y)` | attacker moves spent (else pending) |
| Found city | action space `can_found` (same checks as `build_found_city`) | `found_city(unit_index)` | city exists at tile |
| Improve | `UnitInfo.valid_improvements` (validated on the builder's own tile only) | `improve_tile(unit_index, name)` | tile improvement readback |
| Fortify / heal / skip | action space eligibility | `fortify_unit` / `heal_unit` / `skip_unit` | fortify turns / moves spent |
| Diplomacy response | open session; responses `POSITIVE`/`NEGATIVE` (the vocabulary `AddResponse` accepts; `DiplomacySession.choices` is never populated upstream) | `diplomacy_respond(player, response)` | response delivered |
| Incoming deal | `get_pending_deals` (agreements it cannot name, e.g. a peace treaty, are now listed as `Agreement: <type>` instead of dropped) | `respond_to_deal(player, accept)` | deal no longer pending |
| Policy slot | `get_policies`, slot-type compatible, not already slotted | `set_policies({slot: policy})` | slot readback |
| Government | unlocked governments (new parser) + keep current | `change_government` / `keep_current_government` | current government / prompt cleared |
| Envoy | `get_city_states`, `can_send_envoy` | `send_envoy(player)` | token spent |
| Pantheon | `get_pantheon_status` | `choose_pantheon(belief)` | pantheon readback |
| Unit promotion | `get_promotable_units` (new GameCore query with the XP-threshold rule) + `get_unit_promotions` | `promote_unit(unit_id, promotion)` | `PROMOTED|` in the dispatch output, else promotion count readback |
| Governor appoint / assign / promote | `get_governors` (appointable, unplaced governors × own cities without one, promotions with points) | `appoint_governor` / `assign_governor` / `promote_governor` | `APPOINTED|`/`ASSIGNED|`/`PROMOTED|`, else governor readback |
| Era dedication | `get_dedications().choices` not yet active, described for the current age | `choose_dedication(index)` | `DEDICATION_CHOSEN|`, else `active` readback |
| Great Person | `get_great_people`: recruit (points), patronize (gold / faith within the treasury), wait (dropped when `CLAIM_GREAT_PERSON` forces a claim) | `recruit_great_person` / `patronize_great_person(id, yield)` / none | `RECRUITED|`/`PATRONIZED|`, else claimant readback; wait confirms as `no_action` |
| Religion founding | `get_religion_founding_status`: step 1 religion, step 2 follower belief (both stored in the turn ledger, no dispatch), step 3 founder belief | `found_religion(religion, follower, founder)` | `RELIGION_FOUNDED|`, else `has_religion` readback; partial choices are dropped on a new turn or when the prompt disappears |
| Added belief | every available belief of every class | `add_belief(belief)` (new Lua, `PlayerOperations.ADD_BELIEF`) | `BELIEF_ADDED|`, else belief no longer offered |
| District / wonder placement | production options that need a tile × top 3 advisor tiles from `get_placement_options` (every advisor of the city in one batched round trip; an advisor error excludes the item with its reason) | `set_city_production(city, type, name, x, y)` | `PRODUCING|<item>|` from dispatch, else queue readback |
| Trade route | idle `UNIT_TRADER` with a free route slot: one candidate per `get_trade_destinations` entry (domestic / city-state / quest / trading post facts) | `make_trade_route(unit_index, x, y)` | `TRADE_ROUTE_STARTED|`, else trader `on_route` readback |
| City / district ranged attack | `get_city_attack_targets(city)` (new Lua over `CityManager.GetCommandTargets`) plus "hold fire"; no targets → hold fire is the single legal option | `city_attack(city, x, y)` / none | `CITY_RANGE_ATTACK|` confirmed, else pending combat |
| Captured / rebelled city | `get_captured_city` (new Lua over `GetNextRebelledCity` / `GetNextCapturedCity` and `CanStartCommand(DESTROY)` per directive: keep, raze, liberate founder, liberate previous owner, reject) | `resolve_city_capture(action)` | `<ACTION>|` from dispatch, else city no longer pending |
| Spy escape route | `get_spy_escape_choice` (new Lua; asks the engine for the escaping spy only after the escape/dragnet notification is found standing, since that call can crash the game otherwise) | `choose_spy_escape(district)` (new Lua, `SET_ESCAPE_ROUTE`) | `ESCAPE_ROUTE|` from dispatch, else spy no longer escaping |
| Artifact claimant | `get_artifact_choice` (new Lua over `GetNextExtractingArchaeologist` / `Game.GetArtifactByIndex`; acting player plus the target when the find allows a choice) | `choose_artifact_player(player)` (new Lua, `CHOOSE_ARTIFACT_PLAYER`) | `ARTIFACT_CHOSEN|` from dispatch, else artifact no longer pending |

Outcomes are `confirmed`, `pending` (accepted, effect asynchronous), `rejected` (stale/ineligible or game error with no effect, found on any line of the result) or `unknown`. Every candidate is re-checked against fresh state immediately before dispatch (unit identity by composite id, position, moves, destination/target still offered, queue still empty, choice still unset...); this precheck, not the observation-version check, is what catches stale choices in the single-threaded loop. A candidate is dispatched at most once per turn for a given observed state (another envoy while tokens remain, or a reply in a new dialogue round, counts as a new action). Dispatch runs inside `GameConnection.replay_disabled()`, so a dropped socket raises instead of silently re-sending the Lua; a dispatch that raises is reconciled by reading state, never retried.

**End-turn blockers.** Supported: units, stacked units, production, research, civic, policy slot, government prompt, envoys, pantheon, unit promotion, governors, commemoration, Great Person claim, religion and beliefs, city and district ranged attack, captured and disloyal cities, spy escape and dragnet, artifact. Housekeeping: World Congress "look" (informational). Everything else (emergencies, World Congress sessions — types this Base-ruleset game does not have) waits with `unsupported_blocker` and never stops the run. A blocked end turn is never repeated unless a decision or housekeeping happened since (`end_turn_blocked:<types>` otherwise); the one exception is `interrupted` (a war declaration consumed the request), which allows one new request. While an end-turn request is still being processed (an AI proposal paused the AI turn), only sessions and deals are observed and decided, then the pending request is polled, not re-sent.

## Who decides what

- **Drex:** every selection among two or more candidates. Without Drex, only the engine's single legal option (`forced_single_candidate`) or the single order left by the turn-ending rule (`forced_turn_ending_order`) is executed, labeled as such.
- **Drex, after a session's failure budget is spent:** the same responses plus "Close the screen" (`EXIT`). The controller never closes a decision-bearing screen on its own; it only closes informational ones (goodbye phases, deal-free sessions from a player at war).
- **Controller heuristics (deterministic code):** the scheduler order; deciding research/civic only when nothing is selected and production only for empty queues; excluding placement-dependent production; move filtering (own same-formation stacks use the same civilian/non-civilian rule `build_move_unit` enforces at dispatch); the 3-decisions-per-unit budget and turn-ending-only final decision; the nearest-first shortlist if options exceed the limit (never triggered in tests); a failure budget of 2 per decision key per turn, with failed candidates excluded for the rest of the turn; closing informational diplomacy sessions; the configured objective text.
- **Retained engine / existing behaviour:** pathfinding and move execution; combat resolution; `move_unit`/`attack_unit`/`found_city` popup pre-dismissal and one `found_city` retry; `diplomacy_respond` auto-closing a goodbye phase; `set_research`/`set_civic` GameCore fallback when the InGame request silently fails (after the engine eligibility precheck); `get_cities` removing ghost (hash 0) queue entries; per-turn `0_MCP_NNNN` autosaves; `execute_end_turn` synchronization (`_pending_end_turn`, polling, hang detection). No built-in AI automation (auto-explore, automated builders) is used.
- **`execute_end_turn` in decision-only mode** (`GameState.decision_only_end_turn`, set by `execute_end_turn_typed`): consequential auto-resolutions are disabled — keeping captured/disloyal cities, passing World Congress interactions (also when a vote handler from an earlier session is still registered), dismissing governor/government prompts, choosing spy escape routes, clearing stored promotions, dismissing corrupted production prompts, dismissing an at-war session that carries a deal during the AI turn, and re-sending the end-turn request after a timeout. Retained and logged per turn as `housekeeping`: dismissal of deal-free sessions from at-war players (war declarations cannot be declined), popup dismissal, World Congress "look", envoy prompt with zero tokens, stale research/civic notification when a choice is already set, finishing zero-move units, autosave. MCP behaviour is unchanged when the flag is off (including the upstream behaviour of dismissing every at-war session during the AI turn, which can discard a peace offer).

## What Drex sees

Per decision: the configured objective; turn; own empire totals (gold, yields, city/unit counts, current research/civic); the subject (unit, city, leader dialogue and visible buttons, deal items, slot...); tiles within radius 2 of a unit that are visible or previously revealed — fogged tiles keep only terrain, hills, river and (tech-gated) resource, because the map query reports current rather than last-seen units, owner, yields, feature, improvement and district; unexplored tiles are dropped; and the last 12 outcomes as `{turn, category, subject, action, result}`. Not sent: rival scores/rankings, total land, victory snapshots, rival statistics, end-turn narration, raw error text.

## Decision log

`logs/drex/<run_id>.jsonl`, one JSON object per line. Phase 1 added `selection_waiting`, `game_io_error`, `game_relaunch`, `runner_error`, `checkpoint_failed`, `unsupported_blocker`, `budget_end_turn`, `identity_changed`, `informational_session_stuck`, `end_turn_blocked_repeat`, `end_turn_status` and `speed`; `decision.timing_ms` gained `observe`, `observe_roundtrips`, `execute_roundtrips`, `roundtrips`; `turn` gained `roundtrips` and `phase_ms`. Existing types: `header` (run id, git describe, package version, Drex base URL/model/limits, selector, game civ/seed, start turn, config, scheduler order), `decision` (observation version, candidates with typed params, legal count and forced rule, exclusions, exact request sent, probabilities per candidate id, rule, confidence, model, usage, request id, attempts, dispatch, outcome with evidence, API and execution time), `turn` (typed end-turn status, blockers, housekeeping, end-turn time, legacy report), `housekeeping`, `housekeeping_planned` (dry-run), `no_candidates`, `dry_run`, `stop` (reason, turns advanced, decisions, checkpoint, API time vs game time). Any unexpected exception is logged as `runner_error` with its traceback and one `DREX_CHECKPOINT_TNNNN` save per turn and error type; the run then resumes. The API key and any `Authorization`/`api_key` fields are redacted before writing.

## Tests

`uv run pytest tests` (CI now runs the suite). The Drex tests cover: malformed/unknown/duplicate/missing options never executing; candidates keeping identity and selection after reordering; stale observations rejected before dispatch; visibility filtering removing unexplored tiles, fogged units/owners/yields and rival data; each of the every action kind mapping to its intended `GameState` call with `unit_index` (not the composite id); transport failures reconciled, never re-dispatched, and not replayed by the connection; a failed choice never being inverted into the remaining alternative (deals, multi-round diplomacy); at-war peace offers reaching the selector; dry-run making no calls that mutate; errors mid-run producing a stop record and checkpoint; refused attacks in the real result format; decision-only end turn not auto-resolving consequential blockers (Congress with a stale handler, at-war offers during the AI turn, timeout re-send) while legacy mode still does; typed end-turn outcomes including in-flight polling and war interruptions; scheduler order and budgets; runner traces, checkpoints and stops; key redaction; offline replay.

## Limitations

- Phase 1 and the Phase 2 queries have run against a live game (2026-09-30); Phase 3 kinds are verified by the suite and `probe --kind placements|trade`. `build_unit_action_space_query` uses `GetReachableMovement`, `GetAdjacentPlot`, `PlayersVisibility:IsVisible` and `CanStartOperation` as existing queries do, but its exact output needs confirming with `civ-drex probe` before relying on it. `PlayerCulture:CanProgress` is unverified (prerequisite fallback is logged).
- Drex option and context limits are unverified; defaults follow TypeSafe's documented limits.
- Foreign units on visible tiles come from iterating all players' units, which may include units the player cannot see (e.g. stealth units). Found/fortify eligibility uses the same loose `CanStartOperation(..., true)` check as existing tools; postconditions catch failures.
- Not supported yet (`PHASE_LATER_BLOCKERS`): emergencies and World Congress sessions, which this game's ruleset does not raise. Phase 4 prompts (captured city, spy escape, artifact) are verified by the suite and `probe --kind captured_city|spy_escape|artifact`; their queries return "nothing pending" live until the game raises the prompt. Also not yet: multi-turn movement, espionage missions, purchases, unit upgrades, trade route re-plotting.
- The MCP tools' per-turn advisor budget (`ADVISOR_BUDGET_SOFT/HARD`) applies to LLM agents only; the controller's batched placement read bypasses it (one round trip per production decision that has districts or wonders to offer).
- Phase 2 kinds have not run against a live game yet. Known items to verify with `civ-drex probe --kind`: `u:GetID()` as the composite id in the GameCore promotable-units query; `CityManager.GetCommandTargets` including encampment targets for `DISTRICT_RANGE_ATTACK`; whether "hold fire" leaves the `CITY_RANGE_ATTACK` blocker standing (fallback: dismiss the notification as housekeeping); the real `EndTurnBlockingTypes` values (`fixtures/drex/end_turn_blocking_types.txt` carries -1 placeholders).
- The random-baseline comparison and CivBench integration (Milestone D) are not done; any CivBench use must be labeled a custom open-track agent.
