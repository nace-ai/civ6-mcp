"""Typed GameState wrappers used by the decision-only controller."""

import asyncio

from civ_mcp.game_state import GameState


class _RecordingConn:
    def __init__(self, lines):
        self.lines = lines
        self.calls = []

    async def execute_write(self, lua, timeout=5.0):
        self.calls.append(("write", lua))
        return self.lines

    async def execute_read(self, lua, timeout=5.0):
        self.calls.append(("read", lua))
        return self.lines


def _gs(lines):
    gs = GameState.__new__(GameState)
    gs.conn = _RecordingConn(lines)
    return gs


def _run(coro):
    return asyncio.run(coro)


def test_unit_action_space_runs_in_ingame_context():
    gs = _gs(["UNIT|131073|1|UNIT_WARRIOR|10,12|2|0|0|1|0|0|0|100/100", "---END---"])
    space = _run(gs.get_unit_action_space(1))
    assert space.unit_id == 131073
    assert gs.conn.calls[0][0] == "write"


def test_progress_types_and_eligibility_read_gamecore():
    gs = _gs(["PROGRESS|NONE|CIVIC_CODE_OF_LAWS"])
    assert _run(gs.get_progress_types()).civic_type == "CIVIC_CODE_OF_LAWS"
    gs = _gs(["ELIGIBLE|1|engine"])
    assert _run(gs.check_eligibility("tech", "TECHNOLOGY_POTTERY")) == (True, "engine")
    assert gs.conn.calls[0][0] == "read"


def test_unit_state_and_wonder_types():
    assert _run(_gs(["STATE|1|2|0|0|100|0"]).get_unit_state(4)).moves_remaining == 0
    assert _run(_gs(["WONDER|BUILDING_PYRAMIDS"]).get_wonder_types()) == {
        "BUILDING_PYRAMIDS"
    }


def test_available_governments_and_keep_current():
    gs = _gs(["GOV|GOVERNMENT_CHIEFDOM|1|AVAILABLE|Chiefdom|SLOT_MILITARY|"])
    assert (
        _run(gs.get_available_governments())[0].government_type == "GOVERNMENT_CHIEFDOM"
    )
    gs = _gs(["OK:GOVERNMENT_CHANGE_CONSIDERED"])
    assert _run(gs.keep_current_government()) == "GOVERNMENT_CHANGE_CONSIDERED"


def test_end_turn_blockers_are_typed_pairs():
    gs = _gs(["BLOCKING|ENDTURN_BLOCKING_RESEARCH|Choose research", "---END---"])
    assert _run(gs.get_end_turn_blockers()) == [
        ("ENDTURN_BLOCKING_RESEARCH", "Choose research")
    ]


def test_verify_production_and_city_exists():
    assert (
        _run(_gs(["CONFIRMED|8 turns"]).verify_production(65536, "UNIT_WARRIOR"))
        is True
    )
    assert (
        _run(_gs(["NOT_SET|current=nil|expected=X"]).verify_production(65536, "X"))
        is False
    )
    assert _run(_gs(["OK:CITY_EXISTS"]).city_exists_at(3, 4)) is True
    assert _run(_gs(["OK:NO_CITY"]).city_exists_at(3, 4)) is False
