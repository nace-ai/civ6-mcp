"""The decision loop: observe -> schedule -> enumerate -> select -> execute."""

from __future__ import annotations

import asyncio
import dataclasses
import time
import traceback
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from civ_mcp.connection import LuaError
from civ_mcp.drex.candidates import ActionKind
from civ_mcp.drex.decision_log import DecisionLog
from civ_mcp.drex.executor import Executor, dispatch_call
from civ_mcp.drex.live import LiveObserver
from civ_mcp.drex.observation import CoreObservation, DecisionMemory, Fact
from civ_mcp.drex.points import build_decision_point
from civ_mcp.drex.refresh import refresh_parts
from civ_mcp.drex.scheduler import SCHEDULER_ORDER, EndTurn, Scheduler, TurnLedger
from civ_mcp.drex.selectors import (
    ControllerFilteringError,
    Selector,
    build_request,
    select,
)
from civ_mcp.drex.serialize import to_jsonable
from civ_mcp.drex.spectate import Spectator, focus_point
from civ_mcp.end_turn import EndTurnOutcome, execute_end_turn_typed
from civ_mcp.game_lifecycle import save_game

DEFAULT_OBJECTIVE = (
    "Develop the empire: found cities on good land, keep every city producing, "
    "research steadily, and keep units and cities safe."
)
INTERRUPTION_KEY = "end_turn_interrupted"
# Tuner-side failures the runner recovers from by reconnecting (never by
# re-sending a mutation: dispatch errors are reconciled by the executor).
_IO_ERRORS = (ConnectionError, OSError, LuaError, TimeoutError)


@dataclass
class RunConfig:
    turns: int = 20
    objective: str = DEFAULT_OBJECTIVE
    max_options: int = 255
    nearby_radius: int = 2
    dry_run: bool = False
    max_unit_decisions: int = 3
    max_decisions_per_turn: int = 200
    max_failures_per_key: int = 2
    max_session_close_attempts: int = 2
    max_interruptions_per_turn: int = 2
    # A blocker no decision kind handles yet: wait, re-observe, never stop.
    unsupported_wait_s: float = 30.0
    # A blocked end turn that repeats with no decision in between: dismiss
    # popups and try once more after this many repeats.
    blocked_repeats_before_dismiss: int = 3
    # Pause between repeated blocked end turns so the loop does not hammer
    # the game while it waits for something to change.
    blocked_repeat_wait_s: float = 1.0
    # Game connection recovery: reconnect backoff, and how long an outage may
    # last before the game process is relaunched (needs a windowed game).
    reconnect_backoff_s: float = 1.0
    reconnect_max_backoff_s: float = 30.0
    game_dead_after_s: float = 120.0


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
        on_turn: Callable[[dict[str, Any]], None] | None = None,
        relaunch: Callable[[], Awaitable[str]] | None = None,
        clock: Callable[[], float] = time.monotonic,
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
        self._on_turn = on_turn
        self._current_decision_id: str | None = None
        self._sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep
        # Tests only: bound the loop so a deliberately stuck game ends the run
        # as "interrupted" instead of spinning.
        self.max_loop_iterations: int | None = None
        self._iterations = 0
        self._relaunch = relaunch
        self._clock = clock
        self._phase = "schedule"
        self._header_written = False
        self._io_ok = False
        self._last_observe = {"ms": 0.0, "roundtrips": 0}
        self._turn_stats = {"decisions": 0, "api_ms": 0.0, "roundtrips": 0, "t0": 0.0}

    async def _observe(
        self,
        *,
        reactive_from: CoreObservation | None = None,
        refresh: tuple[frozenset[str], CoreObservation] | None = None,
    ) -> CoreObservation:
        self._phase = "observe"
        t0 = time.perf_counter()
        rt0 = self._roundtrips()
        if reactive_from is not None:
            core = await self.observer.reactive(reactive_from)
        elif refresh is not None:
            core = await self.observer.refresh(refresh[1], refresh[0])
        else:
            core = await self.observer.core()
        ms = (time.perf_counter() - t0) * 1000.0
        self._game_ms += ms
        self._last_observe = {
            "ms": round(ms, 1),
            "roundtrips": self._roundtrips() - rt0,
        }
        self._io_ok = True
        self._phase = "schedule"
        self._turn_stats["roundtrips"] += self._last_observe["roundtrips"]
        self._last_core = core
        if self.spectator is not None:
            self.spectator.popup_status(core.popup_state)
        return core

    async def _wait_unsupported(
        self, core: CoreObservation, blockers: list[str]
    ) -> None:
        """A prompt no decision kind handles yet. Log it and wait; a restart on
        newer code picks the game up from here. The run never stops for it."""
        self.log.write(
            "unsupported_blocker",
            {
                "turn": core.turn,
                "blockers": blockers,
                "wait_s": self.cfg.unsupported_wait_s,
            },
        )
        await self._sleep(self.cfg.unsupported_wait_s)

    def selection_waiting(self, info: dict[str, Any]) -> None:
        """Selector callback: Drex could not answer yet; the run keeps waiting."""
        self.log.write(
            "selection_waiting",
            {
                **info,
                "turn": self._last_core.turn if self._last_core else None,
                "decision_id": self._current_decision_id,
            },
        )

    def _roundtrips(self) -> int:
        counters = getattr(getattr(self.gs, "conn", None), "snapshot_counters", None)
        return counters()[0] if counters else 0

    async def run(self) -> RunResult:
        """Run to game over or the turn budget. Tuner failures reconnect (and
        relaunch the game after ``game_dead_after_s``); anything else is logged
        with a checkpoint and the loop resumes. Only Ctrl-C ends it early."""
        self._t0 = time.perf_counter()
        self._turn_stats["t0"] = self._t0
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
                    if self._io_ok:
                        # progress since the last failure: a new outage
                        attempt, outage_started, relaunched = 0, None, False
                        self._io_ok = False
                    attempt += 1
                    now = self._clock()
                    if outage_started is None:
                        outage_started = now
                    self._log_io_error(e, self._phase, attempt)
                    wait = min(
                        self.cfg.reconnect_backoff_s * (2 ** (attempt - 1)),
                        self.cfg.reconnect_max_backoff_s,
                    )
                    await self._sleep(wait)
                    if (
                        not relaunched
                        and self._relaunch is not None
                        and self._clock() - outage_started >= self.cfg.game_dead_after_s
                    ):
                        relaunched = True
                        result = await self._relaunch()
                        self.log.write("game_relaunch", {"result": result})
                    try:
                        await self.gs.conn.reconnect()
                    except _IO_ERRORS as re_err:
                        self._log_io_error(re_err, "reconnect", attempt)
                        continue
                except Exception as e:  # noqa: BLE001 — logged with traceback; the run resumes
                    self.log.write(
                        "runner_error",
                        {
                            "error": f"{type(e).__name__}: {e}",
                            "traceback": traceback.format_exc()[-4000:],
                            "phase": self._phase,
                            "turn": self._last_core.turn if self._last_core else None,
                        },
                    )
                    if self._last_core is not None and not self.cfg.dry_run:
                        try:
                            await self._checkpoint(self.gs, self._last_core.turn)
                        except Exception as ce:  # noqa: BLE001
                            self.log.write(
                                "checkpoint_failed",
                                {"error": f"{type(ce).__name__}: {ce}"},
                            )
                    await self._sleep(self.cfg.reconnect_backoff_s)
        finally:
            if self.spectator is not None:
                await self.spectator.stop()

    def _write_header(self, core: CoreObservation) -> None:
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

    def _log_io_error(self, e: BaseException, phase: str, attempt: int) -> None:
        self.log.write(
            "game_io_error",
            {
                "error": f"{type(e).__name__}: {e}",
                "phase": phase,
                "attempt": attempt,
                "turn": self._last_core.turn if self._last_core else None,
            },
        )

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
        if self._start_turn is None:
            self._start_turn = core.turn
        if not self._header_written:
            self._header_written = True
            self._write_header(core)
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
            self._iterations += 1
            if (
                self.max_loop_iterations is not None
                and self._iterations > self.max_loop_iterations
            ):
                return await self._stop("interrupted", core, checkpoint=False)
            if self._turns >= self.cfg.turns:
                return await self._stop("turn_budget_reached", core, checkpoint=False)
            if core.game_identity != identity:
                self.log.write(
                    "identity_changed",
                    {"from": list(identity), "to": list(core.game_identity)},
                )
                identity = core.game_identity
                self.memory = DecisionMemory()
                self.memory.bind_game(identity)
                self.observer._wonders = None
                ledger = TurnLedger(turn=core.turn, full_observed=True)
                blocked, retry_allowed, in_flight = None, False, False
            if core.turn != ledger.turn:
                # A new turn always starts from the full observation taken
                # after the end turn advanced.
                ledger = TurnLedger(turn=core.turn, full_observed=True)

            informational = self.scheduler.informational_sessions(
                core, exclude=ledger.stuck_sessions
            )
            if informational and not self.cfg.dry_run:
                for pid in informational:
                    n = close_attempts.get((core.turn, pid), 0) + 1
                    close_attempts[(core.turn, pid)] = n
                    if n > self.cfg.max_session_close_attempts:
                        # Leave it for this turn; the scheduler proceeds past it.
                        ledger.stuck_sessions.add(pid)
                        self.log.write(
                            "informational_session_stuck",
                            {"turn": core.turn, "player": pid, "attempts": n},
                        )
                        continue
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
            if (
                isinstance(step, EndTurn)
                and ledger.budget_hit
                and not ledger.budget_logged
            ):
                ledger.budget_logged = True
                self.log.write(
                    "budget_end_turn",
                    {"turn": core.turn, "decisions": ledger.decisions},
                )

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
                        if self.cfg.dry_run:
                            return await self._stop(
                                "dry_run_complete", core, checkpoint=False
                            )
                        await self._wait_unsupported(core, unsupported)
                        core = await self._observe()
                        continue
                if self.cfg.dry_run:
                    return await self._stop("dry_run_complete", core, checkpoint=False)
                if blocked is not None and not retry_allowed:
                    # Nothing changed since the last blocked end turn. Re-observe
                    # (the game may have moved on); after a few repeats clear
                    # popups and allow one more request. Never stop.
                    ledger.blocked_repeats += 1
                    if (
                        ledger.blocked_repeats
                        >= self.cfg.blocked_repeats_before_dismiss
                    ):
                        raw = await self.gs.dismiss_popup()
                        self.log.write(
                            "end_turn_blocked_repeat",
                            {
                                "turn": core.turn,
                                "blockers": _describe_block(blocked),
                                "repeats": ledger.blocked_repeats,
                                "dismissed": raw,
                            },
                        )
                        ledger.blocked_repeats = 0
                        retry_allowed = True
                    await self._sleep(self.cfg.blocked_repeat_wait_s)
                    core = await self._observe()
                    continue
                retry_allowed = False
                t0 = time.perf_counter()
                if self.spectator is not None:
                    # No camera hops or popup dismissals (InGame calls) while
                    # the engine processes the AI turn: they can hang it.
                    self.spectator.quiet(True)
                self._phase = "end_turn"
                try:
                    outcome = await self._end_turn(self.gs)
                finally:
                    self._phase = "schedule"
                    if self.spectator is not None:
                        self.spectator.quiet(False)
                elapsed = (time.perf_counter() - t0) * 1000.0
                self._game_ms += elapsed
                self._log_turn(outcome, elapsed)
                in_flight = outcome.status == "blocked" and outcome.end_turn_in_flight
                if outcome.status == "advanced":
                    self._turns += 1
                    blocked = None
                    if self.spectator is not None:
                        self.spectator.turn_advanced()
                    self._write_speed(outcome, elapsed)
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
                        await self._wait_unsupported(core, ["world_congress"])
                        core = await self._observe()
                        continue
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
                    self.log.write(
                        "end_turn_status",
                        {"turn": core.turn, "status": outcome.status},
                    )
                    core = await self._observe()
                    continue
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
                allow_exit=self.scheduler.session_exhausted(ledger, step),
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
            self._current_decision_id = decision_id
            try:
                result = await select(point, self.selector)
            except ControllerFilteringError as e:
                self.scheduler.exhaust(ledger, step)
                self.log.write(
                    "no_candidates",
                    {
                        "turn": core.turn,
                        "decision_id": decision_id,
                        "category": str(step.category),
                        "entity": step.entity,
                        "exclusions": [dataclasses.asdict(x) for x in excluded],
                        "controller_error": str(e),
                    },
                )
                core = await self._observe(
                    refresh=(frozenset({"blockers", "popup"}), core)
                )
                continue
            api_ms = sum(a.get("latency_ms") or 0.0 for a in result.attempts)
            self._api_ms += api_ms
            observe = dict(self._last_observe)
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

            self._phase = "execute"
            outcome = await self.executor.execute(
                candidate,
                point,
                current_version=self.observer.version,
                turn=core.turn,
                inputs=inputs,
            )
            self._game_ms += outcome.elapsed_ms
            self._decisions += 1
            self._turn_stats["decisions"] += 1
            self._turn_stats["api_ms"] += api_ms
            self._turn_stats["roundtrips"] += outcome.roundtrips
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
                        "observe": observe["ms"],
                        "roundtrips": observe["roundtrips"] + outcome.roundtrips,
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

    def _write_speed(self, outcome: EndTurnOutcome, end_turn_ms: float) -> None:
        now = time.perf_counter()
        st = self._turn_stats
        speed = {
            "turn": outcome.turn_before,
            "decisions": st["decisions"],
            "seconds": round(now - st["t0"], 2),
            "roundtrips": st["roundtrips"],
            "drex_seconds": round(st["api_ms"] / 1000.0, 2),
            "end_turn_seconds": round(end_turn_ms / 1000.0, 2),
            "phase_ms": dict(outcome.phase_ms),
        }
        self.log.write("speed", speed)
        if self._on_turn is not None:
            self._on_turn(speed)
        self._turn_stats = {"decisions": 0, "api_ms": 0.0, "roundtrips": 0, "t0": now}

    def _log_turn(self, outcome: EndTurnOutcome, elapsed_ms: float) -> None:
        self.log.write(
            "turn",
            {
                "status": outcome.status,
                "roundtrips": self._turn_stats["roundtrips"],
                "phase_ms": dict(outcome.phase_ms),
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
