# civ-drex Phase 6: Foreign policy — proactive diplomacy — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Drex can act in diplomacy instead of only answering: once per turn it chooses one foreign-policy move among everything the engine currently allows with every civilization it has met (send a delegation, declare friendship, open an embassy, denounce, propose mutual open borders, form an alliance, declare a surprise or formal war, propose peace) or does nothing. Today the run only reacts to AI-initiated sessions, which is why it never negotiates or starts a war.

**Architecture:** One new empire-wide category `FOREIGN_POLICY`, scheduled after the purchase decision and before units when at least one civilization has been met. Its inputs are the existing `get_diplomacy()` read (one InGame round trip: per-civ state, scores, strengths, cities, and the engine's own `available_actions`, which already runs `IsDiplomaticActionValid` / `CanDeclareWarOn`). Candidates are one per (civ, allowed action) plus one "no diplomatic action this turn" (no dispatch). Dispatch reuses `send_diplomatic_action`, `propose_peace` and `form_alliance`. Trade deals (selling luxuries, buying resources) are deferred: they need a deal-valuation step and are not part of this phase.

**Tech Stack:** Python 3.12, asyncio, pytest (`uv run pytest tests -q`), ruff via `uvx ruff`. No new Lua.

**Spec:** `docs/superpowers/specs/2026-09-30-civ-drex-never-stop-design.md` — Section 8 phase 5 ("unforced but useful ... same decision-kind pattern"); principle 1 (every choice among 2+ options is Drex's). This phase adds a decision family the spec did not enumerate; the user asked for it on 2026-09-30 ("we need to be able to attack, negotiate with others").

## Global Constraints

- The controller never filters by judgement: every action the engine reports valid is offered, war included, with facts (their military strength vs ours, relationship, cities, grievances, alliances) so Drex can weigh it. The only bounds are structural: one foreign-policy decision per turn, and a civ we are already at war with gets only "propose peace".
- Speed: one InGame read (`get_diplomacy`) per turn for the inputs; dispatch is one round trip (peace is two, the existing verify read); refresh `overview`, `sessions`, `deals`.
- A declared war opens the leader screen; the existing `send_diplomatic_action` closes the session and its background cleanup dismisses the view after ~8 s. The spectator's popup watcher already pauses while a diplomacy screen is up (CRITICAL state).
- Do not edit `src/civ_mcp/lua/*` other than `drex_queries.py`, nor `server.py`, `game_launcher.py`. Stage only files you changed.
- Suite is 612 passing at the start; full suite before each commit; `uvx ruff format` / `uvx ruff check` on touched files. Commits end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.

## Review Focus

1. **An action the AI declines is not a controller failure.** `send_diplomatic_action` reports `SENT|` when the proposal went out and `ACCEPTED|` when the AI agreed; `propose_peace` reports `REJECTED|` when the AI refuses. All three are the action happening: the outcome must be `confirmed` (with the response as evidence), never `rejected`/`unknown`, or the failure budget excludes valid moves and the turn loops. Pinned in Task 1 (`test_declined_proposals_confirm_with_the_answer_as_evidence`).
2. **War candidate only when the engine allows it.** `available_actions` carries `DECLARE_WAR` only when `CanDeclareWarOn` is true; the candidate builder must not invent war options from `is_at_war == False`. Pinned in Task 1 (`test_war_is_offered_only_when_the_engine_allows_it`).
3. **At-war civs get peace only.** No delegation/friendship/embassy against a civ we are fighting even if the engine lists them; the peace option appears whenever `is_at_war` (the engine enforces the 10-turn cooldown at dispatch and a refusal is `confirmed` with evidence). Pinned in Task 1 (`test_at_war_civ_offers_only_peace`).
4. **Stale `available_actions`.** The precheck re-reads `get_diplomacy` when inputs are stale and rejects an action no longer listed, so a delegation is never sent twice. Pinned in Task 1 (`test_precheck_rejects_an_action_the_engine_withdrew`).
5. **Option explosion** is impossible here (≤ 8 actions × ≤ 11 civs < 255), but the "no action" candidate must survive any shortlist: it is neither a move nor a purchase, so the existing rules keep it. Pinned in Task 1 (`test_no_action_candidate_is_always_present`).

---

## File Structure

| File | Responsibility |
|---|---|
| `src/civ_mcp/drex/candidates.py` (modify) | `DecisionCategory.FOREIGN_POLICY`; kinds `DIPLOMATIC_ACTION`, `PROPOSE_PEACE`, `FORM_ALLIANCE`, `NO_DIPLOMACY`; `DiplomaticActionParams(player_id, civ_name, action)`, `PeaceParams(player_id, civ_name)`, `AllianceParams(player_id, civ_name, alliance_type)`, `NoDiplomacyParams()`; ids `diplo:<pid>:<action>`, `peace:<pid>`, `alliance:<pid>:<type>`, `no_diplomacy`. |
| `src/civ_mcp/drex/enumerate.py` (modify) | `foreign_policy_candidates(civs, me_strength)`. |
| `src/civ_mcp/drex/observation.py`, `live.py`, `points.py` (modify) | `DecisionInputs.civs`; inputs read; question; wiring. |
| `src/civ_mcp/drex/executor.py`, `refresh.py`, `spectate.py`, `scheduler.py` (modify) | Dispatch/precheck/verify; refresh rules; no camera focus (empire-wide); once-per-turn scheduling after purchases. |
| Tests: `tests/drex/test_drex_foreign_policy.py` (create); `drex_fixtures.py`, `drex_fakes.py`, `test_drex_refresh.py`, `test_drex_executor.py` (modify). |
| `docs/drex.md` (modify) | Rows, order, limitations. |

Task order: 1 → 2.

---

### Task 1: Foreign-policy decision

**Files:** as in File Structure (all but docs).

**Interfaces:**
- `enumerate.py`: `foreign_policy_candidates(civs: list[lq.CivInfo], our_strength: int) -> list[Candidate]`:
  - Only civs with `has_met`. For an at-war civ: one `PROPOSE_PEACE` candidate labelled `Propose peace to <civ>`; nothing else for that civ.
  - Otherwise, for each label in `civ.available_actions or []`:
    - `DIPLOMATIC_DELEGATION`, `DECLARE_FRIENDSHIP`, `RESIDENT_EMBASSY`, `DENOUNCE` → `DIPLOMATIC_ACTION` with that action, labels `Send a delegation to <civ> (25 gold)`, `Declare friendship with <civ>`, `Open an embassy with <civ>`, `Denounce <civ>`.
    - a label starting with `Open Borders` → `DIPLOMATIC_ACTION` with action `OPEN_BORDERS`, label `Propose mutual open borders with <civ>`.
    - `MAKE_ALLIANCE` → `FORM_ALLIANCE` with `alliance_type="MILITARY"` (the Base ruleset has a single alliance kind; other types are Rise & Fall), label `Form an alliance with <civ>`.
    - `DECLARE_WAR` → `DIPLOMATIC_ACTION` with `DECLARE_SURPRISE_WAR`, label `Declare a surprise war on <civ>`; and additionally `DECLARE_FORMAL_WAR` labelled `Declare a formal war on <civ>` when `civ.diplomatic_state == "DENOUNCED"`.
  - Facts on every candidate: `civilization`, `leader`, `relationship` (`diplomatic_state`), `relationship_score`, `their_military_strength`, `our_military_strength`, `their_cities`, `grievances`, `alliance` (or None), `they_have_delegation`, `has_embassy`.
  - Always append `NO_DIPLOMACY` labelled `No diplomatic action this turn`.
- `candidates.py` ids as in the table; `PARAMS_FOR_KIND`, union, `candidate_id_for`.
- `observation.py`: `DecisionInputs.civs: list[lq.CivInfo] | None`.
- `live.py`: `case DecisionCategory.FOREIGN_POLICY: return DecisionInputs(civs=await gs.get_diplomacy())`.
- `points.py`: question `Which diplomatic move should the empire make this turn?`; `foreign_policy_candidates(inputs.civs, core.overview.military_strength)` (use `getattr(core.overview, "military_strength", 0)` if the overview model lacks the field; check `lq.GameOverview` first and use the real name).
- `scheduler.py`: `TurnLedger.foreign_policy_offered: bool`; after the purchase check and before Great People: `if not ledger.foreign_policy_offered and core.overview.turn >= 2: spec = DecisionSpec(FOREIGN_POLICY, "empire") ...`; `note` sets the flag on any outcome and resolves the key; `key_for` adds the category; `SCHEDULER_ORDER` line `"foreign_policy: once per turn (delegation, friendship, embassy, denounce, open borders, alliance, war, peace, or nothing)"`.
- `executor.py`:
  - dispatch: `DIPLOMATIC_ACTION` → `("send_diplomatic_action", (pid, action))`; `PROPOSE_PEACE` → `("propose_peace", (pid,))`; `FORM_ALLIANCE` → `("form_alliance", (pid, alliance_type))`; `NO_DIPLOMACY` → `NO_DISPATCH`.
  - precheck: known `inputs.civs` else `gs.get_diplomacy()`; civ must exist and `has_met`; for `DIPLOMATIC_ACTION` the action must map to a listed label (`DECLARE_SURPRISE_WAR`/`DECLARE_FORMAL_WAR` ↔ `DECLARE_WAR`, `OPEN_BORDERS` ↔ label starting `Open Borders`, others by name) and the civ not at war (except war declarations, which require not at war too); `PROPOSE_PEACE` requires `is_at_war`; `FORM_ALLIANCE` requires `MAKE_ALLIANCE` listed.
  - verify: `raw` starting with `ACCEPTED|`, `SENT|`, `WAR_DECLARED|`, `SESSION_CLOSED` → `confirmed` (`diplomatic_action_delivered`, evidence `answer=raw`); `REJECTED|` (peace refused) → `confirmed` with reason `offer_declined`; `WARN:`/`WAR_UNCERTAIN` → `pending`; `_game_error(raw)` → `rejected` (`diplomacy_refused`); otherwise `unknown`.
- `refresh.py`: `DIPLOMATIC_ACTION`, `PROPOSE_PEACE`, `FORM_ALLIANCE` → `{"overview", "sessions", "deals"}`; `NO_DIPLOMACY` → `frozenset()`.

- [ ] **Step 1: Fixtures and fake**

`drex_fixtures.py`:

```python
def civs():
    return [
        lq.CivInfo(
            player_id=1, civ_name="Egypt", leader_name="Cleopatra", has_met=True,
            is_at_war=False, diplomatic_state="NEUTRAL", relationship_score=-3,
            available_actions=["DIPLOMATIC_DELEGATION", "DECLARE_WAR"],
            military_strength=120, num_cities=3,
        ),
        lq.CivInfo(
            player_id=2, civ_name="Greece", leader_name="Gorgo", has_met=True,
            is_at_war=True, diplomatic_state="WAR", relationship_score=-40,
            available_actions=["DIPLOMATIC_DELEGATION"], military_strength=300, num_cities=5,
        ),
        lq.CivInfo(
            player_id=3, civ_name="Sumeria", leader_name="Gilgamesh", has_met=False,
            is_at_war=False,
        ),
    ]
```

(Check `lq.CivInfo`'s required positional fields in `models.py:359` and pass them all.)

`drex_fakes.py`: `self.civs = fx.civs()`; `self.war_uncertain = False`;

```python
    async def get_diplomacy(self):
        self.query_counts["get_diplomacy"] += 1
        self.conn.roundtrips += 1
        return copy.deepcopy(self.civs)

    async def send_diplomatic_action(self, other_player_id, action):
        fail = self._record("send_diplomatic_action", other_player_id, action)
        civ = next((c for c in self.civs if c.player_id == other_player_id), None)
        if civ is None or not civ.has_met:
            return "Error: NOT_MET|Have not met this civilization"
        listed = civ.available_actions or []
        if action.startswith("DECLARE_") and action.endswith("_WAR"):
            if "DECLARE_WAR" not in listed:
                return "Error: CANNOT_DECLARE_WAR|not allowed"
            civ.is_at_war, civ.diplomatic_state = True, "WAR"
            civ.available_actions = []
            self._after(fail)
            return "WARN:WAR_UNCERTAIN|declared" if self.war_uncertain else f"WAR_DECLARED|{civ.civ_name}"
        key = "Open Borders" if action == "OPEN_BORDERS" else action
        if not any(a.startswith(key) for a in listed):
            return f"Error: ACTION_INVALID|{action} not available"
        civ.available_actions = [a for a in listed if not a.startswith(key)]
        self._after(fail)
        return f"SENT|{action} to {civ.civ_name}" if action != "DECLARE_FRIENDSHIP" else f"ACCEPTED|{civ.civ_name} accepted friendship"

    async def propose_peace(self, other_player_id):
        fail = self._record("propose_peace", other_player_id)
        civ = next((c for c in self.civs if c.player_id == other_player_id), None)
        if civ is None or not civ.is_at_war:
            return "Error: NOT_AT_WAR|"
        self._after(fail)
        return f"REJECTED|{civ.civ_name} rejected your peace offer"

    async def form_alliance(self, other_player_id, alliance_type):
        fail = self._record("form_alliance", other_player_id, alliance_type)
        civ = next((c for c in self.civs if c.player_id == other_player_id), None)
        if civ is None or "MAKE_ALLIANCE" not in (civ.available_actions or []):
            return "Error: NOT_FRIENDS|Must be declared friends first"
        civ.alliance_type = alliance_type
        self._after(fail)
        return f"ALLIANCE_FORMED|{civ.civ_name}"
```

- [ ] **Step 2: Failing tests** — `tests/drex/test_drex_foreign_policy.py`: candidates (Egypt: delegation + surprise war; Greece: peace only; Sumeria: none; plus no-action; ids and facts incl. `their_military_strength`); `test_war_is_offered_only_when_the_engine_allows_it` (remove `DECLARE_WAR` from Egypt → no war candidate; formal war only when `DENOUNCED`); `test_at_war_civ_offers_only_peace`; `test_no_action_candidate_is_always_present` (even with no met civs → single forced candidate, no Drex call); scheduler offers FOREIGN_POLICY once per turn after PURCHASE (gold 300) and before UNIT; dispatch+confirm for delegation (`SENT|` → confirmed) and friendship (`ACCEPTED|`); `test_declined_proposals_confirm_with_the_answer_as_evidence` (peace → `REJECTED|` → CONFIRMED, reason `offer_declined`); war → `WAR_DECLARED|` confirmed, `war_uncertain=True` → PENDING; `test_precheck_rejects_an_action_the_engine_withdrew` (build point, then `game.civs[0].available_actions = []`, execute with current version and `inputs=None` → REJECTED, no call); refresh rules; DISPATCH_CASES entries for the three dispatching kinds and `NO_DIPLOMACY` in `NO_DISPATCH_KINDS`.
- [ ] **Step 3: RED**, **Step 4: implement** per Interfaces, **Step 5: suite green**, **Step 6: lint + commit** `Foreign policy is a once-per-turn Drex decision: delegations, friendship, embassies, denounce, open borders, alliances, war and peace`.

### Task 2: Docs and live restart

- [ ] `docs/drex.md`: scheduler order item (6d), a "Supported decisions" row, Limitations (trade deals deferred), status row for Phase 6.
- [ ] Suite green; commit `Document Phase 6: foreign policy`.
- [ ] Restart the live run and watch the log for the first `diplo:`/`peace:`/`alliance:` decisions and outcomes; record the first live outcome in the ledger.
