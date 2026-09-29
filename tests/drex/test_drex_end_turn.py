"""End-turn: opt-in decision-only mode and the typed outcome path."""

import asyncio

from civ_mcp.end_turn import execute_end_turn, execute_end_turn_typed
from civ_mcp.game_state import GameState

TURN_QUERY = 'print(Game.GetCurrentGameTurn()); print("---END---")'
END_TURN = "UI.RequestAction(ActionTypes.ACTION_ENDTURN)"


class ScriptedConn:
    """Routes Lua by substring; records every executed snippet."""

    def __init__(self, blockers, advance_on_end_turn=False):
        self.blockers = list(blockers)
        self.advance = advance_on_end_turn
        self.executed: list[str] = []
        self.turn = 12
        self.wc_lines = ["WC_STATUS|false|10|0|1|"]
        self.handler_set = False

    def _respond(self, lua):
        self.executed.append(lua)
        if END_TURN in lua:
            if self.advance:
                self.turn += 1
            return ["OK:TURN_ENDED"]
        if TURN_QUERY in lua:
            return [str(self.turn)]
        if "GAME_ACTIVE" in lua:
            return ["GAME_ACTIVE"]
        if "LeaderResponseText" in lua:
            return ["NONE"]
        if 'print("WC_STATUS|"' in lua:
            return list(self.wc_lines)
        if "__civmcp_wc_handler" in lua:
            return ["HANDLER_SET" if self.handler_set else "NO_HANDLER"]
        if 'print("BLOCKING|"' in lua:
            if not self.blockers:
                return ["NONE"]
            return [f"BLOCKING|{b}|msg" for b in self.blockers]
        if "UI.CanEndTurn()" in lua:
            return ["CANNOT_END"]
        if "CityDestroyDirectives" in lua:
            self.blockers = [b for b in self.blockers if "CITY" not in b]
            return ["OK:KEEP|Thebes (pop 3, id:5, captured)"]
        if "WORLD_CONGRESS_LOOKED_AT_AVAILABLE" in lua:
            self.blockers = [b for b in self.blockers if "WORLD_CONGRESS" not in b]
            return ["OK"]
        return []

    async def execute_write(self, lua, timeout=5.0):
        return self._respond(lua)

    async def execute_read(self, lua, timeout=5.0):
        return self._respond(lua)

    def ran(self, fragment):
        return any(fragment in lua for lua in self.executed)


def _gs(conn, decision_only):
    gs = GameState(conn)
    gs.decision_only_end_turn = decision_only
    return gs


def test_legacy_mode_still_auto_keeps_captured_city():
    conn = ScriptedConn(
        ["ENDTURN_BLOCKING_CONSIDER_RAZE_CITY"], advance_on_end_turn=True
    )
    asyncio.run(execute_end_turn(_gs(conn, decision_only=False)))
    assert conn.ran("CityDestroyDirectives.KEEP")


def test_decision_only_mode_surfaces_captured_city_instead_of_keeping_it():
    conn = ScriptedConn(["ENDTURN_BLOCKING_CONSIDER_RAZE_CITY"])
    text = asyncio.run(execute_end_turn(_gs(conn, decision_only=True)))
    assert "Cannot end turn" in text
    assert not conn.ran("CityDestroyDirectives")
    assert not conn.ran(END_TURN)


def test_decision_only_mode_does_not_clear_stored_promotions():
    conn = ScriptedConn(["ENDTURN_BLOCKING_UNIT_PROMOTION"])
    asyncio.run(execute_end_turn(_gs(conn, decision_only=True)))
    assert not conn.ran("ChangeStoredPromotions")


def test_decision_only_mode_does_not_pass_on_world_congress_or_government():
    conn = ScriptedConn(
        [
            "ENDTURN_BLOCKING_WORLD_CONGRESS_SPECIAL_SESSION",
            "ENDTURN_BLOCKING_CONSIDER_GOVERNMENT_CHANGE",
            "ENDTURN_BLOCKING_SPY_CHOOSE_ESCAPE_ROUTE",
        ]
    )
    asyncio.run(execute_end_turn(_gs(conn, decision_only=True)))
    assert not conn.ran("WORLD_CONGRESS_LOOKED_AT_AVAILABLE")
    assert not conn.ran("SetGovernmentChangeConsidered")
    assert not conn.ran(END_TURN)


def test_informational_world_congress_look_is_retained_and_logged():
    conn = ScriptedConn(
        ["ENDTURN_BLOCKING_WORLD_CONGRESS_LOOK"], advance_on_end_turn=True
    )
    gs = _gs(conn, decision_only=True)
    outcome = asyncio.run(execute_end_turn_typed(gs))
    assert conn.ran("WORLD_CONGRESS_LOOKED_AT_AVAILABLE")
    assert any(
        h["action"] == "world_congress_look_dismissed" for h in outcome.housekeeping
    )


def test_typed_outcome_reports_blockers_without_prose():
    conn = ScriptedConn(["ENDTURN_BLOCKING_CONSIDER_DISLOYAL_CITY"])
    outcome = asyncio.run(
        execute_end_turn_typed(_gs(conn, decision_only=False), decision_only=True)
    )
    assert outcome.status == "blocked"
    assert outcome.turn_before == outcome.turn_after == 12
    assert [b[0] for b in outcome.blockers] == [
        "ENDTURN_BLOCKING_CONSIDER_DISLOYAL_CITY"
    ]
    assert outcome.end_turn_in_flight is False


def test_typed_outcome_reports_advance():
    conn = ScriptedConn([], advance_on_end_turn=True)
    outcome = asyncio.run(execute_end_turn_typed(_gs(conn, decision_only=False)))
    assert outcome.status == "advanced"
    assert (outcome.turn_before, outcome.turn_after) == (12, 13)
    assert sum(END_TURN in lua for lua in conn.executed) == 1


def test_typed_outcome_restores_previous_mode():
    conn = ScriptedConn([], advance_on_end_turn=True)
    gs = _gs(conn, decision_only=False)
    asyncio.run(execute_end_turn_typed(gs, decision_only=True))
    assert gs.decision_only_end_turn is False


WC_FIRES = [
    "WC_STATUS|false|0|10|1|",
    "WC_RES|123|RESOLUTION_X|Some Resolution|PLAYER|A effect|B effect|0|-1|none|",
]


def test_decision_only_congress_gate_ignores_a_stale_vote_handler():
    conn = ScriptedConn([], advance_on_end_turn=True)
    conn.wc_lines, conn.handler_set = WC_FIRES, True
    text = asyncio.run(execute_end_turn(_gs(conn, decision_only=True)))
    assert "World Congress" in text
    assert not conn.ran(END_TURN)


def test_legacy_congress_gate_still_honours_a_registered_handler():
    conn = ScriptedConn([], advance_on_end_turn=True)
    conn.wc_lines, conn.handler_set = WC_FIRES, True
    asyncio.run(execute_end_turn(_gs(conn, decision_only=False)))
    assert conn.ran(END_TURN)


def test_typed_outcome_marks_a_war_declaration_interruption(monkeypatch):
    import civ_mcp.end_turn as et

    async def interrupted(gs):
        et._housekeeping(gs, "war_declaration_dismissed", "Egypt")
        return "WAR DECLARED by Egypt! Session dismissed."

    monkeypatch.setattr(et, "execute_end_turn", interrupted)
    outcome = asyncio.run(
        et.execute_end_turn_typed(_gs(ScriptedConn([]), decision_only=True))
    )
    assert outcome.status == "interrupted"


def test_request_in_flight_is_polled_not_resent():
    conn = ScriptedConn([])
    reads = {"n": 0}
    original = conn._respond

    def respond(lua):
        if TURN_QUERY in lua:
            conn.executed.append(lua)
            reads["n"] += 1
            return ["13" if reads["n"] > 2 else "12"]
        return original(lua)

    conn._respond = respond
    gs = _gs(conn, decision_only=True)
    gs._pending_end_turn, gs._pending_end_turn_from = True, 12
    outcome = asyncio.run(execute_end_turn_typed(gs))
    assert outcome.status == "advanced" and outcome.turn_before == 12
    assert not conn.ran(END_TURN)


def _instant_sleep(monkeypatch):
    import civ_mcp.end_turn as et

    async def instant(_delay):
        return None

    monkeypatch.setattr(et.asyncio, "sleep", instant)


def test_decision_only_timeout_never_resends_the_end_turn_request(monkeypatch):
    _instant_sleep(monkeypatch)
    conn = ScriptedConn([])
    gs = _gs(conn, decision_only=True)

    async def dismissed():
        return "Dismissed: InGamePopup"

    gs.dismiss_popup = dismissed
    asyncio.run(execute_end_turn(gs))
    assert sum(END_TURN in lua for lua in conn.executed) == 1


def test_legacy_timeout_path_is_unchanged(monkeypatch):
    _instant_sleep(monkeypatch)
    conn = ScriptedConn([])
    gs = _gs(conn, decision_only=False)

    async def dismissed():
        return "Dismissed: InGamePopup"

    gs.dismiss_popup = dismissed
    asyncio.run(execute_end_turn(gs))
    assert sum(END_TURN in lua for lua in conn.executed) == 2


class _ProbeGame:
    def __init__(self, decision_only):
        import drex_fixtures as fx

        self.decision_only_end_turn = decision_only
        self.end_turn_housekeeping = []
        self._pending_end_turn = True
        self._pending_end_turn_from = 12
        self.conn = ScriptedConn([])
        self._sessions = [
            fx.session(is_at_war=True, dialogue_text="Let us make peace.")
        ]
        self._deals = [fx.deal()]

    async def get_diplomacy_sessions(self):
        return list(self._sessions)

    async def get_pending_deals(self):
        return list(self._deals)


def test_decision_only_mid_turn_probe_surfaces_an_at_war_offer(monkeypatch):
    from civ_mcp.end_turn import _check_mid_turn_diplomacy

    _instant_sleep(monkeypatch)
    game = _ProbeGame(decision_only=True)
    message, advanced = asyncio.run(_check_mid_turn_diplomacy(game, END_TURN, 12))
    assert message is not None and "proposal" in message
    assert not game.conn.ran("CloseSession")


def test_legacy_mid_turn_probe_still_dismisses_at_war_sessions(monkeypatch):
    from civ_mcp.end_turn import _check_mid_turn_diplomacy

    _instant_sleep(monkeypatch)
    game = _ProbeGame(decision_only=False)
    asyncio.run(_check_mid_turn_diplomacy(game, END_TURN, 12))
    assert game.conn.ran("CloseSession")


# ------------------------------------------------- phase timing, trims (C7)
def test_typed_outcome_reports_phase_timings():
    conn = ScriptedConn([], advance_on_end_turn=True)
    outcome = asyncio.run(execute_end_turn_typed(_gs(conn, decision_only=True)))
    assert outcome.status == "advanced"
    assert {"pre_checks", "pre_dismiss", "request", "poll", "post"} <= set(
        outcome.phase_ms
    )
    assert all(v >= 0.0 for v in outcome.phase_ms.values())


def _record(name, sink):
    async def fake(*args, **kwargs):
        sink.append(name)
        return ([], 0) if name == "warnings" else []

    return fake


def test_decision_only_mode_skips_narration_only_checks(monkeypatch):
    from civ_mcp import end_turn as end_turn_mod

    called = []
    monkeypatch.setattr(
        end_turn_mod, "_check_victory_proximity", _record("victory", called)
    )
    monkeypatch.setattr(
        end_turn_mod, "_check_empire_warnings", _record("warnings", called)
    )
    conn = ScriptedConn([], advance_on_end_turn=True)
    asyncio.run(execute_end_turn_typed(_gs(conn, decision_only=True)))
    assert called == []


def test_legacy_mode_keeps_narration_checks(monkeypatch):
    from civ_mcp import end_turn as end_turn_mod

    called = []
    monkeypatch.setattr(
        end_turn_mod, "_check_victory_proximity", _record("victory", called)
    )
    monkeypatch.setattr(
        end_turn_mod, "_check_empire_warnings", _record("warnings", called)
    )
    conn = ScriptedConn([], advance_on_end_turn=True)
    asyncio.run(execute_end_turn(_gs(conn, decision_only=False)))
    assert "victory" in called and "warnings" in called


def test_recovery_poll_sleep_is_short_only_in_decision_only_mode():
    from civ_mcp.end_turn import _poll_sleep_s

    conn = ScriptedConn([])
    assert _poll_sleep_s(_gs(conn, decision_only=True)) == 0.5
    assert _poll_sleep_s(_gs(conn, decision_only=False)) == 2.0
