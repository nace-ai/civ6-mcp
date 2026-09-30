"""Popups a human would click away: the tutorial advisor is one of them."""

import asyncio


def test_advisor_popup_is_a_noncritical_popup_in_the_status_lua():
    from civ_mcp.spectator import _NONCRITICAL_POPUPS, POPUP_STATUS_LUA

    assert "TutorialUIRoot/AdvisorPopup" in _NONCRITICAL_POPUPS
    assert "/InGame/TutorialUIRoot/AdvisorPopup" in POPUP_STATUS_LUA
    # a root-level context path is used verbatim, not prefixed with /InGame
    assert "/TutorialUIRoot/AdvisorPopup" in POPUP_STATUS_LUA
    assert "/InGame//TutorialUIRoot" not in POPUP_STATUS_LUA


def test_dismiss_popup_closes_the_advisor_popup():
    from civ_mcp.game_lifecycle import dismiss_popup

    class Conn:
        def __init__(self):
            self.lua = []

        async def execute_write(self, lua, timeout=5.0):
            self.lua.append(lua)
            return []

        async def execute_read(self, lua, timeout=5.0):
            self.lua.append(lua)
            return []

    conn = Conn()
    asyncio.run(dismiss_popup(conn))
    joined = "\n".join(conn.lua)
    assert "/InGame/TutorialUIRoot/AdvisorPopup" in joined
    assert "/TutorialUIRoot/AdvisorPopup" in joined
    assert "/InGame//TutorialUIRoot" not in joined


class _StateConn:
    """Connection fake: InGame reports which views are visible; per-state
    calls are recorded so the test can see the close path that was used."""

    def __init__(self, ingame_lines, lua_states):
        self.ingame_lines = ingame_lines
        self.lua_states = lua_states
        self.ingame_lua = []
        self.state_calls = []

    async def execute_write(self, lua, timeout=5.0):
        self.ingame_lua.append(lua)
        return list(self.ingame_lines) if len(self.ingame_lua) == 1 else []

    async def execute_read(self, lua, timeout=5.0):
        return []

    async def execute_in_state(self, idx, lua, timeout=5.0):
        self.state_calls.append((self.lua_states[idx], lua))
        return ["CLOSED|" + self.lua_states[idx]]


def test_advisor_popup_is_closed_in_its_own_state_not_hidden():
    """AdvisorPopup holds UI.ReferenceCurrentEvent until its Close() runs;
    SetHide from InGame froze the game at PLEASE WAIT (T28, 2026-09-30)."""
    from civ_mcp.game_lifecycle import dismiss_popup

    conn = _StateConn(
        ["ADVISOR|TutorialUIRoot/AdvisorPopup"], {95: "InGame", 96: "AdvisorPopup"}
    )
    result = asyncio.run(dismiss_popup(conn))

    assert [(s, "pcall(Close)" in lua) for s, lua in conn.state_calls] == [
        ("AdvisorPopup", True)
    ]
    assert "AdvisorPopup" in result
    joined = "\n".join(conn.ingame_lua)
    # detection only from InGame: never SetHide the advisor there
    advisor_block = joined.split("AdvisorPopup")[1]
    assert "SetHide" not in advisor_block.split("end end")[0]


def test_orphan_leader_scene_is_released_via_diplomacy_view_uninitialize():
    """DiplomacyActionView locks the engine in InitializeView and releases it
    only in UninitializeView; hiding LeaderScene froze T32 and T50."""
    from civ_mcp.game_lifecycle import dismiss_popup

    conn = _StateConn(["LEADERSCENE"], {95: "InGame", 80: "DiplomacyActionView"})
    result = asyncio.run(dismiss_popup(conn))

    assert conn.state_calls and conn.state_calls[0][0] == "DiplomacyActionView"
    assert "pcall(UninitializeView)" in conn.state_calls[0][1]
    assert "LeaderScene" in result
    assert (
        'LookUpControl("/InGame/LeaderScene") if ls and not ls:IsHidden() then print("LEADERSCENE")'
        in "\n".join(conn.ingame_lua)
    )


def test_leader_scene_with_open_session_is_left_for_respond_to_diplomacy():
    from civ_mcp.game_lifecycle import dismiss_popup

    conn = _StateConn(
        ["LEADERSCENE", "PENDING|DiplomacyActionView"],
        {95: "InGame", 80: "DiplomacyActionView"},
    )
    result = asyncio.run(dismiss_popup(conn))

    assert conn.state_calls == []
    assert "LeaderScene" not in result
    assert "diplomacy session active" in result.lower()
