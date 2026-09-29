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


# ---------------------------------------------------------------- snapshot
class _SnapshotGame:
    """A game that offers the batched snapshot; counts how often it is used."""

    def __init__(self, base):
        self.base = base
        self.snapshot_calls = []

    def __getattr__(self, name):
        return getattr(self.base, name)

    async def get_core_snapshot(self, parts=None):
        from civ_mcp.game_state import CORE_PARTS, CoreSnapshot

        parts = parts or CORE_PARTS
        self.snapshot_calls.append(frozenset(parts))
        b = self.base
        civ, seed = await b.get_game_identity()
        return CoreSnapshot(
            civ=civ,
            seed=seed,
            overview=await b.get_game_overview() if "overview" in parts else None,
            tech=await b.get_tech_civics() if "tech" in parts else None,
            progress=await b.get_progress_types() if "progress" in parts else None,
            cities=(await b.get_cities())[0] if "cities" in parts else None,
            units=await b.get_units() if "units" in parts else None,
            sessions=await b.get_diplomacy_sessions() if "sessions" in parts else None,
            deals=await b.get_pending_deals() if "deals" in parts else None,
            blockers=await b.get_end_turn_blockers() if "blockers" in parts else None,
            popup_state="POPUP" if "popup" in parts else None,
            errors={},
            roundtrips=2,
        )


def test_live_observer_uses_snapshot_when_available():
    from drex_fakes import FakeGame

    from civ_mcp.drex.live import LiveObserver

    game = _SnapshotGame(FakeGame())
    obs = LiveObserver(game)
    core = asyncio.run(obs.core())
    assert game.snapshot_calls and core.civ == "rome" and core.turn == 5
    assert core.popup_state == "POPUP"
    assert len(core.units) == 3 and core.cities[0].name == "Roma"


def test_live_observer_falls_back_to_individual_queries_without_snapshot():
    from drex_fakes import FakeGame

    from civ_mcp.drex.live import LiveObserver

    game = FakeGame()
    core = asyncio.run(LiveObserver(game).core())
    assert core.popup_state == "CLEAR" and core.turn == 5


# ---------------------------------------------------------------- prefetch (C5)
def test_next_inputs_are_prefetched_during_selection(tmp_path):
    from drex_fakes import FakeGame
    from test_drex_runner import PreferSelector, _fake_end_turn

    from civ_mcp.drex.decision_log import DecisionLog
    from civ_mcp.drex.runner import RunConfig, Runner

    game = FakeGame()
    order = []
    orig_space = game.get_unit_action_space

    async def spaced(idx):
        order.append(("space", idx))
        return await orig_space(idx)

    game.get_unit_action_space = spaced

    class SlowSelector(PreferSelector):
        async def choose(self, point):
            order.append(("choose", point.entity))
            await asyncio.sleep(0)  # let the prefetch task run
            await asyncio.sleep(0)
            order.append(("chosen", point.entity))
            return await super().choose(point)

    log = DecisionLog(tmp_path / "run.jsonl", run_id="t", secrets=[])
    runner = Runner(
        game,
        SlowSelector(prefixes=("skip:", "research:", "produce:")),
        log,
        RunConfig(turns=1),
        end_turn=_fake_end_turn(game),
    )
    asyncio.run(runner.run())
    # at least one action-space read happened between a 'choose' and its 'chosen'
    assert any(
        order[i][0] == "choose" and order[i + 1][0] == "space"
        for i in range(len(order) - 1)
    ), order
