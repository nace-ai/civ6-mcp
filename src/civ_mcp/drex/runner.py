"""The decision loop: observe -> schedule -> enumerate -> select -> execute."""

from __future__ import annotations

import asyncio
import dataclasses
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from civ_mcp.drex.candidates import ActionKind
from civ_mcp.drex.decision_log import DecisionLog
from civ_mcp.drex.executor import Executor, dispatch_call
from civ_mcp.drex.live import LiveObserver
from civ_mcp.drex.observation import CoreObservation, DecisionMemory, Fact
from civ_mcp.drex.points import build_decision_point
from civ_mcp.drex.refresh import refresh_parts
from civ_mcp.drex.scheduler import SCHEDULER_ORDER, EndTurn, Scheduler, Stop, TurnLedger
from civ_mcp.drex.selectors import SelectionPaused, Selector, build_request, select
from civ_mcp.drex.serialize import to_jsonable
from civ_mcp.drex.spectate import Spectator, focus_point
from civ_mcp.end_turn import EndTurnOutcome, execute_end_turn_typed
from civ_mcp.game_lifecycle import save_game

DEFAULT_OBJECTIVE = (
    "Develop the empire: found cities on good land, keep every city producing, "
    "research steadily, and keep units and cities safe."
)
INTERRUPTION_KEY = "end_turn_interrupted"


@dataclass
class RunConfig:
    turns: int = 20
    objective: str = DEFAULT_OBJECTIVE
    max_options: int = 255
    nearby_radius: int = 2
    dry_run: bool = False
    max_unit_decisions: int = 3
    max_decisions_per_turn: int = 80
    max_failures_per_key: int = 2
    max_session_close_attempts: int = 2
    max_interruptions_per_turn: int = 2


@dataclass
class RunResult:
    stop_reason: str
    turns_advanced: int
    decisions: int
    start_turn: int | None
    final_turn: int | None
    checkpoint: str | None


async def _default_end_turn(gs: Any) -> EndTurnOutcome:
    return await execute_end_turn_typed(gs, decision_only=True)


async def _default_checkpoint(gs: Any, turn: int) -> str:
    name = f"DREX_CHECKPOINT_T{turn:04d}"
    await save_game(gs.conn, name)
    return name


def _describe_block(outcome: EndTurnOutcome) -> str:
    what = [b[0] for b in outcome.blockers]
    what += [f"diplomacy_player_{p}" for p in outcome.diplomacy_pending]
    what += [f"deal_player_{p}" for p in outcome.deals_pending]
    if outcome.world_congress_pending:
        what.append("world_congress")
    return ",".join(what) or outcome.status


class Runner:
    def __init__(
        self,
        gs: Any,
        selector: Selector | None,
        log: DecisionLog,
        config: RunConfig,
        *,
        end_turn: Callable[[Any], Awaitable[EndTurnOutcome]] | None = None,
        checkpoint: Callable[[Any, int], Awaitable[str]] | None = None,
        observer: LiveObserver | None = None,
        executor: Executor | None = None,
        run_meta: dict[str, Any] | None = None,
        spectator: Spectator | None = None,
    ):
        if selector is None and not config.dry_run:
            raise ValueError("a selector is required unless dry_run is set")
        self.gs = gs
        self.selector = selector
        self.log = log
        self.cfg = config
        self.preview: dict[str, Any] | None = None
        self._end_turn = end_turn or _default_end_turn
        self._checkpoint = checkpoint or _default_checkpoint
        self.observer = observer or LiveObserver(gs, nearby_radius=config.nearby_radius)
        self.executor = executor or Executor(
            gs,
            popup_state_provider=lambda: (
                self._last_core.popup_state if self._last_core else "POPUP"
            ),
        )
        self.spectator = spectator
        self.scheduler = Scheduler(
            max_unit_decisions=config.max_unit_decisions,
            max_decisions_per_turn=config.max_decisions_per_turn,
            max_failures_per_key=config.max_failures_per_key,
        )
        self.memory = DecisionMemory()
        self.run_meta = run_meta or {}
        self._seq = 0
        self._decisions = 0
        self._turns = 0
        self._start_turn: int | None = None
        self._api_ms = 0.0
        self._game_ms = 0.0
        self._t0 = time.perf_counter()
        self._last_core: CoreObservation | None = None

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
        self._game_ms += (time.perf_counter() - t0) * 1000.0
        self._last_core = core
        return core

    async def run(self) -> RunResult:
        self._t0 = time.perf_counter()
        if self.spectator is not None:
            self.spectator.start()
        try:
            return await self._run()
        except asyncio.CancelledError:
            self._write_stop(self._result("interrupted", self._last_core, None))
            raise
        except Exception as e:
            return await self._stop(
                f"error:{type(e).__name__}: {e}", self._last_core, checkpoint=True
            )
        finally:
            if self.spectator is not None:
                await self.spectator.stop()

    def _follow(self, candidate: Any, core: CoreObservation) -> None:
        if self.spectator is None:
            return
        where = focus_point(candidate, core)
        if where is not None:
            self.spectator.focus(*where)

    async def _run(self) -> RunResult:
        core = await self._observe()
        identity = core.game_identity
        self.memory.bind_game(identity)
        self._start_turn = core.turn
        self.log.write(
            "header",
            {
                **self.run_meta,
                "selector": self.selector.name if self.selector else None,
                "game": {
                    "civ": core.civ,
                    "seed": core.seed,
                    "player_id": core.local_player_id,
                },
                "start_turn": core.turn,
                "config": dataclasses.asdict(self.cfg),
                "scheduler_order": list(SCHEDULER_ORDER),
            },
        )
        ledger = TurnLedger(turn=core.turn, full_observed=True)
        close_attempts: dict[tuple[int, int], int] = {}
        # An end-turn request the game is still processing (AI turn paused on
        # a proposal): only sessions and deals are observed until it resumes.
        in_flight = False
        # The last blocked end turn, cleared by any decision or housekeeping.
        # Ending the turn again with nothing changed would be a blind retry.
        blocked: EndTurnOutcome | None = None
        retry_allowed = False

        while True:
            if self._turns >= self.cfg.turns:
                return await self._stop("turn_budget_reached", core, checkpoint=False)
            if core.game_identity != identity:
                return await self._stop("game_identity_changed", core, checkpoint=False)
            if core.turn != ledger.turn:
                # A new turn always starts from the full observation taken
                # after the end turn advanced.
                ledger = TurnLedger(turn=core.turn, full_observed=True)

            informational = self.scheduler.informational_sessions(core)
            if informational and not self.cfg.dry_run:
                for pid in informational:
                    n = close_attempts.get((core.turn, pid), 0) + 1
                    close_attempts[(core.turn, pid)] = n
                    if n > self.cfg.max_session_close_attempts:
                        return await self._stop(
                            f"informational_session_stuck:player_{pid}",
                            core,
                            checkpoint=True,
                        )
                    raw = await self.gs.diplomacy_respond(pid, "EXIT")
                    self.log.write(
                        "housekeeping",
                        {
                            "turn": core.turn,
                            "action": "informational_session_closed",
                            "player": pid,
                            "raw": raw,
                        },
                    )
                blocked = None
                core = await self._observe(reactive_from=core if in_flight else None)
                continue
            if informational:
                self.log.write(
                    "housekeeping_planned",
                    {
                        "turn": core.turn,
                        "action": "close_informational_sessions",
                        "players": informational,
                    },
                )

            step = (
                self.scheduler.next_reactive(core, ledger) or EndTurn()
                if in_flight
                else self.scheduler.next(core, ledger)
            )
            if isinstance(step, Stop):
                return await self._stop(step.reason, core, checkpoint=True)

            if isinstance(step, EndTurn):
                if not in_flight and not ledger.full_observed:
                    # Safety net for partial refreshes: one full read per turn
                    # before the turn is ended.
                    ledger.full_observed = True
                    core = await self._observe()
                    continue
                if not in_flight:
                    unsupported = self.scheduler.unsupported_blockers(core)
                    if unsupported:
                        return await self._stop(
                            "unsupported_blocker:" + ",".join(unsupported),
                            core,
                            checkpoint=True,
                        )
                if self.cfg.dry_run:
                    return await self._stop("dry_run_complete", core, checkpoint=False)
                if blocked is not None and not retry_allowed:
                    return await self._stop(
                        f"end_turn_{blocked.status}:{_describe_block(blocked)}",
                        core,
                        checkpoint=True,
                    )
                retry_allowed = False
                t0 = time.perf_counter()
                outcome = await self._end_turn(self.gs)
                elapsed = (time.perf_counter() - t0) * 1000.0
                self._game_ms += elapsed
                self._log_turn(outcome, elapsed)
                in_flight = outcome.status == "blocked" and outcome.end_turn_in_flight
                if outcome.status == "advanced":
                    self._turns += 1
                    blocked = None
                    if self.spectator is not None:
                        self.spectator.turn_advanced()
                    self.memory.record(
                        Fact(
                            outcome.turn_before or core.turn,
                            "turn",
                            "empire",
                            "end turn",
                            "advanced",
                        )
                    )
                elif outcome.status == "blocked":
                    if outcome.world_congress_pending:
                        return await self._stop(
                            "unsupported_blocker:world_congress", core, checkpoint=True
                        )
                    blocked = outcome
                elif outcome.status == "interrupted":
                    ledger.counts[INTERRUPTION_KEY] += 1
                    blocked = outcome
                    retry_allowed = (
                        ledger.counts[INTERRUPTION_KEY]
                        <= self.cfg.max_interruptions_per_turn
                    )
                elif outcome.status == "game_over":
                    return await self._stop("game_over", core, checkpoint=False)
                else:
                    return await self._stop(
                        f"end_turn_{outcome.status}", core, checkpoint=True
                    )
                core = await self._observe(reactive_from=core if in_flight else None)
                continue

            inputs = await self.observer.inputs(step, core)
            self._seq += 1
            decision_id = f"T{core.turn}#{self._seq:04d}"
            point, excluded = build_decision_point(
                step,
                core,
                inputs,
                self.memory,
                objective=self.cfg.objective,
                decision_id=decision_id,
                max_options=self.cfg.max_options,
                failed=ledger.failed_candidates,
                exclude_kinds=(
                    frozenset({ActionKind.MOVE_UNIT})
                    if self.scheduler.final_unit_decision(ledger, step)
                    else frozenset()
                ),
            )
            if point is None:
                self.scheduler.exhaust(ledger, step)
                self.log.write(
                    "no_candidates",
                    {
                        "turn": core.turn,
                        "decision_id": decision_id,
                        "category": str(step.category),
                        "entity": step.entity,
                        "exclusions": [dataclasses.asdict(e) for e in excluded],
                    },
                )
                continue

            request = build_request(point)
            if self.cfg.dry_run:
                self.preview = {
                    "decision_id": decision_id,
                    "objective": self.cfg.objective,
                    "spec": to_jsonable(step),
                    "core": to_jsonable(core),
                    "inputs": to_jsonable(inputs),
                }
            if self.selector is None:
                self.log.write(
                    "dry_run",
                    {
                        **point.to_record(),
                        "turn": core.turn,
                        "context": dict(point.context),
                        "request": request,
                        "decision": None,
                        "dispatch": None,
                    },
                )
                return await self._stop("dry_run_complete", core, checkpoint=False)
            try:
                result = await select(point, self.selector)
            except SelectionPaused as e:
                self.log.write(
                    "selection_paused",
                    {
                        **point.to_record(),
                        "turn": core.turn,
                        "request": request,
                        "reason": str(e),
                        "attempts": e.attempts,
                    },
                )
                return await self._stop(f"selector_paused:{e}", core, checkpoint=True)
            api_ms = sum(a.get("latency_ms") or 0.0 for a in result.attempts)
            self._api_ms += api_ms
            candidate = point.get(result.decision.candidate_id)
            call = dispatch_call(candidate)

            if self.cfg.dry_run:
                self.log.write(
                    "dry_run",
                    {
                        **point.to_record(),
                        "turn": core.turn,
                        "context": dict(point.context),
                        "request": request,
                        "decision": result.decision.to_record(),
                        "attempts": result.attempts,
                        "dispatch": call.to_record(),
                    },
                )
                return await self._stop("dry_run_complete", core, checkpoint=False)

            outcome = await self.executor.execute(
                candidate,
                point,
                current_version=self.observer.version,
                turn=core.turn,
                inputs=inputs,
            )
            self._game_ms += outcome.elapsed_ms
            self._decisions += 1
            blocked = None
            if outcome.dispatched:
                self._follow(candidate, core)
            self.scheduler.note(
                ledger, step, candidate.kind, outcome, candidate.candidate_id
            )
            self.memory.record(
                Fact(
                    core.turn,
                    str(step.category),
                    point.entity,
                    candidate.label,
                    str(outcome.status),
                )
            )
            self.log.write(
                "decision",
                {
                    **point.to_record(),
                    "turn": core.turn,
                    "request": request if result.request is not None else None,
                    "decision": result.decision.to_record(),
                    "attempts": result.attempts,
                    "dispatch": call.to_record(),
                    "outcome": outcome.to_record(),
                    "timing_ms": {
                        "api": round(api_ms, 1),
                        "execute": outcome.elapsed_ms,
                    },
                },
            )
            if in_flight:
                core = await self._observe(reactive_from=core)
            elif outcome.dispatched:
                core = await self._observe(
                    refresh=(refresh_parts(candidate.kind), core)
                )
                ledger.full_observed = False
            else:
                core = await self._observe(
                    refresh=(frozenset({"blockers", "popup"}), core)
                )

    def _log_turn(self, outcome: EndTurnOutcome, elapsed_ms: float) -> None:
        self.log.write(
            "turn",
            {
                "status": outcome.status,
                "turn_before": outcome.turn_before,
                "turn_after": outcome.turn_after,
                "blockers": [list(b) for b in outcome.blockers],
                "diplomacy_pending": outcome.diplomacy_pending,
                "deals_pending": outcome.deals_pending,
                "world_congress_pending": outcome.world_congress_pending,
                "end_turn_in_flight": outcome.end_turn_in_flight,
                "housekeeping": outcome.housekeeping,
                "game_over": dataclasses.asdict(outcome.game_over)
                if outcome.game_over
                else None,
                "end_turn_ms": round(elapsed_ms, 1),
                "report": outcome.report,
            },
        )

    def _result(
        self, reason: str, core: CoreObservation | None, checkpoint: str | None
    ) -> RunResult:
        return RunResult(
            stop_reason=reason,
            turns_advanced=self._turns,
            decisions=self._decisions,
            start_turn=self._start_turn,
            final_turn=core.turn if core else None,
            checkpoint=checkpoint,
        )

    def _write_stop(self, result: RunResult) -> None:
        self.log.write(
            "stop",
            {
                **dataclasses.asdict(result),
                "api_ms_total": round(self._api_ms, 1),
                "game_ms_total": round(self._game_ms, 1),
                "wall_ms": round((time.perf_counter() - self._t0) * 1000.0, 1),
            },
        )

    async def _stop(
        self, reason: str, core: CoreObservation | None, *, checkpoint: bool
    ) -> RunResult:
        name = None
        if checkpoint and not self.cfg.dry_run and core is not None:
            try:
                name = await self._checkpoint(self.gs, core.turn)
            except Exception as e:
                name = f"checkpoint_failed:{type(e).__name__}"
        result = self._result(reason, core, name)
        self._write_stop(result)
        return result
