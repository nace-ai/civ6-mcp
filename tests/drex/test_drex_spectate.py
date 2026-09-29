"""Spectator integration: popup auto-dismiss and camera follow in Drex runs."""

import asyncio

import drex_fixtures as fx
from drex_fakes import FakeGame
from test_drex_runner import PreferSelector, _fake_end_turn

from civ_mcp.drex.candidates import (
    ActionKind,
    AttackParams,
    Candidate,
    MoveParams,
    ProductionParams,
    ResearchParams,
    UnitOrderParams,
    UnitRef,
)
from civ_mcp.drex.decision_log import DecisionLog
from civ_mcp.drex.live import LiveObserver
from civ_mcp.drex.runner import RunConfig, Runner
from civ_mcp.drex.spectate import LiveSpectator, focus_point
from civ_mcp.spectator import CameraController


class RecordingSpectator:
    def __init__(self):
        self.events = []

    def start(self):
        self.events.append(("start",))

    async def stop(self):
        self.events.append(("stop",))

    def focus(self, x, y, label=""):
        self.events.append(("focus", x, y, label))

    def turn_advanced(self):
        self.events.append(("turn",))

    def popup_status(self, state):
        self.events.append(("popup", state))

    def quiet(self, on):
        self.events.append(("quiet_on",) if on else ("quiet_off",))


def _unit_ref(game, idx):
    u = game._by_index(idx)
    return UnitRef(u.unit_id, u.unit_index, u.unit_type, u.x, u.y)


def _core(game):
    return asyncio.run(LiveObserver(game).core())


def test_focus_point_move_targets_destination():
    game = FakeGame()
    ref = _unit_ref(game, fx.WARRIOR_IDX)
    c = Candidate("m", ActionKind.MOVE_UNIT, MoveParams(ref, 7, 9), "Move")
    assert focus_point(c, _core(game)) == (7, 9, "Move")


def test_focus_point_attack_targets_defender():
    game = FakeGame()
    ref = _unit_ref(game, fx.WARRIOR_IDX)
    c = Candidate(
        "a",
        ActionKind.ATTACK,
        AttackParams(ref, 3, 4, "MELEE", "UNIT_WARRIOR", 1),
        "Attack",
    )
    assert focus_point(c, _core(game))[:2] == (3, 4)


def test_focus_point_unit_orders_use_unit_position():
    game = FakeGame()
    ref = _unit_ref(game, fx.SETTLER_IDX)
    c = Candidate("f", ActionKind.FOUND_CITY, UnitOrderParams(ref), "Found")
    assert focus_point(c, _core(game))[:2] == (ref.x, ref.y)


def test_focus_point_production_uses_city_from_observation():
    game = FakeGame()
    core = _core(game)
    city = core.cities[0]
    c = Candidate(
        "p",
        ActionKind.SET_PRODUCTION,
        ProductionParams(city.city_id, "UNIT", "UNIT_SCOUT"),
        "Scout",
    )
    assert focus_point(c, core)[:2] == (city.x, city.y)


def test_focus_point_none_for_empire_wide_decisions_and_unknown_city():
    game = FakeGame()
    core = _core(game)
    research = Candidate(
        "r", ActionKind.SET_RESEARCH, ResearchParams("TECH_MINING"), "Mining"
    )
    assert focus_point(research, core) is None
    ghost = Candidate(
        "p",
        ActionKind.SET_PRODUCTION,
        ProductionParams(999_999, "UNIT", "UNIT_SCOUT"),
        "Scout",
    )
    assert focus_point(ghost, core) is None


def _runner(game, tmp_path, spectator, **cfg):
    log = DecisionLog(tmp_path / "run.jsonl", run_id="drex-test", secrets=[])
    return Runner(
        game,
        PreferSelector(prefixes=("move:", "research:", "produce:", "skip:")),
        log,
        RunConfig(**{"turns": 1, **cfg}),
        end_turn=_fake_end_turn(game),
        spectator=spectator,
    )


def test_runner_starts_focuses_and_stops_spectator(tmp_path):
    game = FakeGame()
    spec = RecordingSpectator()
    result = asyncio.run(_runner(game, tmp_path, spec).run())
    assert result.stop_reason == "turn_budget_reached"
    kinds = [e[0] for e in spec.events]
    assert kinds[0] == "start" and kinds[-1] == "stop"
    assert "turn" in kinds
    focuses = [e for e in spec.events if e[0] == "focus"]
    assert focuses, "dispatched unit moves must move the camera"
    assert all(isinstance(e[1], int) and isinstance(e[2], int) for e in focuses)


def test_runner_stops_spectator_when_loop_raises(tmp_path):
    game = FakeGame()
    spec = RecordingSpectator()

    async def boom(gs):
        raise RuntimeError("tuner gone")

    log = DecisionLog(tmp_path / "run.jsonl", run_id="drex-test", secrets=[])
    runner = Runner(
        game,
        PreferSelector(),
        log,
        RunConfig(turns=1),
        end_turn=boom,
        spectator=spec,
    )

    async def no_sleep(_):
        return None

    runner._sleep = no_sleep
    runner.max_loop_iterations = 4
    result = asyncio.run(runner.run())
    assert result.stop_reason == "interrupted"  # bounded by the test only
    assert spec.events[-1] == ("stop",)


def test_runner_without_spectator_is_unchanged(tmp_path):
    game = FakeGame()
    result = asyncio.run(_runner(game, tmp_path, None).run())
    assert result.stop_reason == "turn_budget_reached"


def test_live_spectator_wraps_camera_and_popup_watcher():
    class Conn:
        async def execute_write(self, lua, timeout=5.0):
            return ["NO", "---END---"]

    async def scenario():
        s = LiveSpectator(Conn())
        s.start()
        s.focus(1, 2, "x")
        s.turn_advanced()
        await s.stop()

    asyncio.run(scenario())


# ------------------------------------------------ no contention, quiet (C6)
def test_spectator_is_quiet_during_end_turn(tmp_path):
    game = FakeGame()
    spec = RecordingSpectator()
    asyncio.run(_runner(game, tmp_path, spec).run())
    kinds = [e[0] for e in spec.events]
    i = kinds.index("quiet_on")
    j = kinds.index("quiet_off", i)
    assert "focus" not in kinds[i:j]


def test_runner_feeds_popup_status_from_each_observation(tmp_path):
    game = FakeGame()
    spec = RecordingSpectator()
    asyncio.run(_runner(game, tmp_path, spec).run())
    statuses = [e for e in spec.events if e[0] == "popup"]
    assert statuses and all(e[1] in ("CLEAR", "POPUP", "CRITICAL") for e in statuses)


def test_popup_watcher_external_mode_dismisses_after_delay(monkeypatch):
    from civ_mcp import spectator as sp

    calls = []

    async def fake_dismiss(conn):
        calls.append("dismiss")
        return "Dismissed X"

    monkeypatch.setattr("civ_mcp.game_lifecycle.dismiss_popup", fake_dismiss)

    async def scenario():
        w = sp.PopupWatcher(conn=None, poll=False)
        await w.report("POPUP", now=0.0)
        await w.report("POPUP", now=0.4)
        assert calls == []
        await w.report("POPUP", now=1.1)
        assert calls == ["dismiss"]
        await w.report("CRITICAL", now=2.0)
        await w.report("POPUP", now=2.5)
        await w.report("POPUP", now=3.0)
        assert calls == ["dismiss"]  # timer reset by CRITICAL; not yet 1s

    asyncio.run(scenario())


def test_camera_holds_hops_while_critical_or_quiet():
    class Conn:
        def __init__(self):
            self.lua = []

        async def execute_write(self, lua, timeout=5.0):
            self.lua.append(lua)
            return ["---END---"]

    async def scenario():
        conn = Conn()
        cam = CameraController(conn, check_diplomacy=False)
        cam.set_critical(True)
        cam.start()
        cam.push(1, 2)
        await asyncio.sleep(0.05)
        assert conn.lua == []  # held: diplomacy screen up
        cam.set_critical(False)
        await asyncio.sleep(0.2)
        assert any("LookAtPlot" in lua for lua in conn.lua)
        assert not any("DiplomacyActionView" in lua for lua in conn.lua)
        cam.quiet(True)
        cam.push(3, 4)
        n = len(conn.lua)
        await asyncio.sleep(0.05)
        assert len(conn.lua) == n  # held while quiet
        await cam.stop()

    asyncio.run(scenario())


def test_live_spectator_does_not_poll_on_its_own():
    class Conn:
        def __init__(self):
            self.lua = []

        async def execute_write(self, lua, timeout=5.0):
            self.lua.append(lua)
            return ["CLEAR", "---END---"]

    async def scenario():
        conn = Conn()
        s = LiveSpectator(conn)
        s.start()
        await asyncio.sleep(0.7)  # longer than the old 0.5 s poll interval
        await s.stop()
        assert conn.lua == []

    asyncio.run(scenario())
