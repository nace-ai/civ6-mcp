"""Tuner errors reconnect; the run never ends for them."""

import asyncio

from drex_fakes import FakeGame
from test_drex_runner import PreferSelector, _fake_end_turn, _records

from civ_mcp.connection import LuaError
from civ_mcp.drex.decision_log import DecisionLog
from civ_mcp.drex.runner import RunConfig, Runner


class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def _runner(game, tmp_path, clock, **kw):
    log = DecisionLog(tmp_path / "run.jsonl", run_id="drex-test", secrets=[])
    slept = []

    async def sleep(s):
        slept.append(s)
        clock.t += s

    r = Runner(
        game,
        PreferSelector(),
        log,
        RunConfig(turns=1),
        end_turn=_fake_end_turn(game),
        clock=clock,
        **kw,
    )
    r._sleep = sleep
    return r, slept


def test_lua_error_during_observe_reconnects_and_continues(tmp_path):
    game = FakeGame()
    game.fail["get_units"] = (LuaError("ERR: boom"), False)
    clock = _Clock()
    runner, slept = _runner(game, tmp_path, clock)
    result = asyncio.run(runner.run())
    assert result.stop_reason == "turn_budget_reached"
    assert game.conn.reconnects == 1 and slept[:1] == [1.0]
    recs = _records(tmp_path)
    assert [r["type"] for r in recs].count("header") == 1
    err = next(r for r in recs if r["type"] == "game_io_error")
    assert err["phase"] == "observe" and "LuaError" in err["error"]


def test_game_dead_triggers_relaunch_once_then_continues(tmp_path):
    game = FakeGame()
    state = {"dead": True}
    orig = game.get_units

    async def flaky():
        if state["dead"]:
            raise ConnectionError("socket closed")
        return await orig()

    game.get_units = flaky

    async def reconnect():
        if state["dead"]:
            raise ConnectionError("refused")

    game.conn.reconnect = reconnect
    relaunched = []

    async def relaunch():
        relaunched.append(True)
        state["dead"] = False
        return "relaunched"

    clock = _Clock()
    runner, slept = _runner(game, tmp_path, clock, relaunch=relaunch)
    runner.cfg.game_dead_after_s = 60.0
    result = asyncio.run(runner.run())
    assert result.stop_reason == "turn_budget_reached"
    assert relaunched == [True]
    assert max(slept) <= 30.0
    assert any(r["type"] == "game_relaunch" for r in _records(tmp_path))


def test_unexpected_exception_is_logged_with_checkpoint_and_run_continues(tmp_path):
    game = FakeGame()
    orig = game.get_cities
    calls = {"n": 0}

    async def flaky_second_call():
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("weird")
        return await orig()

    game.get_cities = flaky_second_call
    clock = _Clock()
    checkpoints = []

    async def checkpoint(gs, turn):
        checkpoints.append(turn)
        return f"DREX_CHECKPOINT_T{turn:04d}"

    runner, _ = _runner(game, tmp_path, clock, checkpoint=checkpoint)
    result = asyncio.run(runner.run())
    assert result.stop_reason == "turn_budget_reached"
    assert checkpoints == [5]
    err = next(r for r in _records(tmp_path) if r["type"] == "runner_error")
    assert "RuntimeError" in err["error"] and "traceback" in err


def test_dispatch_error_is_reconciled_not_replayed(tmp_path):
    """A raised dispatch never reaches the recovery loop and is never re-sent."""
    game = FakeGame()
    game.fail["set_research"] = (ConnectionError("dropped mid-send"), False)
    clock = _Clock()
    runner, _ = _runner(game, tmp_path, clock)
    result = asyncio.run(runner.run())
    assert result.stop_reason == "turn_budget_reached"
    sent = [a for m, a in game.calls if m == "set_research"]
    assert len(set(sent)) == len(sent), "the same research request was re-sent"
    dec = next(
        r
        for r in _records(tmp_path)
        if r["type"] == "decision" and r["dispatch"]["method"] == "set_research"
    )
    assert dec["outcome"]["reconciled"] is True
    assert not any(r["type"] == "game_io_error" for r in _records(tmp_path))
