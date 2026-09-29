"""Great People: recruit, patronize or wait — always Drex's call."""

import asyncio

import drex_fixtures as fx
from drex_fakes import FakeGame
from test_drex_runner import PreferSelector, _fake_end_turn, _records

from civ_mcp.drex.candidates import ActionKind, DecisionCategory
from civ_mcp.drex.decision_log import DecisionLog
from civ_mcp.drex.enumerate import great_person_candidates
from civ_mcp.drex.executor import Executor, OutcomeStatus
from civ_mcp.drex.live import LiveObserver
from civ_mcp.drex.observation import DecisionMemory, DecisionSpec
from civ_mcp.drex.points import build_decision_point
from civ_mcp.drex.runner import RunConfig, Runner
from civ_mcp.drex.scheduler import CLAIM_BLOCKER, Scheduler, TurnLedger


async def _no_sleep(_):
    return None


def _point(obs, core):
    spec = DecisionSpec(DecisionCategory.GREAT_PERSON, "empire")
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


def test_wait_is_offered_unless_the_claim_is_forced():
    people = fx.great_people()
    free = great_person_candidates(people, gold=500, faith=0, forced=False)
    forced = great_person_candidates(people, gold=500, faith=0, forced=True)
    assert any(c.kind is ActionKind.WAIT_GREAT_PERSON for c in free)
    assert not any(c.kind is ActionKind.WAIT_GREAT_PERSON for c in forced)
    assert {c.kind for c in free} >= {
        ActionKind.RECRUIT_GREAT_PERSON,
        ActionKind.PATRONIZE_GREAT_PERSON,
    }


def test_patronage_requires_the_treasury():
    people = fx.great_people()
    cheap = great_person_candidates(people, gold=10, faith=0, forced=False)
    assert not any(c.kind is ActionKind.PATRONIZE_GREAT_PERSON for c in cheap)
    faithful = great_person_candidates(people, gold=0, faith=1000, forced=False)
    assert any(
        c.kind is ActionKind.PATRONIZE_GREAT_PERSON
        and c.params.yield_type == "YIELD_FAITH"
        for c in faithful
    )


def test_forced_claim_drops_wait():
    game = FakeGame()
    game.great_people = [p for p in fx.great_people() if p.can_recruit][:1]
    game.gold = 0.0
    game.extra_blockers = [(CLAIM_BLOCKER, "Claim a Great Person")]
    obs = LiveObserver(game)
    core = asyncio.run(obs.core())
    point, _ = _point(obs, core)
    assert [c.kind for c in point.candidates] == [ActionKind.RECRUIT_GREAT_PERSON]
    assert point.forced_rule == "forced_single_candidate"


def test_great_people_read_only_every_five_turns_or_when_forced():
    game = FakeGame()
    game.great_people = fx.great_people()
    game.turn = 6
    core = asyncio.run(LiveObserver(game).core())
    assert core.great_people is None and game.query_counts["get_great_people"] == 0
    game.turn = 10
    core = asyncio.run(LiveObserver(game).core())
    assert core.great_people and game.query_counts["get_great_people"] == 1
    game.turn = 7
    game.extra_blockers = [(CLAIM_BLOCKER, "x")]
    core = asyncio.run(LiveObserver(game).core())
    assert core.great_people and game.query_counts["get_great_people"] == 2


def test_scheduler_offers_great_people_once_per_turn_when_someone_is_claimable():
    game = FakeGame()
    game.great_people = fx.great_people()
    game.turn = 10
    game.gold = 1000.0
    core = asyncio.run(LiveObserver(game).core())
    s = Scheduler()
    ledger = TurnLedger(turn=10)
    seen = []
    while True:
        step = s.next(core, ledger)
        cat = getattr(step, "category", None)
        if cat is None:
            break
        seen.append(cat)
        from civ_mcp.drex.executor import ActionOutcome

        kind = (
            ActionKind.WAIT_GREAT_PERSON
            if cat is DecisionCategory.GREAT_PERSON
            else None
        )
        s.note(ledger, step, kind, ActionOutcome(OutcomeStatus.CONFIRMED, "x", False))
        if len(seen) > 40:
            break
    assert seen.count(DecisionCategory.GREAT_PERSON) == 1
    # after production, before units
    assert seen.index(DecisionCategory.GREAT_PERSON) > seen.index(
        DecisionCategory.PRODUCTION
    )
    assert seen.index(DecisionCategory.GREAT_PERSON) < seen.index(DecisionCategory.UNIT)


def test_recruit_dispatches_and_confirms():
    game = FakeGame()
    game.great_people = fx.great_people()
    game.turn = 10
    obs = LiveObserver(game)
    core = asyncio.run(obs.core())
    point, inputs = _point(obs, core)
    cand = next(
        c for c in point.candidates if c.kind is ActionKind.RECRUIT_GREAT_PERSON
    )
    outcome = asyncio.run(
        Executor(game, sleep=_no_sleep).execute(
            cand, point, current_version=obs.version, turn=10, inputs=inputs
        )
    )
    assert outcome.status is OutcomeStatus.CONFIRMED
    assert ("recruit_great_person", (7,)) in game.calls
    assert game.great_people[0].claimant == "Rome"


def test_wait_confirms_without_dispatch_and_resolves_for_the_turn(tmp_path):
    game = FakeGame()
    game.great_people = fx.great_people()
    game.turn = 10
    log = DecisionLog(tmp_path / "run.jsonl", run_id="t", secrets=[])
    runner = Runner(
        game,
        PreferSelector(
            prefixes=("great_person:wait", "skip:", "research:", "produce:")
        ),
        log,
        RunConfig(turns=1),
        end_turn=_fake_end_turn(game),
    )
    asyncio.run(runner.run())
    gp = [
        r
        for r in _records(tmp_path)
        if r["type"] == "decision" and r["category"] == "great_person"
    ]
    assert len(gp) == 1 and gp[0]["dispatch"]["method"] is None
    assert gp[0]["outcome"]["status"] == "confirmed"
    assert not any(
        m in ("recruit_great_person", "patronize_great_person") for m, _ in game.calls
    )
