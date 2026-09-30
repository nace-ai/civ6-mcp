"""Drex decides leader dialogues on the buttons a human sees.

The leader screen builds its choice buttons per statement; the runner reads
them (key + text) from the DiplomacyActionView Lua state, offers exactly those
as candidates, and answers by pressing the chosen button the way a click does.
"""

from __future__ import annotations

import asyncio

import drex_fixtures as fx

from civ_mcp import lua as lq
from civ_mcp.drex.candidates import DecisionCategory
from civ_mcp.drex.enumerate import diplomacy_candidates
from civ_mcp.drex.observation import (
    DecisionInputs,
    DecisionMemory,
    DecisionSpec,
    build_context,
)
from civ_mcp.game_state import GameState
from test_drex_observation import _core

GORGO = [
    lq.DiplomacyChoice("CHOICE_POSITIVE", "We would love to sample your hospitality."),
    lq.DiplomacyChoice("CHOICE_NEGATIVE", "Sorry but not at this time."),
]


def _ids(cands):
    return sorted(c.candidate_id for c in cands)


def test_view_choices_parse_skips_disabled_and_blank_buttons():
    lines = [
        "LEADER|We have a city nearby.",
        "CHOICE|CHOICE_POSITIVE|We would love to sample your hospitality.|0",
        "CHOICE|CHOICE_NEGATIVE|Sorry but not at this time.|0",
        "CHOICE|CHOICE_MAKE_DEAL|Let us make a deal.|1",
        "CHOICE|||0",
    ]
    choices = lq.parse_diplomacy_view_choices(lines)
    assert [(c.key, c.text) for c in choices] == [
        ("CHOICE_POSITIVE", "We would love to sample your hospitality."),
        ("CHOICE_NEGATIVE", "Sorry but not at this time."),
    ]
    assert lq.parse_diplomacy_view_choices(["VIEW_HIDDEN"]) == []


def test_candidates_are_the_real_buttons_when_the_screen_is_known():
    cands = diplomacy_candidates(fx.session(choices=GORGO))
    assert _ids(cands) == ["diplomacy:1:CHOICE_NEGATIVE", "diplomacy:1:CHOICE_POSITIVE"]
    assert {c.label for c in cands} == {
        "We would love to sample your hospitality.",
        "Sorry but not at this time.",
    }
    assert all(c.params.response.startswith("CHOICE_") for c in cands)


def test_goodbye_only_screen_is_not_a_decision_unless_exit_is_allowed():
    goodbye = [lq.DiplomacyChoice("CHOICE_EXIT", "Goodbye.")]
    assert diplomacy_candidates(fx.session(choices=goodbye)) == []
    with_exit = diplomacy_candidates(fx.session(choices=goodbye), allow_exit=True)
    assert [c.params.response for c in with_exit] == ["CHOICE_EXIT"]
    assert with_exit[0].label == "Goodbye."


def test_exit_is_added_once_when_allowed_and_not_on_screen():
    cands = diplomacy_candidates(fx.session(choices=GORGO), allow_exit=True)
    exits = [c for c in cands if c.params.response == "EXIT"]
    assert len(exits) == 1 and exits[0].label == "Close the screen"


def test_buttons_without_keys_fall_back_to_the_generic_responses():
    unkeyed = [lq.DiplomacyChoice("", "We would love to sample your hospitality.")]
    cands = diplomacy_candidates(fx.session(choices=unkeyed))
    assert _ids(cands) == ["diplomacy:1:NEGATIVE", "diplomacy:1:POSITIVE"]


def test_drex_sees_the_button_texts_in_the_subject():
    spec = DecisionSpec(DecisionCategory.DIPLOMACY, "player:1")
    ctx = build_context(
        spec,
        _core(),
        DecisionInputs(session=fx.session(choices=GORGO)),
        DecisionMemory(),
        objective="Expand.",
    )
    assert ctx["subject"]["visible_buttons"] == [
        "We would love to sample your hospitality.",
        "Sorry but not at this time.",
    ]


class _ViewConn:
    """Tuner fake: a visible leader screen whose buttons work, and a session
    the engine closes once the positive button has been pressed."""

    def __init__(self, view_visible=True):
        self.lua_states = {80: "DiplomacyActionView", 95: "InGame"}
        self.view_visible = view_visible
        self.open = True
        self.calls: list[str] = []

    async def execute_write(self, lua, timeout=5.0):
        if 'if "EXIT" == "EXIT"' in lua:
            self.calls.append("raw_close")
            self.open = False
            return ["OK:SESSION_CLOSED"]
        if "DiplomacyManager.AddResponse" in lua:
            self.calls.append(
                "add_response:" + lua.split('AddResponse(sid, me, "')[1].split('"')[0]
            )
            self.open = False
            return ["OK:RESPONSE_SENT|X"]
        if 'print("SESSION_OPEN|"' in lua:
            return ["SESSION_OPEN|1"] if self.open else ["SESSION_CLOSED"]
        if "FindOpenSessionID(me, i)" in lua:
            return (
                ["SESSION|1|3|Greece|Gorgo|Who are you?|||0"] if self.open else ["NONE"]
            )
        return []

    async def execute_in_state(self, idx, lua, timeout=5.0):
        assert self.lua_states[idx] == "DiplomacyActionView"
        if "ApplyStatement" in lua:  # choices reader
            if not self.view_visible:
                return ["VIEW_HIDDEN"]
            return [
                "LEADER|Who are you?",
                "CHOICE|CHOICE_POSITIVE|Hello|0",
                "CHOICE|CHOICE_NEGATIVE|Go away|0",
            ]
        if "OnSelectConversationDiplomacyStatement" in lua and "CHOICE_EXIT" not in lua:
            key = lua.split('OnSelectConversationDiplomacyStatement, "')[1].split('"')[
                0
            ]
            self.calls.append("press:" + key)
            if not self.view_visible:
                return ["VIEW_HIDDEN"]
            self.open = False
            return ["VIEW_SELECT"]
        if "UninitializeView" in lua:
            self.calls.append("release")
            self.view_visible = False
            return ["VIEW_RELEASED"]
        return []


def _gs(conn):
    gs = GameState(conn)  # type: ignore[arg-type]
    gs.RESPONSE_SETTLE_S = 0.001
    return gs


def test_game_state_reads_the_screen_buttons():
    conn = _ViewConn()
    choices = asyncio.run(_gs(conn).get_diplomacy_view_choices())
    assert [(c.key, c.text) for c in choices] == [
        ("CHOICE_POSITIVE", "Hello"),
        ("CHOICE_NEGATIVE", "Go away"),
    ]
    assert (
        asyncio.run(_gs(_ViewConn(view_visible=False)).get_diplomacy_view_choices())
        == []
    )


def test_a_choice_key_presses_the_button_instead_of_add_response():
    conn = _ViewConn()
    result = asyncio.run(_gs(conn).diplomacy_respond(3, "CHOICE_POSITIVE"))
    assert result == "OK:RESPONDED|POSITIVE|SESSION_CLOSED"
    assert conn.calls == ["press:CHOICE_POSITIVE", "release"]


def test_a_choice_key_falls_back_to_add_response_when_the_screen_is_gone():
    conn = _ViewConn(view_visible=False)
    result = asyncio.run(_gs(conn).diplomacy_respond(3, "CHOICE_NEGATIVE"))
    assert result == "OK:RESPONDED|NEGATIVE|SESSION_CLOSED"
    assert conn.calls == ["press:CHOICE_NEGATIVE", "add_response:NEGATIVE", "release"]
