"""Spectator-mode background services for video recording.

Two asyncio tasks that run alongside the agent and share the GameConnection:

CameraController — hops the in-game camera to key locations as the agent acts.
  Tools push (x, y) events; the controller replays them at 1-second intervals.
  Pauses automatically when a diplomacy screen is active.

PopupWatcher — polls for non-critical popups and auto-dismisses them after 1 second,
  keeping the view uncluttered. Skips while diplomacy screens are showing.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from civ_mcp.connection import GameConnection

log = logging.getLogger(__name__)

SENTINEL = "---END---"

# How long (seconds) the camera dwells at each location before the next hop.
CAMERA_DWELL = 1.0

# Maximum queued camera events — oldest dropped when full.
CAMERA_QUEUE_MAX = 6

# How often (seconds) the popup watcher polls for visible non-critical popups.
POPUP_POLL_INTERVAL = 0.5

# How long (seconds) a popup must be visible before it is auto-dismissed.
POPUP_DISMISS_DELAY = 1.0

# Non-critical popups that will be auto-dismissed.
_NONCRITICAL_POPUPS = [
    "InGamePopup",
    "GenericPopup",
    "PopupDialog",
    "TechCivicCompletedPopup",
    "BoostUnlockedPopup",
    "GreatWorkShowcase",
    "NaturalWonderPopup",
    "WonderBuiltPopup",
    "EraCompletePopup",
    "HistoricMoments",
    "MomentPopup",
    "ProjectBuiltPopup",
    "RockBandPopup",
    "RockBandMoviePopup",
    "NaturalDisasterPopup",
]

# Critical screens — pause both camera and popup watcher while visible.
_CRITICAL_SCREENS = [
    "DiplomacyActionView",
    "DiplomacyDealView",
]

# Lua snippet that returns CLEAR / POPUP / CRITICAL in one roundtrip.
POPUP_STATUS_LUA = (
    "local r='CLEAR' "
    + "".join(
        f"do local c=ContextPtr:LookUpControl('/InGame/{n}') "
        f"if c and not c:IsHidden() then r='CRITICAL' end end "
        for n in _CRITICAL_SCREENS
    )
    + "if r=='CLEAR' then "
    + "".join(
        f"do local c=ContextPtr:LookUpControl('/InGame/{n}') "
        f"if c and not c:IsHidden() then r='POPUP' end end "
        for n in _NONCRITICAL_POPUPS
    )
    + "end "
    + f"print(r) print('{SENTINEL}')"
)
_POPUP_POLL_LUA = POPUP_STATUS_LUA

# Lua snippet to check for active diplomacy screens (used by camera).
_DIPLOMACY_CHECK_LUA = (
    "local active=false "
    + "".join(
        f"do local c=ContextPtr:LookUpControl('/InGame/{n}') "
        f"if c and not c:IsHidden() then active=true end end "
        for n in _CRITICAL_SCREENS
    )
    + f"print(active and 'YES' or 'NO') print('{SENTINEL}')"
)


@dataclass
class CameraEvent:
    x: int
    y: int
    label: str = ""


class CameraController:
    """Hops the game camera to locations pushed by tool handlers.

    Call push(x, y) from any tool. The controller dequeues events in the
    background, fires UI.LookAtPlot, and waits CAMERA_DWELL seconds before
    the next hop. Pauses automatically during active diplomacy screens.
    """

    def __init__(self, conn: GameConnection, *, check_diplomacy: bool = True) -> None:
        self._conn = conn
        self._queue: asyncio.Queue[CameraEvent] = asyncio.Queue(
            maxsize=CAMERA_QUEUE_MAX
        )
        self._task: asyncio.Task | None = None
        # With check_diplomacy=False the owner reports screen state through
        # set_critical() instead of the controller spending a round trip per hop.
        self._check_diplomacy = check_diplomacy
        self._critical = False
        self._quiet = False

    def set_critical(self, flag: bool) -> None:
        """A diplomacy screen is up (True) or gone (False); hops hold while up."""
        self._critical = flag

    def quiet(self, on: bool) -> None:
        """Hold all hops (e.g. while the engine processes the AI turn)."""
        self._quiet = on

    def push(self, x: int, y: int, label: str = "") -> None:
        """Push a camera event. Drops the oldest event if the queue is full."""
        event = CameraEvent(x, y, label)
        if self._queue.full():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            pass

    def clear(self) -> None:
        """Drain all pending events (call when a turn advances)."""
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="camera-controller")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _is_diplomacy_active(self) -> bool:
        try:
            lines = await self._conn.execute_write(_DIPLOMACY_CHECK_LUA, timeout=2.0)
            return any(line.strip() == "YES" for line in lines)
        except Exception:
            return False

    async def _look_at(self, x: int, y: int) -> None:
        lua = (
            f"local p=Map.GetPlot({x},{y}) "
            f"if p then pcall(function() UI.LookAtPlot(p) end) end "
            f"print('{SENTINEL}')"
        )
        try:
            await self._conn.execute_write(lua, timeout=2.0)
        except Exception:
            pass

    async def _run(self) -> None:
        while True:
            event = await self._queue.get()
            # Hold until diplomacy screen closes / the owner lifts the hold.
            while True:
                if self._quiet or self._critical:
                    await asyncio.sleep(0.1)
                    continue
                if not self._check_diplomacy:
                    break
                try:
                    if not await self._is_diplomacy_active():
                        break
                except Exception:
                    break
                await asyncio.sleep(0.5)
            await self._look_at(event.x, event.y)
            await asyncio.sleep(CAMERA_DWELL)


class PopupWatcher:
    """Auto-dismisses non-critical popups after POPUP_DISMISS_DELAY seconds.

    Polls the InGame UI every POPUP_POLL_INTERVAL seconds. Pauses completely
    while diplomacy screens are active (CRITICAL status).
    """

    def __init__(self, conn: GameConnection, *, poll: bool = True) -> None:
        self._conn = conn
        self._task: asyncio.Task | None = None
        # poll=False: the owner already reads the popup state in its own
        # round trip and calls report(); nothing is polled here.
        self._poll_enabled = poll
        self._first_seen: float | None = None

    def start(self) -> None:
        if self._poll_enabled:
            self._task = asyncio.create_task(self._run(), name="popup-watcher")

    async def report(self, status: str, now: float | None = None) -> None:
        """Apply the dismissal rule to an externally observed status."""
        from civ_mcp.game_lifecycle import dismiss_popup

        if now is None:
            now = asyncio.get_running_loop().time()
        if status != "POPUP":
            # CRITICAL or CLEAR — reset timer
            self._first_seen = None
            return
        if self._first_seen is None:
            self._first_seen = now
        elif now - self._first_seen >= POPUP_DISMISS_DELAY:
            log.debug(
                "PopupWatcher: dismissing popup after %.1fs", now - self._first_seen
            )
            self._first_seen = None
            await dismiss_popup(self._conn)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    @staticmethod
    async def _dismiss_crash_dialogs() -> None:
        """Check for and dismiss Win32 crash reporter dialogs."""
        from civ_mcp.game_launcher import dismiss_crash_dialogs

        dismissed = await dismiss_crash_dialogs()
        if dismissed:
            log.warning("PopupWatcher: dismissed crash dialog: %s", dismissed)

    async def _poll(self) -> str:
        """Returns 'POPUP', 'CRITICAL', or 'CLEAR'."""
        try:
            lines = await self._conn.execute_write(_POPUP_POLL_LUA, timeout=2.0)
            for line in lines:
                s = line.strip()
                if s in ("POPUP", "CRITICAL", "CLEAR"):
                    return s
        except Exception:
            pass
        return "CLEAR"

    async def _run(self) -> None:
        # Check for Win32 crash dialogs every N iterations (not every 0.5s)
        _crash_check_interval = 6  # every ~3 seconds
        _iteration = 0

        while True:
            await asyncio.sleep(POPUP_POLL_INTERVAL)
            _iteration += 1
            try:
                await self.report(await self._poll())
            except Exception:
                self._first_seen = None

            # Periodically check for Win32 crash reporter dialogs.
            # These are OS-level dialogs outside the game's Lua layer.
            if _iteration % _crash_check_interval == 0:
                await self._dismiss_crash_dialogs()
