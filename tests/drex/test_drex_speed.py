"""Round-trip accounting and budgets for the decision loop."""

import asyncio

from civ_mcp.connection import GameConnection


class _Msg:
    def __init__(self, payload):
        self.payload = payload


class _Writer:
    def __init__(self):
        self.queue: list = []

    def is_closing(self):
        return False


class _Reader:
    def __init__(self, writer):
        self.writer = writer


def _fake_wire(monkeypatch, outputs):
    """Replace tuner_client I/O: every command returns `outputs` then the sentinel."""
    from civ_mcp import connection as conn_mod

    sent = []

    async def send_message(writer, tag, text):
        sent.append(text)
        writer.queue = [_Msg(f"O\x00InGame: {o}") for o in outputs] + [
            _Msg("O\x00InGame: ---END---")
        ]

    async def recv_message_timeout(reader, timeout=2.0):
        if reader.writer.queue:
            return reader.writer.queue.pop(0)
        return None

    async def drain_messages(reader, timeout=0.5):
        return []

    monkeypatch.setattr(conn_mod.tuner_client, "send_message", send_message)
    monkeypatch.setattr(
        conn_mod.tuner_client, "recv_message_timeout", recv_message_timeout
    )
    monkeypatch.setattr(conn_mod.tuner_client, "drain_messages", drain_messages)
    return sent


def _connected():
    c = GameConnection()
    w = _Writer()
    c._writer, c._reader = w, _Reader(w)
    c.gamecore_index, c.ingame_index = 0, 1
    return c


def test_each_execute_counts_one_roundtrip_and_records_time(monkeypatch):
    _fake_wire(monkeypatch, ["A", "B"])
    c = _connected()
    lines = asyncio.run(c.execute_write("print('x')"))
    assert lines == ["A", "B"]
    assert c.roundtrips == 1 and c.roundtrip_ms >= 0.0
    asyncio.run(c.execute_read("print('y')"))
    assert c.snapshot_counters()[0] == 2


def test_drain_waits_are_short(monkeypatch):
    from civ_mcp import connection as conn_mod

    waits = []

    async def drain_messages(reader, timeout=0.5):
        waits.append(timeout)
        return []

    _fake_wire(monkeypatch, ["A"])
    monkeypatch.setattr(conn_mod.tuner_client, "drain_messages", drain_messages)
    c = _connected()
    asyncio.run(c.execute_write("print('x')"))
    assert waits == [GameConnection.PRE_DRAIN_S, GameConnection.POST_DRAIN_S]
    assert GameConnection.PRE_DRAIN_S <= 0.02 and GameConnection.POST_DRAIN_S <= 0.05
