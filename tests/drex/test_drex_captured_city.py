"""Captured or rebelled city: keep, raze, liberate or reject (Drex decides)."""

import asyncio

import drex_fixtures as fx
from drex_fakes import FakeGame

from civ_mcp.drex.candidates import ActionKind, DecisionCategory
from civ_mcp.drex.enumerate import captured_city_candidates
from civ_mcp.drex.executor import Executor, OutcomeStatus
from civ_mcp.drex.live import LiveObserver
from civ_mcp.drex.observation import DecisionMemory, DecisionSpec
from civ_mcp.drex.points import build_decision_point
from civ_mcp.drex.scheduler import (
    CAPTURED_CITY_BLOCKERS,
    PHASE_LATER_BLOCKERS,
    SUPPORTED_BLOCKERS,
    Scheduler,
    TurnLedger,
)
from civ_mcp.lua.drex_queries import (
    build_captured_city_query,
    parse_captured_city,
)

RAZE = "ENDTURN_BLOCKING_CONSIDER_RAZE_CITY"


async def _no_sleep(_):
    return None


def _point(obs, core):
    spec = DecisionSpec(DecisionCategory.CAPTURED_CITY, "empire")
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


def test_parse_captured_city_reads_city_and_engine_options():
    lines = [
        "CAPTURED|65540|Antium|14|9|4|1|captured|Greece|Greece",
        "OPTION|keep",
        "OPTION|liberate_founder",
        "OPTION|raze",
    ]
    c = parse_captured_city(lines)
    assert c.city_id == 65540 and c.name == "Antium" and (c.x, c.y) == (14, 9)
    assert c.population == 4 and c.districts == 1 and c.source == "captured"
    assert c.original_owner == "Greece"
    assert c.options == ["keep", "raze", "liberate_founder"]  # canonical order


def test_parse_captured_city_none_without_pending_city():
    assert parse_captured_city(["NO_PENDING_CITY"]) is None
    assert parse_captured_city([]) is None


def test_captured_city_query_checks_every_directive_the_popup_offers():
    lua = build_captured_city_query()
    for d in ("KEEP", "RAZE", "LIBERATE_FOUNDER", "LIBERATE_PREVIOUS_OWNER", "REJECT"):
        assert f"CityDestroyDirectives.{d}" in lua
    assert "GetNextRebelledCity" in lua and "GetNextCapturedCity" in lua
    assert "CanStartCommand" in lua and "RequestCommand" not in lua


def test_captured_city_candidates_one_per_engine_option():
    cands = captured_city_candidates(fx.captured_city())
    assert [c.params.action for c in cands] == ["keep", "raze", "liberate_founder"]
    assert all(c.kind is ActionKind.RESOLVE_CAPTURED_CITY for c in cands)
    lib = cands[2]
    assert lib.candidate_id == "captured:65540:liberate_founder"
    assert "Greece" in lib.label and "Antium" in lib.label
    assert lib.facts["population"] == 4 and lib.facts["source"] == "captured"


def test_blockers_are_supported_not_phase_later():
    assert CAPTURED_CITY_BLOCKERS <= SUPPORTED_BLOCKERS
    assert not (CAPTURED_CITY_BLOCKERS & PHASE_LATER_BLOCKERS)


def test_scheduler_offers_captured_city_first_for_both_blockers():
    for b in sorted(CAPTURED_CITY_BLOCKERS):
        game = FakeGame()
        game.captured = fx.captured_city()
        game.extra_blockers = [(b, "Consider city")]
        core = asyncio.run(LiveObserver(game).core())
        step = Scheduler().next(core, TurnLedger(turn=5))
        assert step.category is DecisionCategory.CAPTURED_CITY, b
        assert step.entity == "empire"


def test_resolve_dispatches_action_and_confirms_from_dispatch():
    game = FakeGame()
    game.captured = fx.captured_city()
    game.extra_blockers = [(RAZE, "x")]
    obs = LiveObserver(game)
    core = asyncio.run(obs.core())
    point, inputs = _point(obs, core)
    cand = next(c for c in point.candidates if c.params.action == "raze")
    outcome = asyncio.run(
        Executor(game, sleep=_no_sleep).execute(
            cand, point, current_version=obs.version, turn=5, inputs=inputs
        )
    )
    assert outcome.status is OutcomeStatus.CONFIRMED
    assert game.calls == [("resolve_city_capture", ("raze",))]
    assert game.query_counts["get_captured_city"] == 1  # inputs reused


def test_single_option_is_forced():
    game = FakeGame()
    game.captured = fx.captured_city(options=("keep",))
    game.extra_blockers = [(RAZE, "x")]
    obs = LiveObserver(game)
    core = asyncio.run(obs.core())
    point, _ = _point(obs, core)
    assert [c.params.action for c in point.candidates] == ["keep"]
    assert point.forced_rule == "forced_single_candidate"


def test_precheck_rejects_when_a_different_city_is_pending():
    game = FakeGame()
    game.captured = fx.captured_city()
    game.extra_blockers = [(RAZE, "x")]
    obs = LiveObserver(game)
    core = asyncio.run(obs.core())
    point, inputs = _point(obs, core)
    cand = next(c for c in point.candidates if c.params.action == "raze")
    other = fx.captured_city()
    other.city_id, other.name = 65541, "Ostia"
    game.captured = other
    # current observation, no reused inputs: the precheck itself re-reads
    outcome = asyncio.run(
        Executor(game, sleep=_no_sleep).execute(
            cand, point, current_version=obs.version, turn=5, inputs=None
        )
    )
    assert outcome.status is OutcomeStatus.REJECTED and game.calls == []
    assert "different_city" in outcome.reason


def test_precheck_rejects_an_action_the_engine_withdrew():
    game = FakeGame()
    game.captured = fx.captured_city()
    game.extra_blockers = [(RAZE, "x")]
    obs = LiveObserver(game)
    core = asyncio.run(obs.core())
    point, _ = _point(obs, core)
    cand = next(c for c in point.candidates if c.params.action == "raze")
    game.captured = fx.captured_city(options=("keep",))
    outcome = asyncio.run(
        Executor(game, sleep=_no_sleep).execute(
            cand, point, current_version="stale", turn=5, inputs=None
        )
    )
    assert outcome.status is OutcomeStatus.REJECTED and game.calls == []


def test_second_pending_city_is_decided_in_the_same_turn():
    game = FakeGame()
    game.captured = fx.captured_city()
    game.extra_blockers = [(RAZE, "x")]
    game.sticky_blockers = {RAZE}  # engine still flags: another city is pending
    obs = LiveObserver(game)
    core = asyncio.run(obs.core())
    s = Scheduler()
    ledger = TurnLedger(turn=5)
    step = s.next(core, ledger)
    point, inputs = _point(obs, core)
    cand = point.candidates[0]
    outcome = asyncio.run(
        Executor(game, sleep=_no_sleep).execute(
            cand, point, current_version=obs.version, turn=5, inputs=inputs
        )
    )
    s.note(ledger, step, cand.kind, outcome, cand.candidate_id)
    again = s.next(core, ledger)
    assert again.category is DecisionCategory.CAPTURED_CITY


def test_standing_blocker_with_nothing_pending_is_dismissed(tmp_path):
    from test_drex_runner import PreferSelector, _fake_end_turn, _records

    from civ_mcp.drex.decision_log import DecisionLog
    from civ_mcp.drex.runner import RunConfig, Runner

    game = FakeGame()
    game.captured = fx.captured_city()
    game.extra_blockers = [(RAZE, "Consider city")]
    game.sticky_blockers = {RAZE}
    log = DecisionLog(tmp_path / "run.jsonl", run_id="t", secrets=[])
    runner = Runner(
        game,
        PreferSelector(
            prefixes=("captured:65540:keep", "skip:", "research:", "produce:")
        ),
        log,
        RunConfig(turns=1),
        end_turn=_fake_end_turn(game),
    )
    runner._sleep = _no_sleep
    runner.max_loop_iterations = 80
    result = asyncio.run(runner.run())
    assert result.stop_reason == "turn_budget_reached"
    assert ("resolve_city_capture", ("keep",)) in game.calls
    recs = _records(tmp_path)
    assert not [r for r in recs if r["type"] == "unsupported_blocker"]
    hk = [
        r
        for r in recs
        if r["type"] == "housekeeping" and r["action"] == "blocker_dismissed"
    ]
    assert hk and RAZE in hk[0]["blockers"]
    decided = [
        r for r in recs if r["type"] == "decision" and r["category"] == "captured_city"
    ]
    assert decided and decided[0]["decision"]["candidate_id"] == "captured:65540:keep"
