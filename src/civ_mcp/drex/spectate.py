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
import time
from typing import TYPE_CHECKING, Protocol

from civ_mcp.drex.candidates import (
    ActionKind,
    ArtifactParams,
    AttackParams,
    Candidate,
    CapturedCityParams,
    EscapeRouteParams,
    ImproveParams,
    MoveParams,
    ProductionParams,
    TradeRouteParams,
    UnitOrderParams,
)
from civ_mcp.drex.hexgrid import hex_distance
from civ_mcp.drex.observation import CoreObservation
from civ_mcp.spectator import CameraController, PopupWatcher

__all__ = [
    "CameraPacer",
    "LiveSpectator",
    "Spectator",
    "focus_point",
    "hex_distance",
]

if TYPE_CHECKING:
    from civ_mcp.connection import GameConnection


class Spectator(Protocol):
    def start(self) -> None: ...

    async def stop(self) -> None: ...

    def focus(self, x: int, y: int, label: str = "") -> None: ...

    def turn_advanced(self) -> None: ...

    def popup_status(self, state: str) -> None: ...

    def quiet(self, on: bool) -> None: ...


class CameraPacer:
    """Pan like a person watching: only when the action leaves the view
    (more than ``min_tiles`` from the last focus) or after ``min_seconds`` of
    looking at the same area. Every dispatched action is a candidate hop, so
    without this the camera re-centres on each one-tile move and skip."""

    def __init__(self, *, min_tiles: int = 4, min_seconds: float = 3.0) -> None:
        self.min_tiles = min_tiles
        self.min_seconds = min_seconds
        self._last: tuple[int, int, float] | None = None

    def should_hop(self, x: int, y: int, *, now: float | None = None) -> bool:
        if now is None:
            now = time.monotonic()
        if self._last is None:
            self._last = (x, y, now)
            return True
        lx, ly, lt = self._last
        if hex_distance(x, y, lx, ly) > self.min_tiles or now - lt >= self.min_seconds:
            self._last = (x, y, now)
            return True
        return False


class LiveSpectator:
    """Popup auto-dismiss plus camera follow on a live tuner connection.

    Nothing here polls the game: the runner reads the popup state as part of
    its own observation and hands it over through ``popup_status``.
    """

    def __init__(self, conn: GameConnection) -> None:
        # One pending hop at most: when the loop runs faster than the dwell,
        # the camera jumps to the latest action instead of replaying old ones.
        self.camera = CameraController(conn, check_diplomacy=False, queue_max=1)
        self.popups = PopupWatcher(conn, poll=False)
        self.pacer = CameraPacer()
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
        if on and self._pending is not None and not self._pending.done():
            # No UI Lua while the engine processes the AI turn.
            self._pending.cancel()
            self._pending = None

    def focus(self, x: int, y: int, label: str = "") -> None:
        if self.pacer.should_hop(x, y):
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
        case ActionKind.MAKE_TRADE_ROUTE, TradeRouteParams():
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
        case ActionKind.RESOLVE_CAPTURED_CITY, CapturedCityParams():
            city = core.city(p.city_id)
            if city is None:
                return None
            return city.x, city.y, candidate.label
        case ActionKind.CHOOSE_ESCAPE_ROUTE, EscapeRouteParams():
            unit = core.unit(p.spy_unit_id)
            if unit is None:
                return None
            return unit.x, unit.y, candidate.label
        case ActionKind.CHOOSE_ARTIFACT_PLAYER, ArtifactParams():
            unit = core.unit(p.archaeologist_unit_id)
            if unit is None:
                return None
            return unit.x, unit.y, candidate.label
    return None
