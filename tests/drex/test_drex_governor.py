"""Governor appoint / assign / promote as Drex decisions."""

import asyncio

import drex_fixtures as fx
from drex_fakes import FakeGame

from civ_mcp.drex.candidates import ActionKind, DecisionCategory
from civ_mcp.drex.enumerate import governor_candidates
from civ_mcp.drex.executor import ActionOutcome, Executor, OutcomeStatus
from civ_mcp.drex.live import LiveObserver
from civ_mcp.drex.observation import DecisionMemory, DecisionSpec
from civ_mcp.drex.points import build_decision_point
from civ_mcp.drex.scheduler import GOVERNOR_BLOCKERS, Scheduler, TurnLedger


async def _no_sleep(_):
    return None


def _point(obs, core):
    spec = DecisionSpec(DecisionCategory.GOVERNOR, "empire")
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


def test_candidates_cover_appoint_assign_and_promote():
    cands = governor_candidates(fx.governors(points=1, unassigned=True), [fx.capital()])
    kinds = {c.kind for c in cands}
    assert kinds == {
        ActionKind.APPOINT_GOVERNOR,
        ActionKind.ASSIGN_GOVERNOR,
        ActionKind.PROMOTE_GOVERNOR,
    }
    assign = next(c for c in cands if c.kind is ActionKind.ASSIGN_GOVERNOR)
    assert assign.params.city_id == fx.CAPITAL_ID and assign.facts["city"] == "Roma"


def test_no_appoint_without_points_and_no_assign_to_a_governed_city():
    cands = governor_candidates(
        fx.governors(points=0, unassigned=False), [fx.capital()]
    )
    assert all(c.kind is not ActionKind.APPOINT_GOVERNOR for c in cands)
    assert all(c.kind is not ActionKind.ASSIGN_GOVERNOR for c in cands)
    # only promotions with points; none here
    assert cands == []


def test_scheduler_offers_governor_for_each_blocker_type():
    for b in sorted(GOVERNOR_BLOCKERS):
        game = FakeGame()
        game.extra_blockers = [(b, "governor")]
        core = asyncio.run(LiveObserver(game).core())
        step = Scheduler().next(core, TurnLedger(turn=5))
        assert step.category is DecisionCategory.GOVERNOR, b


def test_governor_decisions_are_capped_per_turn():
    s = Scheduler()
    game = FakeGame()
    game.extra_blockers = [("ENDTURN_BLOCKING_GOVERNOR_IDLE", "x")]
    core = asyncio.run(LiveObserver(game).core())
    ledger = TurnLedger(turn=5)
    n = 0
    while True:
        step = s.next(core, ledger)
        if getattr(step, "category", None) is not DecisionCategory.GOVERNOR:
            break
        s.note(
            ledger,
            step,
            ActionKind.APPOINT_GOVERNOR,
            ActionOutcome(OutcomeStatus.CONFIRMED, "x", True),
        )
        n += 1
        assert n <= 5
    assert n == 5


def test_appoint_dispatches_and_confirms():
    game = FakeGame()
    game.governor_status = fx.governors(points=1, unassigned=False)
    game.extra_blockers = [("ENDTURN_BLOCKING_GOVERNOR_APPOINTMENT", "x")]
    obs = LiveObserver(game)
    core = asyncio.run(obs.core())
    point, inputs = _point(obs, core)
    cand = next(c for c in point.candidates if c.kind is ActionKind.APPOINT_GOVERNOR)
    outcome = asyncio.run(
        Executor(game, sleep=_no_sleep).execute(
            cand, point, current_version=obs.version, turn=5, inputs=inputs
        )
    )
    assert outcome.status is OutcomeStatus.CONFIRMED
    assert ("appoint_governor", (cand.params.governor_type,)) in game.calls
    assert any(
        g.governor_type == cand.params.governor_type
        for g in game.governor_status.appointed
    )


def test_assign_and_promote_dispatch_and_confirm():
    game = FakeGame()
    game.governor_status = fx.governors(points=1, unassigned=True)
    game.extra_blockers = [("ENDTURN_BLOCKING_GOVERNOR_IDLE", "x")]
    obs = LiveObserver(game)
    core = asyncio.run(obs.core())
    point, inputs = _point(obs, core)
    assign = next(c for c in point.candidates if c.kind is ActionKind.ASSIGN_GOVERNOR)
    out = asyncio.run(
        Executor(game, sleep=_no_sleep).execute(
            assign, point, current_version=obs.version, turn=5, inputs=inputs
        )
    )
    assert out.status is OutcomeStatus.CONFIRMED
    assert ("assign_governor", ("GOVERNOR_THE_EDUCATOR", fx.CAPITAL_ID)) in game.calls
    core = asyncio.run(obs.core())
    point, inputs = _point(obs, core)
    promote = next(c for c in point.candidates if c.kind is ActionKind.PROMOTE_GOVERNOR)
    out = asyncio.run(
        Executor(game, sleep=_no_sleep).execute(
            promote, point, current_version=obs.version, turn=5, inputs=inputs
        )
    )
    assert out.status is OutcomeStatus.CONFIRMED
    assert game.governor_status.appointed[0].available_promotions == []


def test_precheck_rejects_appoint_when_points_are_gone():
    game = FakeGame()
    game.governor_status = fx.governors(points=1, unassigned=False)
    game.extra_blockers = [("ENDTURN_BLOCKING_GOVERNOR_APPOINTMENT", "x")]
    obs = LiveObserver(game)
    core = asyncio.run(obs.core())
    point, inputs = _point(obs, core)
    cand = next(c for c in point.candidates if c.kind is ActionKind.APPOINT_GOVERNOR)
    game.governor_status.points_available = 0
    game.governor_status.can_appoint = False
    out = asyncio.run(
        Executor(game, sleep=_no_sleep).execute(
            cand, point, current_version="stale", turn=5, inputs=None
        )
    )
    assert out.status is OutcomeStatus.REJECTED and game.calls == []
