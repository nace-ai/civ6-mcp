"""Make a Drex run watchable: close informational popups, follow the action.

The controller drives the game through Lua, never the UI, so without help the
screen shows stale "Research Completed" cards and a camera parked on the
capital. ``LiveSpectator`` reuses the MCP server's background services
(:mod:`civ_mcp.spectator`): a popup watcher that dismisses non-critical popups
and a camera controller that pans to where the last action happened. Neither
makes a game-rule decision; both are cosmetic.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, Protocol

from civ_mcp.drex.candidates import (
    ActionKind,
    AttackParams,
    Candidate,
    ImproveParams,
    MoveParams,
    ProductionParams,
    UnitOrderParams,
)
from civ_mcp.drex.observation import CoreObservation
from civ_mcp.spectator import CameraController, PopupWatcher

if TYPE_CHECKING:
    from civ_mcp.connection import GameConnection


class Spectator(Protocol):
    def start(self) -> None: ...

    async def stop(self) -> None: ...

    def focus(self, x: int, y: int, label: str = "") -> None: ...

    def turn_advanced(self) -> None: ...

    def popup_status(self, state: str) -> None: ...

    def quiet(self, on: bool) -> None: ...


class LiveSpectator:
    """Popup auto-dismiss plus camera follow on a live tuner connection.

    Nothing here polls the game: the runner reads the popup state as part of
    its own observation and hands it over through ``popup_status``.
    """

    def __init__(self, conn: GameConnection) -> None:
        self.camera = CameraController(conn, check_diplomacy=False)
        self.popups = PopupWatcher(conn, poll=False)
        self._quiet = False
        self._pending: asyncio.Task | None = None

    def start(self) -> None:
        self.camera.start()
        self.popups.start()

    async def stop(self) -> None:
        if self._pending is not None and not self._pending.done():
            self._pending.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._pending
        await self.camera.stop()
        await self.popups.stop()

    def popup_status(self, state: str) -> None:
        self.camera.set_critical(state == "CRITICAL")
        if self._quiet:
            return
        if self._pending is None or self._pending.done():
            # Dismissal is its own round trip; never block the decision loop.
            self._pending = asyncio.ensure_future(self.popups.report(state))

    def quiet(self, on: bool) -> None:
        self._quiet = on
        self.camera.quiet(on)

    def focus(self, x: int, y: int, label: str = "") -> None:
        self.camera.push(x, y, label)

    def turn_advanced(self) -> None:
        # Pending hops belong to the finished turn; the next turn's actions
        # should not wait behind them.
        self.camera.clear()


def focus_point(
    candidate: Candidate, core: CoreObservation
) -> tuple[int, int, str] | None:
    """Where a viewer would look after this action, or None for empire-wide
    decisions (research, policies, diplomacy...) that have no tile."""
    p = candidate.params
    match candidate.kind, p:
        case ActionKind.MOVE_UNIT, MoveParams():
            return p.to_x, p.to_y, candidate.label
        case ActionKind.ATTACK, AttackParams():
            return p.target_x, p.target_y, candidate.label
        case (
            ActionKind.FOUND_CITY
            | ActionKind.FORTIFY_UNIT
            | ActionKind.HEAL_UNIT
            | ActionKind.SKIP_UNIT,
            UnitOrderParams(),
        ):
            return p.unit.x, p.unit.y, candidate.label
        case ActionKind.IMPROVE_TILE, ImproveParams():
            return p.unit.x, p.unit.y, candidate.label
        case ActionKind.SET_PRODUCTION, ProductionParams():
            city = core.city(p.city_id)
            if city is None:
                return None
            return city.x, city.y, candidate.label
    return None
