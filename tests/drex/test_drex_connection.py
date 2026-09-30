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


# ------------------------------------------------ hung game must surface
def test_consecutive_command_timeouts_raise_connection_error():
    """Live: a hung engine accepted commands and answered nothing; every call
    timed out and returned [] so the runner never saw an error and never
    relaunched. After a few incomplete commands in a row the connection must
    raise, which is what the runner's dead-game recovery listens for."""
    conn = GameConnection()
    conn._reader, conn._writer = object(), object()

    async def inner(state_index, lua, timeout):
        conn.dirty = True  # no sentinel arrived before the deadline
        return []

    conn._locked_execute_inner = inner
    with pytest.raises(ConnectionError):
        for _ in range(conn.MAX_CONSECUTIVE_TIMEOUTS):
            asyncio.run(conn._locked_execute(1, "X", 0.01))


def test_a_complete_command_resets_the_timeout_streak():
    conn = GameConnection()
    conn._reader, conn._writer = object(), object()
    calls = []

    async def inner(state_index, lua, timeout):
        calls.append(lua)
        conn.dirty = len(calls) % 2 == 1  # alternate: timeout, complete, ...
        return [] if conn.dirty else ["OK"]

    conn._locked_execute_inner = inner
    for _ in range(conn.MAX_CONSECUTIVE_TIMEOUTS * 2):
        asyncio.run(conn._locked_execute(1, "X", 0.01))  # never raises


def test_handshake_has_a_deadline(monkeypatch):
    import civ_mcp.connection as c

    async def fake_connect(host, port):
        return object(), object()

    async def hanging_handshake(reader, writer):
        await asyncio.sleep(3600)

    monkeypatch.setattr(c.tuner_client, "connect", fake_connect)
    monkeypatch.setattr(c.tuner_client, "handshake", hanging_handshake)
    conn = GameConnection()
    conn.HANDSHAKE_TIMEOUT_S = 0.05
    with pytest.raises(ConnectionError):
        asyncio.run(conn.connect())
