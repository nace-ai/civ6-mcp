"""Leader dialogues: answer like the choice button, leave like the Goodbye button.

A forced DiplomacyManager.CloseSession while the DiplomacyActionView was
still presenting the leader's reply left the view holding its engine event
(UI.ReferenceCurrentEvent) and froze the game at PLEASE WAIT (T58, 2026-09-30).
"""

from __future__ import annotations

import asyncio

from civ_mcp.game_state import GameState


def _session_line(pid: int, text: str) -> str:
    return f"SESSION|1|{pid}|Greece|Gorgo|{text}|||0"


class ScriptedConn:
    """Fake tuner: a diplomacy session with one leader whose reply may arrive
    only after a few polls, and a view whose exit button closes the session."""

    def __init__(self, reply_after_polls: int, reply_text: str | None):
        self.lua_states = {80: "DiplomacyActionView", 95: "InGame"}
        self.open = True
        self.polls = 0
        self.reply_after_polls = reply_after_polls
        self.reply_text = reply_text
        self.text = "I am Gorgo. Who are you?"
        self.view_visible = True
        self.calls: list[str] = []

    async def execute_write(self, lua: str, timeout: float = 5.0) -> list[str]:
        if 'if "EXIT" == "EXIT"' in lua:  # build_diplomacy_respond(EXIT)
            self.calls.append("raw_close")
            self.open = False
            return ["OK:SESSION_CLOSED"]
        if "DiplomacyManager.AddResponse" in lua:
            self.calls.append("add_response")
            return ["OK:RESPONSE_SENT|POSITIVE"]
        if 'print("SESSION_OPEN|"' in lua:
            self.polls += 1
            if self.open and self.reply_text and self.polls >= self.reply_after_polls:
                self.text = self.reply_text
            return ["SESSION_OPEN|1"] if self.open else ["SESSION_CLOSED"]
        if "FindOpenSessionID(me, i)" in lua:  # session listing
            return [_session_line(3, self.text)] if self.open else ["NONE"]
        return []

    async def execute_in_state(
        self, idx: int, lua: str, timeout: float = 5.0
    ) -> list[str]:
        assert self.lua_states[idx] == "DiplomacyActionView"
        if "CHOICE_EXIT" in lua:
            self.calls.append("view_exit")
            if not self.view_visible:
                return ["VIEW_HIDDEN"]
            self.open = False
            self.view_visible = False
            return ["VIEW_EXIT"]
        if "UninitializeView" in lua:
            self.calls.append("view_release")
            if not self.view_visible:
                return ["VIEW_HIDDEN"]
            self.view_visible = False
            return ["VIEW_RELEASED"]
        return []


def _gs(conn: ScriptedConn) -> GameState:
    gs = GameState(conn)  # type: ignore[arg-type]
    gs.RESPONSE_SETTLE_S = 0.001
    return gs


def test_waits_for_the_leaders_reply_instead_of_forcing_a_close():
    conn = ScriptedConn(
        reply_after_polls=3, reply_text="We have a city nearby. Visit us?"
    )
    result = asyncio.run(_gs(conn).diplomacy_respond(3, "POSITIVE"))

    assert "SESSION_CONTINUES" in result
    assert "We have a city nearby" in result
    assert conn.calls == ["add_response"]  # no close of any kind
    assert conn.open and conn.view_visible


def test_goodbye_phase_leaves_through_the_view_not_close_session():
    conn = ScriptedConn(
        reply_after_polls=99, reply_text=None
    )  # leader has nothing more to say
    result = asyncio.run(_gs(conn).diplomacy_respond(3, "POSITIVE"))

    assert "SESSION_CLOSED" in result
    assert conn.calls == ["add_response", "view_exit", "view_release"]
    assert "raw_close" not in conn.calls
    assert not conn.open and not conn.view_visible


def test_engine_closing_the_session_still_releases_a_lingering_view():
    conn = ScriptedConn(reply_after_polls=99, reply_text=None)

    async def run():
        gs = _gs(conn)
        # the engine closes the session right after our answer, but the view stays up
        orig = conn.execute_write

        async def write(lua, timeout=5.0):
            out = await orig(lua, timeout)
            if "DiplomacyManager.AddResponse" in lua:
                conn.open = False
            return out

        conn.execute_write = write  # type: ignore[method-assign]
        return await gs.diplomacy_respond(3, "NEGATIVE")

    result = asyncio.run(run())
    assert result == "OK:RESPONDED|NEGATIVE|SESSION_CLOSED"
    assert conn.calls == ["add_response", "view_release"]
    assert not conn.view_visible


def test_exit_uses_the_view_first_and_raw_close_only_as_fallback():
    conn = ScriptedConn(reply_after_polls=99, reply_text=None)
    conn.view_visible = False  # nothing on screen to press: fall back to CloseSession
    result = asyncio.run(_gs(conn).diplomacy_respond(3, "EXIT"))

    assert result.startswith("OK:RESPONDED|EXIT|SESSION_CLOSED")
    assert conn.calls == ["view_exit", "raw_close", "view_release"]
    assert not conn.open
