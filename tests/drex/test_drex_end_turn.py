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
            return ["WC_STATUS|false|10|0|1|"]
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
