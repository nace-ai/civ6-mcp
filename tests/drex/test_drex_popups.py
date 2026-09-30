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
