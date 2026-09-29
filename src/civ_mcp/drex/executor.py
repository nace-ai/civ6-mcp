"""Execute one stored candidate: fresh precheck, single dispatch, verification.

Outcome statuses:
- CONFIRMED: an observable postcondition shows the effect.
- PENDING: the engine accepted the request but the effect is asynchronous or
  only visible later (e.g. fortification, combat resolution).
- REJECTED: not dispatched (stale/ineligible) or the game reported an error
  and no effect was observed.
- UNKNOWN: dispatched but no effect observed, or the dispatch raised and
  reconciliation could not confirm it.

A candidate is dispatched at most once per turn. A dispatch that raises is
never retried; the executor reconciles by re-reading state instead.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from civ_mcp.connection import LuaError
from civ_mcp.drex.candidates import (
    ActionKind,
    AttackParams,
    Candidate,
    CivicParams,
    DealParams,
    DecisionPoint,
    DiplomacyParams,
    EnvoyParams,
    GovernmentParams,
    ImproveParams,
    KeepGovernmentParams,
    MoveParams,
    PantheonParams,
    PolicyParams,
    ProductionParams,
    ResearchParams,
    UnitOrderParams,
    UnitRef,
)
from civ_mcp.drex.decision import StaleDecision, ensure_current
from civ_mcp.drex.observation import DecisionInputs

EMPTY_QUEUE_STATES = frozenset({"nothing", "NONE", "CORRUPTED_QUEUE", ""})
CONSIDER_GOVERNMENT = "ENDTURN_BLOCKING_CONSIDER_GOVERNMENT_CHANGE"


class OutcomeStatus(StrEnum):
    CONFIRMED = "confirmed"
    PENDING = "pending"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


@dataclass
class ActionOutcome:
    status: OutcomeStatus
    reason: str
    dispatched: bool
    raw: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    reconciled: bool = False
    dispatch_error: str | None = None
    elapsed_ms: float = 0.0
    roundtrips: int = 0

    def to_record(self) -> dict[str, Any]:
        rec = dataclasses.asdict(self)
        rec["status"] = str(self.status)
        return rec


@dataclass(frozen=True)
class DispatchCall:
    method: str
    args: tuple[Any, ...]
    kwargs: dict[str, Any] = field(default_factory=dict)

    def to_record(self) -> dict[str, Any]:
        rec: dict[str, Any] = {
            "method": self.method,
            "args": [_plain(a) for a in self.args],
        }
        if self.kwargs:
            rec["kwargs"] = dict(self.kwargs)
        return rec


def _plain(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): v for k, v in value.items()}
    return value


def dispatch_call(candidate: Candidate) -> DispatchCall:
    """The single GameState call a candidate maps to."""
    p = candidate.params
    match candidate.kind, p:
        case ActionKind.SET_RESEARCH, ResearchParams():
            return DispatchCall("set_research", (p.tech_type,))
        case ActionKind.SET_CIVIC, CivicParams():
            return DispatchCall("set_civic", (p.civic_type,))
        case ActionKind.SET_PRODUCTION, ProductionParams():
            return DispatchCall(
                "set_city_production",
                (p.city_id, p.item_type, p.item_name, p.target_x, p.target_y),
            )
        case ActionKind.MOVE_UNIT, MoveParams():
            return DispatchCall("move_unit", (p.unit.unit_index, p.to_x, p.to_y))
        case ActionKind.ATTACK, AttackParams():
            return DispatchCall(
                "attack_unit", (p.unit.unit_index, p.target_x, p.target_y)
            )
        case ActionKind.FOUND_CITY, UnitOrderParams():
            return DispatchCall("found_city", (p.unit.unit_index,))
        case ActionKind.IMPROVE_TILE, ImproveParams():
            return DispatchCall("improve_tile", (p.unit.unit_index, p.improvement_type))
        case ActionKind.FORTIFY_UNIT, UnitOrderParams():
            return DispatchCall("fortify_unit", (p.unit.unit_index,))
        case ActionKind.HEAL_UNIT, UnitOrderParams():
            return DispatchCall("heal_unit", (p.unit.unit_index,))
        case ActionKind.SKIP_UNIT, UnitOrderParams():
            return DispatchCall("skip_unit", (p.unit.unit_index,))
        case ActionKind.DIPLOMACY_RESPOND, DiplomacyParams():
            return DispatchCall("diplomacy_respond", (p.other_player_id, p.response))
        case ActionKind.DEAL_RESPOND, DealParams():
            return DispatchCall("respond_to_deal", (p.other_player_id, p.accept))
        case ActionKind.SET_POLICY, PolicyParams():
            return DispatchCall("set_policies", ({p.slot_index: p.policy_type},))
        case ActionKind.SEND_ENVOY, EnvoyParams():
            return DispatchCall("send_envoy", (p.city_state_player_id,))
        case ActionKind.CHANGE_GOVERNMENT, GovernmentParams():
            return DispatchCall("change_government", (p.government_type,))
        case ActionKind.KEEP_GOVERNMENT, KeepGovernmentParams():
            return DispatchCall("keep_current_government", ())
        case ActionKind.CHOOSE_PANTHEON, PantheonParams():
            return DispatchCall("choose_pantheon", (p.belief_type,))
    raise TypeError(f"no dispatch for {candidate.kind} with {type(p).__name__}")


@dataclass
class _Precheck:
    ok: bool
    reason: str = ""
    state: dict[str, Any] = field(default_factory=dict)


def _ok(**state: Any) -> _Precheck:
    return _Precheck(True, "", state)


def _no(reason: str) -> _Precheck:
    return _Precheck(False, reason)


def _game_error(raw: str) -> bool:
    # Some results carry narration before the result line (e.g. attack_unit
    # prepends a combat estimate), so check every line.
    return any(line.startswith(("Error", "ERR:")) for line in raw.splitlines())


def _no_replay(gs: Any) -> contextlib.AbstractContextManager[Any]:
    conn = getattr(gs, "conn", None)
    replay_disabled = getattr(conn, "replay_disabled", None)
    return replay_disabled() if replay_disabled else contextlib.nullcontext()


class Executor:
    def __init__(
        self,
        gs: Any,
        *,
        poll_attempts: int = 6,
        poll_interval_s: float = 0.25,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
        popup_state_provider: Callable[[], str] | None = None,
    ):
        self.gs = gs
        # "CLEAR" | "POPUP" | "CRITICAL" as last observed; lets a move skip the
        # pre-dismiss round trip when nothing is on screen.
        self._popup_state = popup_state_provider
        self._poll_attempts = max(1, poll_attempts)
        self._poll_interval_s = poll_interval_s
        self._sleep = sleep
        self._dispatched: set[tuple[int, str, Any]] = set()
        # Reads made for the decision itself, reusable by the precheck while
        # the observation they were taken from is still current.
        self._known: DecisionInputs | None = None

    async def execute(
        self,
        candidate: Candidate,
        point: DecisionPoint,
        *,
        current_version: str,
        turn: int,
        inputs: DecisionInputs | None = None,
    ) -> ActionOutcome:
        started = time.perf_counter()
        self._known = (
            inputs
            if inputs is not None and point.observation_version == current_version
            else None
        )
        counters = getattr(getattr(self.gs, "conn", None), "snapshot_counters", None)
        rt0 = counters()[0] if counters else 0
        try:
            outcome = await self._execute(candidate, point, current_version, turn)
        finally:
            self._known = None
        outcome.elapsed_ms = round((time.perf_counter() - started) * 1000.0, 1)
        if counters:
            outcome.roundtrips = counters()[0] - rt0
        return outcome

    async def _execute(
        self, candidate: Candidate, point: DecisionPoint, version: str, turn: int
    ) -> ActionOutcome:
        try:
            ensure_current(point, version)
        except StaleDecision:
            return ActionOutcome(OutcomeStatus.REJECTED, "stale_observation", False)
        if point.get(candidate.candidate_id) is None:
            return ActionOutcome(
                OutcomeStatus.REJECTED, "not_a_candidate_of_decision", False
            )
        try:
            pre = await self._precheck(candidate)
        except Exception as e:  # a failed read must never lead to a dispatch
            return ActionOutcome(
                OutcomeStatus.REJECTED, f"precheck_error:{type(e).__name__}", False
            )
        # Repeatable decisions (another envoy, a reply in a new dialogue round)
        # are distinct actions only when the observed state differs.
        key = (turn, candidate.candidate_id, pre.state.get("dedup"))
        if key in self._dispatched:
            return ActionOutcome(
                OutcomeStatus.REJECTED, "already_dispatched_this_turn", False
            )
        if not pre.ok:
            return ActionOutcome(
                OutcomeStatus.REJECTED, f"precheck:{pre.reason}", False
            )

        call = dispatch_call(candidate)
        if (
            candidate.kind is ActionKind.MOVE_UNIT
            and self._popup_state is not None
            and self._popup_state() == "CLEAR"
        ):
            call = dataclasses.replace(
                call, kwargs={**call.kwargs, "predismiss": False}
            )
        self._dispatched.add(key)
        raw, error = "", None
        try:
            with _no_replay(self.gs):
                result = await getattr(self.gs, call.method)(*call.args, **call.kwargs)
            raw = result if isinstance(result, str) else str(result)
        except Exception as e:
            error = f"{type(e).__name__}: {e}"

        try:
            outcome = await self._verify(candidate, raw, pre.state)
        except Exception as e:
            outcome = ActionOutcome(
                OutcomeStatus.UNKNOWN, f"verify_error:{type(e).__name__}", True, raw
            )
        outcome.dispatched = True
        outcome.raw = raw
        if error is not None:
            outcome.dispatch_error = error
            outcome.reconciled = True
            if outcome.status is not OutcomeStatus.CONFIRMED:
                outcome.status = OutcomeStatus.UNKNOWN
                outcome.reason = f"dispatch_raised_unreconciled:{outcome.reason}"
        return outcome

    # ----------------------------------------------------------- prechecks
    async def _unit_space(self, ref: UnitRef) -> tuple[Any, str]:
        known = self._known.action_space if self._known is not None else None
        if known is not None and known.unit_id == ref.unit_id:
            space = known
        else:
            space = await self.gs.get_unit_action_space(ref.unit_index)
        if space is None:
            return None, "unit_gone"
        if space.unit_id != ref.unit_id:
            return None, "unit_identity_changed"
        if (space.x, space.y) != (ref.x, ref.y):
            return None, "unit_moved"
        if space.moves_remaining <= 0:
            return None, "no_moves"
        return space, ""

    async def _precheck(self, c: Candidate) -> _Precheck:
        p = c.params
        gs = self.gs
        match c.kind:
            case ActionKind.SET_RESEARCH | ActionKind.SET_CIVIC:
                known = self._known.progress if self._known is not None else None
                progress = known if known is not None else await gs.get_progress_types()
                if c.kind is ActionKind.SET_RESEARCH:
                    kind, target, current = "tech", p.tech_type, progress.research_type
                else:
                    kind, target, current = "civic", p.civic_type, progress.civic_type
                if current is not None:
                    return _no(f"{kind}_already_selected:{current}")
                eligible, how = await gs.check_eligibility(kind, target)
                return _ok(method=how) if eligible else _no(f"not_eligible:{how}")

            case ActionKind.SET_PRODUCTION:
                k = self._known
                if (
                    k is not None
                    and k.city is not None
                    and k.city.city_id == p.city_id
                    and k.production_options is not None
                ):
                    city, options = k.city, k.production_options
                else:
                    cities, _ = await gs.get_cities()
                    city = next((x for x in cities if x.city_id == p.city_id), None)
                    options = None
                if city is None:
                    return _no("city_gone")
                if city.currently_building not in EMPTY_QUEUE_STATES:
                    return _no(f"queue_not_empty:{city.currently_building}")
                if options is None:
                    options = await gs.list_city_production(p.city_id)
                for o in options:
                    same_target = p.target_x is None or (o.repair_x, o.repair_y) == (
                        p.target_x,
                        p.target_y,
                    )
                    if (
                        o.category == p.item_type
                        and o.item_name == p.item_name
                        and same_target
                    ):
                        return _ok()
                return _no("item_no_longer_available")

            case ActionKind.MOVE_UNIT:
                space, why = await self._unit_space(p.unit)
                if space is None:
                    return _no(why)
                dest = next(
                    (t for t in space.reachable if (t.x, t.y) == (p.to_x, p.to_y)), None
                )
                if dest is None or dest.own_stack_conflict or dest.visible_foreign_unit:
                    return _no("destination_not_reachable")
                return _ok(origin=(space.x, space.y))

            case ActionKind.ATTACK:
                space, why = await self._unit_space(p.unit)
                if space is None:
                    return _no(why)
                if not any(
                    (t.x, t.y, t.attack_type) == (p.target_x, p.target_y, p.attack_type)
                    for t in space.targets
                ):
                    return _no("target_not_available")
                return _ok(moves=space.moves_remaining)

            case (
                ActionKind.FOUND_CITY
                | ActionKind.FORTIFY_UNIT
                | ActionKind.HEAL_UNIT
                | ActionKind.SKIP_UNIT
            ):
                space, why = await self._unit_space(p.unit)
                if space is None:
                    return _no(why)
                if c.kind is ActionKind.FOUND_CITY and not space.can_found:
                    return _no("cannot_found_here")
                if c.kind is ActionKind.FORTIFY_UNIT and not space.can_fortify:
                    return _no("cannot_fortify")
                if c.kind is ActionKind.HEAL_UNIT and not space.can_heal:
                    return _no("cannot_heal")
                return _ok(moves=space.moves_remaining)

            case ActionKind.IMPROVE_TILE:
                units = await gs.get_units()
                unit = next((u for u in units if u.unit_id == p.unit.unit_id), None)
                if unit is None:
                    return _no("unit_gone")
                if (unit.x, unit.y) != (p.unit.x, p.unit.y):
                    return _no("unit_moved")
                if unit.moves_remaining <= 0 or unit.build_charges <= 0:
                    return _no("no_moves_or_charges")
                if p.improvement_type not in unit.valid_improvements:
                    return _no("improvement_not_valid_here")
                return _ok(charges=unit.build_charges)

            case ActionKind.DIPLOMACY_RESPOND:
                sessions = await gs.get_diplomacy_sessions()
                s = next(
                    (x for x in sessions if x.other_player_id == p.other_player_id),
                    None,
                )
                if s is None or s.is_at_war or s.buttons == "GOODBYE":
                    return _no("session_not_open_for_response")
                return _ok(dedup=(s.dialogue_text, s.reason_text, s.buttons))

            case ActionKind.DEAL_RESPOND:
                deals = await gs.get_pending_deals()
                if not any(d.other_player_id == p.other_player_id for d in deals):
                    return _no("deal_not_pending")
                return _ok()

            case ActionKind.SET_POLICY:
                status = await gs.get_policies()
                slot = next(
                    (s for s in status.slots if s.slot_index == p.slot_index), None
                )
                if slot is None:
                    return _no("slot_gone")
                if slot.current_policy:
                    return _no(f"slot_filled:{slot.current_policy}")
                if any(s.current_policy == p.policy_type for s in status.slots):
                    return _no("policy_already_slotted")
                if not any(
                    a.policy_type == p.policy_type for a in status.available_policies
                ):
                    return _no("policy_not_available")
                return _ok()

            case ActionKind.SEND_ENVOY:
                status = await gs.get_city_states()
                cs = next(
                    (
                        x
                        for x in status.city_states
                        if x.player_id == p.city_state_player_id
                    ),
                    None,
                )
                if status.tokens_available <= 0 or cs is None or not cs.can_send_envoy:
                    return _no("cannot_send_envoy")
                return _ok(
                    tokens=status.tokens_available, dedup=status.tokens_available
                )

            case ActionKind.CHANGE_GOVERNMENT:
                govs = await gs.get_available_governments()
                g = next(
                    (x for x in govs if x.government_type == p.government_type), None
                )
                if g is None:
                    return _no("government_not_unlocked")
                if g.is_current:
                    return _no("government_already_current")
                return _ok()

            case ActionKind.KEEP_GOVERNMENT:
                return _ok()

            case ActionKind.CHOOSE_PANTHEON:
                status = await gs.get_pantheon_status()
                if status.has_pantheon:
                    return _no("pantheon_already_chosen")
                if not any(
                    b.belief_type == p.belief_type for b in status.available_beliefs
                ):
                    return _no("belief_not_available")
                return _ok()
        return _no(f"unsupported_kind:{c.kind}")

    # --------------------------------------------------------- verification
    async def _poll(self, check: Callable[[], Awaitable[Any]]) -> Any:
        result = None
        for n in range(self._poll_attempts):
            result = await check()
            if result:
                return result
            if n + 1 < self._poll_attempts:
                await self._sleep(self._poll_interval_s)
        return result

    def _unconfirmed(self, raw: str, reason: str, **evidence: Any) -> ActionOutcome:
        if _game_error(raw):
            return ActionOutcome(
                OutcomeStatus.REJECTED, f"game_error:{reason}", True, evidence=evidence
            )
        return ActionOutcome(OutcomeStatus.UNKNOWN, reason, True, evidence=evidence)

    async def _verify(
        self, c: Candidate, raw: str, pre: dict[str, Any]
    ) -> ActionOutcome:
        p = c.params
        gs = self.gs
        confirmed = lambda reason, **ev: ActionOutcome(
            OutcomeStatus.CONFIRMED, reason, True, evidence=ev
        )
        match c.kind:
            case ActionKind.SET_RESEARCH | ActionKind.SET_CIVIC:
                research = c.kind is ActionKind.SET_RESEARCH
                target = p.tech_type if research else p.civic_type

                async def applied():
                    progress = await gs.get_progress_types()
                    now = progress.research_type if research else progress.civic_type
                    return now == target

                if await self._poll(applied):
                    return confirmed(
                        "selection_observed",
                        gamecore_fallback="GAMECORE" in raw or "_GC" in raw,
                    )
                return self._unconfirmed(raw, "selection_not_observed")

            case ActionKind.SET_PRODUCTION:
                try:
                    if await self._poll(
                        lambda: gs.verify_production(p.city_id, p.item_name)
                    ):
                        return confirmed("production_readback_confirmed")
                except LuaError:
                    pass  # readback unavailable; fall back to the dispatch output
                if raw.startswith("PRODUCING|") and f"|{p.item_name}|" in raw + "|":
                    return confirmed("production_confirmed_from_dispatch")
                return self._unconfirmed(raw, "production_not_observed")

            case ActionKind.MOVE_UNIT:
                origin = pre["origin"]
                last = None
                m = re.search(r"\|now_at:(\d+),(\d+)", raw)
                if m and (int(m.group(1)), int(m.group(2))) == (p.to_x, p.to_y):
                    return confirmed("arrived_from_dispatch", position=[p.to_x, p.to_y])

                async def arrived():
                    nonlocal last
                    last = await gs.get_unit_state(p.unit.unit_index)
                    return last is not None and (last.x, last.y) == (p.to_x, p.to_y)

                if await self._poll(arrived):
                    return confirmed("arrived", position=[p.to_x, p.to_y])
                if last is None:
                    return self._unconfirmed(raw, "unit_gone_after_move")
                if (last.x, last.y) != origin:
                    return ActionOutcome(
                        OutcomeStatus.PENDING,
                        "moved_partially",
                        True,
                        evidence={"position": [last.x, last.y]},
                    )
                return self._unconfirmed(
                    raw, "no_position_change", position=[last.x, last.y]
                )

            case ActionKind.ATTACK:
                if _game_error(raw):
                    return self._unconfirmed(raw, "attack_refused")
                state = await gs.get_unit_state(p.unit.unit_index)
                if state is None:
                    return confirmed("attacker_gone_after_combat")
                if state.moves_remaining < pre["moves"]:
                    return confirmed("attack_spent_moves", attacker_hp=state.hp)
                return ActionOutcome(
                    OutcomeStatus.PENDING, "combat_resolution_async", True
                )

            case ActionKind.FOUND_CITY:
                ref = p.unit
                if await self._poll(lambda: gs.city_exists_at(ref.x, ref.y)):
                    return confirmed("city_observed", position=[ref.x, ref.y])
                return self._unconfirmed(raw, "city_not_observed")

            case ActionKind.IMPROVE_TILE:
                ref = p.unit

                async def built():
                    tiles = await gs.get_map_area(ref.x, ref.y, 0)
                    tile = next(
                        (t for t in tiles if (t.x, t.y) == (ref.x, ref.y)), None
                    )
                    return tile is not None and tile.improvement == p.improvement_type

                if await self._poll(built):
                    return confirmed("improvement_observed")
                return self._unconfirmed(raw, "improvement_not_observed")

            case ActionKind.FORTIFY_UNIT | ActionKind.HEAL_UNIT:
                state = await gs.get_unit_state(p.unit.unit_index)
                if state is not None and state.fortify_turns > 0:
                    return confirmed("fortification_observed")
                if _game_error(raw):
                    return self._unconfirmed(raw, "order_refused")
                return ActionOutcome(
                    OutcomeStatus.PENDING, "order_accepted_effect_later", True
                )

            case ActionKind.SKIP_UNIT:

                async def done():
                    state = await gs.get_unit_state(p.unit.unit_index)
                    return state is not None and state.moves_remaining <= 0

                if await self._poll(done):
                    return confirmed("moves_spent")
                return self._unconfirmed(raw, "moves_not_spent")

            case ActionKind.DIPLOMACY_RESPOND:
                if _game_error(raw):
                    return self._unconfirmed(raw, "response_refused")
                sessions = await gs.get_diplomacy_sessions()
                still = any(s.other_player_id == p.other_player_id for s in sessions)
                return confirmed("response_delivered", session_open=still)

            case ActionKind.DEAL_RESPOND:

                async def gone():
                    deals = await gs.get_pending_deals()
                    return not any(
                        d.other_player_id == p.other_player_id for d in deals
                    )

                if await self._poll(gone):
                    return confirmed("deal_resolved")
                return self._unconfirmed(raw, "deal_still_pending")

            case ActionKind.SET_POLICY:

                async def slotted():
                    status = await gs.get_policies()
                    slot = next(
                        (s for s in status.slots if s.slot_index == p.slot_index), None
                    )
                    return slot is not None and slot.current_policy == p.policy_type

                if await self._poll(slotted):
                    return confirmed("policy_slotted")
                return self._unconfirmed(raw, "policy_not_slotted")

            case ActionKind.SEND_ENVOY:

                async def spent():
                    status = await gs.get_city_states()
                    return status.tokens_available < pre["tokens"]

                if await self._poll(spent):
                    return confirmed("envoy_token_spent")
                return self._unconfirmed(raw, "envoy_token_not_spent")

            case ActionKind.CHANGE_GOVERNMENT:

                async def adopted():
                    status = await gs.get_policies()
                    return status.government_type == p.government_type

                if await self._poll(adopted):
                    return confirmed("government_observed")
                return self._unconfirmed(raw, "government_not_observed")

            case ActionKind.KEEP_GOVERNMENT:

                async def cleared():
                    blockers = await gs.get_end_turn_blockers()
                    return all(b[0] != CONSIDER_GOVERNMENT for b in blockers)

                if await self._poll(cleared):
                    return confirmed("government_prompt_cleared")
                return self._unconfirmed(raw, "government_prompt_still_blocking")

            case ActionKind.CHOOSE_PANTHEON:

                async def chosen():
                    status = await gs.get_pantheon_status()
                    return (
                        status.has_pantheon and status.current_belief == p.belief_type
                    )

                if await self._poll(chosen):
                    return confirmed("pantheon_observed")
                return self._unconfirmed(raw, "pantheon_not_observed")
        return ActionOutcome(OutcomeStatus.UNKNOWN, "no_verifier", True)
