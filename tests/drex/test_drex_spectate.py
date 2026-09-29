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
    result = asyncio.run(runner.run())
    assert result.stop_reason.startswith("error:RuntimeError")
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
