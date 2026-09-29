"""GameConnection must not silently re-send a command when replay is disabled."""

import asyncio

import pytest

from civ_mcp.connection import GameConnection


def _conn(fail_first=True):
    conn = GameConnection()
    sent = []

    async def locked_execute(state_index, lua, timeout):
        sent.append(lua)
        if fail_first and len(sent) == 1:
            raise ConnectionError("socket closed")
        return ["OK"]

    async def noop():
        return None

    conn._locked_execute = locked_execute
    conn.ensure_connected = noop
    conn.reconnect = noop
    return conn, sent


def test_default_behaviour_reconnects_and_replays():
    conn, sent = _conn()
    assert asyncio.run(conn._execute_and_collect(1, "X", 1.0)) == ["OK"]
    assert sent == ["X", "X"]


def test_replay_disabled_reconnects_but_raises_instead_of_resending():
    conn, sent = _conn()

    async def go():
        with conn.replay_disabled():
            await conn._execute_and_collect(1, "MUTATE", 1.0)

    with pytest.raises(ConnectionError):
        asyncio.run(go())
    assert sent == ["MUTATE"]
    assert asyncio.run(conn._execute_and_collect(1, "READ", 1.0)) == ["OK"]
