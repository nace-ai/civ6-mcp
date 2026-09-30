"""A caught spy's escape route (escape-route and dragnet blockers)."""

import asyncio

import drex_fixtures as fx
from drex_fakes import FakeGame

from civ_mcp.drex.candidates import ActionKind, DecisionCategory
from civ_mcp.drex.enumerate import escape_route_candidates
from civ_mcp.drex.executor import Executor, OutcomeStatus
from civ_mcp.drex.live import LiveObserver
from civ_mcp.drex.observation import DecisionMemory, DecisionSpec
from civ_mcp.drex.points import build_decision_point
from civ_mcp.drex.scheduler import (
    PHASE_LATER_BLOCKERS,
    SPY_ESCAPE_BLOCKERS,
    SUPPORTED_BLOCKERS,
    Scheduler,
    TurnLedger,
)
from civ_mcp.lua.drex_queries import (
    ESCAPE_DISTRICTS,
    build_choose_spy_escape,
    build_spy_escape_options_query,
    parse_spy_escape_options,
)

ESCAPE = "ENDTURN_BLOCKING_SPY_CHOOSE_ESCAPE_ROUTE"
DRAGNET = "ENDTURN_BLOCKING_SPY_CHOOSE_DRAGNET_PRIORITY"


async def _no_sleep(_):
    return None


def _point(obs, core):
    spec = DecisionSpec(DecisionCategory.SPY_ESCAPE, "empire")
    inputs = asyncio.run(obs.inputs(spec, core))
    point, _ = build_decision_point(
        spec,
        core,
        inputs,
        DecisionMemory(),
        objective="o",
        decision_id="T5#1",
        max_options=255,
    )
    return point, inputs


def test_parse_spy_escape_options():
    lines = [
        "ESCAPE|65600|Artimpasa|Athens|20|15",
        "ROUTE|DISTRICT_CITY_CENTER",
        "ROUTE|DISTRICT_HARBOR",
    ]
    c = parse_spy_escape_options(lines)
    assert c.spy_unit_id == 65600 and c.spy_name == "Artimpasa"
    assert c.city_name == "Athens" and (c.x, c.y) == (20, 15)
    assert c.routes == ["DISTRICT_HARBOR", "DISTRICT_CITY_CENTER"]  # fastest first
    assert parse_spy_escape_options(["NO_ESCAPING_SPY"]) is None


def test_escape_query_checks_the_notification_before_asking_for_the_spy():
    lua = build_spy_escape_options_query()
    assert ESCAPE in lua and DRAGNET in lua
    assert lua.index("NotificationManager.GetList") < lua.index("GetNextEscapingSpyID")
    assert "NO_ESCAPING_SPY" in lua
    for d in ESCAPE_DISTRICTS:
        assert d in lua
    assert "SET_ESCAPE_ROUTE" not in lua  # read-only


def test_choose_escape_lua_validates_the_route_and_requests_it():
    lua = build_choose_spy_escape("DISTRICT_HARBOR")
    assert "HasDistrict" in lua and "ROUTE_NOT_AVAILABLE" in lua
    assert "PlayerOperations.SET_ESCAPE_ROUTE" in lua
    assert 'print("OK:ESCAPE_ROUTE|"' in lua
    assert "ERR:" in build_choose_spy_escape("DISTRICT_CAMPUS")  # not an escape route


def test_escape_route_candidates_one_per_route():
    cands = escape_route_candidates(fx.spy_escape())
    assert [c.params.district_type for c in cands] == [
        "DISTRICT_HARBOR",
        "DISTRICT_CITY_CENTER",
    ]
    assert all(c.kind is ActionKind.CHOOSE_ESCAPE_ROUTE for c in cands)
    assert cands[0].candidate_id == f"escape:{fx.SPY_ID}:DISTRICT_HARBOR"
    assert "Harbor" in cands[0].label and "Artimpasa" in cands[0].label
    assert cands[0].facts["city"] == "Athens"


def test_blockers_are_supported_not_phase_later():
    assert SPY_ESCAPE_BLOCKERS <= SUPPORTED_BLOCKERS
    assert not (SPY_ESCAPE_BLOCKERS & PHASE_LATER_BLOCKERS)


def test_scheduler_offers_spy_escape_for_both_blockers():
    for b in (ESCAPE, DRAGNET):
        game = FakeGame()
        game.spy_escape = fx.spy_escape()
        game.extra_blockers = [(b, "Choose escape route")]
        core = asyncio.run(LiveObserver(game).core())
        step = Scheduler().next(core, TurnLedger(turn=5))
        assert step.category is DecisionCategory.SPY_ESCAPE, b


def test_choose_escape_dispatches_and_confirms_from_dispatch():
    game = FakeGame()
    game.spy_escape = fx.spy_escape()
    game.extra_blockers = [(ESCAPE, "x")]
    obs = LiveObserver(game)
    core = asyncio.run(obs.core())
    point, inputs = _point(obs, core)
    cand = next(
        c for c in point.candidates if c.params.district_type == "DISTRICT_HARBOR"
    )
    outcome = asyncio.run(
        Executor(game, sleep=_no_sleep).execute(
            cand, point, current_version=obs.version, turn=5, inputs=inputs
        )
    )
    assert outcome.status is OutcomeStatus.CONFIRMED
    assert game.calls == [("choose_spy_escape", ("DISTRICT_HARBOR",))]


def test_city_center_only_is_forced():
    game = FakeGame()
    game.spy_escape = fx.spy_escape(routes=("DISTRICT_CITY_CENTER",))
    game.extra_blockers = [(ESCAPE, "x")]
    obs = LiveObserver(game)
    core = asyncio.run(obs.core())
    point, _ = _point(obs, core)
    assert len(point.candidates) == 1
    assert point.forced_rule == "forced_single_candidate"


def test_precheck_rejects_a_route_or_spy_that_changed():
    game = FakeGame()
    game.spy_escape = fx.spy_escape()
    game.extra_blockers = [(ESCAPE, "x")]
    obs = LiveObserver(game)
    core = asyncio.run(obs.core())
    point, _ = _point(obs, core)
    cand = next(
        c for c in point.candidates if c.params.district_type == "DISTRICT_HARBOR"
    )
    game.spy_escape = fx.spy_escape(routes=("DISTRICT_CITY_CENTER",))
    out = asyncio.run(
        Executor(game, sleep=_no_sleep).execute(
            cand, point, current_version=obs.version, turn=5, inputs=None
        )
    )
    assert out.status is OutcomeStatus.REJECTED and game.calls == []
    assert "route_not_available" in out.reason
    other = fx.spy_escape()
    other.spy_unit_id = fx.SPY_ID + 1
    game.spy_escape = other
    out = asyncio.run(
        Executor(game, sleep=_no_sleep).execute(
            cand, point, current_version=obs.version, turn=5, inputs=None
        )
    )
    assert out.status is OutcomeStatus.REJECTED and game.calls == []
    assert "different_spy" in out.reason


def test_runner_resolves_the_escape_and_advances(tmp_path):
    from test_drex_runner import PreferSelector, _fake_end_turn, _records

    from civ_mcp.drex.decision_log import DecisionLog
    from civ_mcp.drex.runner import RunConfig, Runner

    game = FakeGame()
    game.spy_escape = fx.spy_escape()
    game.extra_blockers = [(DRAGNET, "Choose dragnet priority")]
    log = DecisionLog(tmp_path / "run.jsonl", run_id="t", secrets=[])
    runner = Runner(
        game,
        PreferSelector(
            prefixes=(
                f"escape:{fx.SPY_ID}:DISTRICT_HARBOR",
                "skip:",
                "research:",
                "produce:",
            )
        ),
        log,
        RunConfig(turns=1),
        end_turn=_fake_end_turn(game),
    )
    runner._sleep = _no_sleep
    runner.max_loop_iterations = 80
    result = asyncio.run(runner.run())
    assert result.stop_reason == "turn_budget_reached"
    assert ("choose_spy_escape", ("DISTRICT_HARBOR",)) in game.calls
    assert not [r for r in _records(tmp_path) if r["type"] == "unsupported_blocker"]
