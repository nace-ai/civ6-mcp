"""Which civilization an excavated artifact is credited to."""

import asyncio

import drex_fixtures as fx
from drex_fakes import FakeGame

from civ_mcp.drex.candidates import ActionKind, DecisionCategory
from civ_mcp.drex.enumerate import artifact_candidates
from civ_mcp.drex.executor import Executor, OutcomeStatus
from civ_mcp.drex.live import LiveObserver
from civ_mcp.drex.observation import DecisionMemory, DecisionSpec
from civ_mcp.drex.points import build_decision_point
from civ_mcp.drex.scheduler import (
    ARTIFACT_BLOCKER,
    PHASE_LATER_BLOCKERS,
    SUPPORTED_BLOCKERS,
    Scheduler,
    TurnLedger,
)
from civ_mcp.lua.drex_queries import (
    build_artifact_choice_query,
    build_choose_artifact_player,
    parse_artifact_choice,
)


async def _no_sleep(_):
    return None


def _point(obs, core):
    spec = DecisionSpec(DecisionCategory.ARTIFACT, "empire")
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


def test_parse_artifact_choice_with_two_claimants():
    lines = [
        "ARTIFACT|65700|Archaeologist|18|11|3|Classical Era",
        "PLAYER|0|Rome|acting",
        "PLAYER|3|Greece|target",
    ]
    a = parse_artifact_choice(lines)
    assert a.unit_id == 65700 and (a.x, a.y) == (18, 11)
    assert a.kind == "battle site" and a.era == "Classical Era"
    assert a.players == [(0, "Rome", "acting"), (3, "Greece", "target")]


def test_parse_artifact_choice_without_choice_and_without_artifact():
    a = parse_artifact_choice(
        [
            "ARTIFACT|65700|Archaeologist|18|11|2|Ancient Era",
            "PLAYER|63|Barbarians|acting",
        ]
    )
    assert a.kind == "barbarian camp" and a.players == [(63, "Barbarians", "acting")]
    assert parse_artifact_choice(["NO_ARTIFACT"]) is None


def test_artifact_lua_uses_the_popups_api():
    lua = build_artifact_choice_query()
    for s in (
        "GetNextExtractingArchaeologist",
        "GetArchaeology",
        "GetArtifactIndex",
        "Game.GetArtifactByIndex",
        "NO_ARTIFACT",
    ):
        assert s in lua
    assert "CHOOSE_ARTIFACT_PLAYER" not in lua  # read-only
    choose = build_choose_artifact_player(3)
    assert "PlayerOperations.CHOOSE_ARTIFACT_PLAYER" in choose
    assert "PARAM_PLAYER_ONE" in choose and "PLAYER_NOT_OFFERED" in choose
    assert 'print("OK:ARTIFACT_CHOSEN|"' in choose


def test_artifact_candidates_one_per_claimant():
    cands = artifact_candidates(fx.artifact_choice())
    assert [c.params.player_id for c in cands] == [0, 3]
    assert all(c.kind is ActionKind.CHOOSE_ARTIFACT_PLAYER for c in cands)
    assert cands[1].candidate_id == f"artifact:{fx.ARCHAEOLOGIST_ID}:3"
    assert "Greece" in cands[1].label
    assert cands[1].facts["role"] == "target"
    assert cands[1].facts["origin"] == "battle site"


def test_blocker_is_supported_not_phase_later():
    assert ARTIFACT_BLOCKER in SUPPORTED_BLOCKERS
    assert ARTIFACT_BLOCKER not in PHASE_LATER_BLOCKERS


def test_scheduler_offers_artifact_when_the_blocker_stands():
    game = FakeGame()
    game.artifact = fx.artifact_choice()
    game.extra_blockers = [(ARTIFACT_BLOCKER, "Choose artifact")]
    core = asyncio.run(LiveObserver(game).core())
    step = Scheduler().next(core, TurnLedger(turn=5))
    assert step.category is DecisionCategory.ARTIFACT


def test_choose_artifact_dispatches_and_confirms_from_dispatch():
    game = FakeGame()
    game.artifact = fx.artifact_choice()
    game.extra_blockers = [(ARTIFACT_BLOCKER, "x")]
    obs = LiveObserver(game)
    core = asyncio.run(obs.core())
    point, inputs = _point(obs, core)
    cand = next(c for c in point.candidates if c.params.player_id == 3)
    outcome = asyncio.run(
        Executor(game, sleep=_no_sleep).execute(
            cand, point, current_version=obs.version, turn=5, inputs=inputs
        )
    )
    assert outcome.status is OutcomeStatus.CONFIRMED
    assert game.calls == [("choose_artifact_player", (3,))]


def test_no_choice_artifact_is_forced_single_candidate():
    game = FakeGame()
    game.artifact = fx.artifact_choice(choice=False)
    game.extra_blockers = [(ARTIFACT_BLOCKER, "x")]
    obs = LiveObserver(game)
    core = asyncio.run(obs.core())
    point, _ = _point(obs, core)
    assert [c.params.player_id for c in point.candidates] == [0]
    assert point.forced_rule == "forced_single_candidate"


def test_precheck_rejects_a_claimant_no_longer_offered():
    game = FakeGame()
    game.artifact = fx.artifact_choice()
    game.extra_blockers = [(ARTIFACT_BLOCKER, "x")]
    obs = LiveObserver(game)
    core = asyncio.run(obs.core())
    point, _ = _point(obs, core)
    cand = next(c for c in point.candidates if c.params.player_id == 3)
    game.artifact = fx.artifact_choice(choice=False)
    out = asyncio.run(
        Executor(game, sleep=_no_sleep).execute(
            cand, point, current_version=obs.version, turn=5, inputs=None
        )
    )
    assert out.status is OutcomeStatus.REJECTED and game.calls == []
    assert "player_not_offered" in out.reason


def test_runner_resolves_the_artifact_and_advances(tmp_path):
    from test_drex_runner import PreferSelector, _fake_end_turn, _records

    from civ_mcp.drex.decision_log import DecisionLog
    from civ_mcp.drex.runner import RunConfig, Runner

    game = FakeGame()
    game.artifact = fx.artifact_choice()
    game.extra_blockers = [(ARTIFACT_BLOCKER, "Choose artifact")]
    log = DecisionLog(tmp_path / "run.jsonl", run_id="t", secrets=[])
    runner = Runner(
        game,
        PreferSelector(
            prefixes=(
                f"artifact:{fx.ARCHAEOLOGIST_ID}:3",
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
    assert ("choose_artifact_player", (3,)) in game.calls
    assert not [r for r in _records(tmp_path) if r["type"] == "unsupported_blocker"]
