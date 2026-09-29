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

**No choice without Drex.** A candidate is executed without asking Drex only when the engine offered exactly one legal option (`forced_single_candidate`), or when the per-unit turn-ending rule leaves exactly one order (`forced_turn_ending_order`); both are logged with `selector: controller`. If Drex's choice fails and only one alternative is left (e.g. accept after a failed reject), it is never executed: the decision is dropped and the run stops with a checkpoint.

**Failures.** Timeouts, transport and body-decoding errors, 429/5xx/529 and malformed bodies or answers are retried up to `DREX_MAX_RETRIES` with backoff (honouring `retry-after`), then the run pauses with a checkpoint. 401/403/400/422, requests over local limits and model mismatches pause immediately. There is no fallback model and no random fallback.

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

Game setup (Milestone B/C): Steam Civilization VI with Gathering Storm; set `EnableTuner 1` (macOS: `~/Library/Application Support/Sid Meier's Civilization VI/Firaxis Games/Sid Meier's Civilization VI/AppOptions.txt`; Windows: in-game option); disable Auto End Turn; close FireTuner and any other tuner client (the game accepts one connection; do not run `civ-mcp` at the same time); load a single-player save manually. Tuner disables achievements. Then:

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

Exit codes: 0 success/turn budget reached/game over, 2 configuration or API/connection error, 3 run stopped early (see `stop_reason`), 4 replayed answer rejected.

### Recording the starting save (Milestone C)

Record in the run notes: save file name and SHA-256, game build (main menu), DLC and mods, civilization/leader, map type/size, difficulty, speed, start turn, and the `game.civ`/`game.seed` from the log header (from `civ-drex probe`). Keep the save untouched; the run writes `DREX_CHECKPOINT_TNNNN` saves only when it stops early, plus the existing `0_MCP_NNNN` per-turn autosaves.

## Scheduler order

The controller picks what is decided next; Drex only chooses within that entity's candidates. The order affects results and is written into every log header.

1. Open diplomacy sessions (ascending player id). Informational ones are closed and logged as housekeeping (never in dry-run): goodbye phases, and sessions from a player we are at war with that carry no deal. `is_at_war` describes the relationship, not the session, so an at-war session with a deal (e.g. a peace offer) is decided as a deal.
2. Pending incoming deals.
3. Blocker-driven: government prompt, empty policy slot (lowest index), envoys, pantheon.
4. Research, only when none is selected. 5. Civic, only when none is selected.
6. Production for empty queues (ascending city id).
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

Outcomes are `confirmed`, `pending` (accepted, effect asynchronous), `rejected` (stale/ineligible or game error with no effect, found on any line of the result) or `unknown`. Every candidate is re-checked against fresh state immediately before dispatch (unit identity by composite id, position, moves, destination/target still offered, queue still empty, choice still unset...); this precheck, not the observation-version check, is what catches stale choices in the single-threaded loop. A candidate is dispatched at most once per turn for a given observed state (another envoy while tokens remain, or a reply in a new dialogue round, counts as a new action). Dispatch runs inside `GameConnection.replay_disabled()`, so a dropped socket raises instead of silently re-sending the Lua; a dispatch that raises is reconciled by reading state, never retried.

**End-turn blockers.** Supported: units, stacked units, production, research, civic, policy slot, government prompt, envoys, pantheon. Housekeeping: World Congress "look" (informational). Everything else — governor appointment, unit promotion, commemoration, religion founding, great people, World Congress sessions or votes, captured/disloyal cities, spy escape — checkpoints and stops with `unsupported_blocker:<type>`. A blocked end turn is never repeated unless a decision or housekeeping happened since (`end_turn_blocked:<types>` otherwise); the one exception is `interrupted` (a war declaration consumed the request), which allows one new request. While an end-turn request is still being processed (an AI proposal paused the AI turn), only sessions and deals are observed and decided, then the pending request is polled, not re-sent.

## Who decides what

- **Drex:** every selection among two or more candidates. Without Drex, only the engine's single legal option (`forced_single_candidate`) or the single order left by the turn-ending rule (`forced_turn_ending_order`) is executed, labeled as such.
- **Controller heuristics (deterministic code):** the scheduler order; deciding research/civic only when nothing is selected and production only for empty queues; excluding placement-dependent production; move filtering (own same-formation stacks use the same civilian/non-civilian rule `build_move_unit` enforces at dispatch); the 3-decisions-per-unit budget and turn-ending-only final decision; the nearest-first shortlist if options exceed the limit (never triggered in tests); a failure budget of 2 per decision key per turn, with failed candidates excluded for the rest of the turn; closing informational diplomacy sessions; the configured objective text.
- **Retained engine / existing behaviour:** pathfinding and move execution; combat resolution; `move_unit`/`attack_unit`/`found_city` popup pre-dismissal and one `found_city` retry; `diplomacy_respond` auto-closing a goodbye phase; `set_research`/`set_civic` GameCore fallback when the InGame request silently fails (after the engine eligibility precheck); `get_cities` removing ghost (hash 0) queue entries; per-turn `0_MCP_NNNN` autosaves; `execute_end_turn` synchronization (`_pending_end_turn`, polling, hang detection). No built-in AI automation (auto-explore, automated builders) is used.
- **`execute_end_turn` in decision-only mode** (`GameState.decision_only_end_turn`, set by `execute_end_turn_typed`): consequential auto-resolutions are disabled — keeping captured/disloyal cities, passing World Congress interactions (also when a vote handler from an earlier session is still registered), dismissing governor/government prompts, choosing spy escape routes, clearing stored promotions, dismissing corrupted production prompts, dismissing an at-war session that carries a deal during the AI turn, and re-sending the end-turn request after a timeout. Retained and logged per turn as `housekeeping`: dismissal of deal-free sessions from at-war players (war declarations cannot be declined), popup dismissal, World Congress "look", envoy prompt with zero tokens, stale research/civic notification when a choice is already set, finishing zero-move units, autosave. MCP behaviour is unchanged when the flag is off (including the upstream behaviour of dismissing every at-war session during the AI turn, which can discard a peace offer).

## What Drex sees

Per decision: the configured objective; turn; own empire totals (gold, yields, city/unit counts, current research/civic); the subject (unit, city, leader dialogue and visible buttons, deal items, slot...); tiles within radius 2 of a unit that are visible or previously revealed — fogged tiles keep only terrain, hills, river and (tech-gated) resource, because the map query reports current rather than last-seen units, owner, yields, feature, improvement and district; unexplored tiles are dropped; and the last 12 outcomes as `{turn, category, subject, action, result}`. Not sent: rival scores/rankings, total land, victory snapshots, rival statistics, end-turn narration, raw error text.

## Decision log

`logs/drex/<run_id>.jsonl`, one JSON object per line: `header` (run id, git describe, package version, Drex base URL/model/limits, selector, game civ/seed, start turn, config, scheduler order), `decision` (observation version, candidates with typed params, legal count and forced rule, exclusions, exact request sent, probabilities per candidate id, rule, confidence, model, usage, request id, attempts, dispatch, outcome with evidence, API and execution time), `turn` (typed end-turn status, blockers, housekeeping, end-turn time, legacy report), `housekeeping`, `housekeeping_planned` (dry-run), `no_candidates`, `selection_paused`, `dry_run`, `stop` (reason, turns advanced, decisions, checkpoint, API time vs game time). Any unexpected exception ends the run with a `stop` record (`error:<type>: <message>`) and a checkpoint. The API key and any `Authorization`/`api_key` fields are redacted before writing.

## Tests

`uv run pytest tests` (CI now runs the suite). The Drex tests cover: malformed/unknown/duplicate/missing options never executing; candidates keeping identity and selection after reordering; stale observations rejected before dispatch; visibility filtering removing unexplored tiles, fogged units/owners/yields and rival data; each of the 17 action kinds mapping to its intended `GameState` call with `unit_index` (not the composite id); transport failures reconciled, never re-dispatched, and not replayed by the connection; a failed choice never being inverted into the remaining alternative (deals, multi-round diplomacy); at-war peace offers reaching the selector; dry-run making no calls that mutate; errors mid-run producing a stop record and checkpoint; refused attacks in the real result format; decision-only end turn not auto-resolving consequential blockers (Congress with a stale handler, at-war offers during the AI turn, timeout re-send) while legacy mode still does; typed end-turn outcomes including in-flight polling and war interruptions; scheduler order and budgets; runner traces, checkpoints and stops; key redaction; offline replay.

## Limitations

- Nothing here has run against a live game. `build_unit_action_space_query` uses `GetReachableMovement`, `GetAdjacentPlot`, `PlayersVisibility:IsVisible` and `CanStartOperation` as existing queries do, but its exact output needs confirming with `civ-drex probe` before relying on it. `PlayerCulture:CanProgress` is unverified (prerequisite fallback is logged).
- Drex option and context limits are unverified; defaults follow TypeSafe's documented limits.
- Foreign units on visible tiles come from iterating all players' units, which may include units the player cannot see (e.g. stealth units). Found/fortify eligibility uses the same loose `CanStartOperation(..., true)` check as existing tools; postconditions catch failures.
- Not supported yet: district/wonder placement, city attacks, multi-turn movement, promotions, governors, religion beyond pantheon, great people, trade routes, espionage, purchases, World Congress, city capture decisions.
- The random-baseline comparison and CivBench integration (Milestone D) are not done; any CivBench use must be labeled a custom open-track agent.
