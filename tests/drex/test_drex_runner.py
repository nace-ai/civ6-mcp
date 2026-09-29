"""Runner loop against an in-memory game: trace, stops, checkpoints, dry-run."""

import asyncio
import json

import drex_fixtures as fx
from drex_fakes import FakeGame

from civ_mcp.drex.decision import validate_selection
from civ_mcp.drex.decision_log import DecisionLog
from civ_mcp.drex.runner import RunConfig, Runner
from civ_mcp.drex.selectors import SelectionPaused, SelectionResult
from civ_mcp.end_turn import EndTurnOutcome


class PreferSelector:
    """Test selector: first candidate whose id starts with a preferred prefix."""

    name = "test-prefer"

    def __init__(self, prefixes=("skip:", "research:", "produce:")):
        self.prefixes = prefixes
        self.points = []

    async def choose(self, point):
        self.points.append(point)
        ids = sorted(c.candidate_id for c in point.candidates)
        pick = next((i for p in self.prefixes for i in ids if i.startswith(p)), ids[0])
        return SelectionResult(validate_selection(point, pick, selector=self.name))


class PausingSelector:
    name = "pausing"

    async def choose(self, point):
        raise SelectionPaused("DrexAuthError: HTTP 401", [{"attempt": 1, "ok": False}])


def _fake_end_turn(game):
    async def end_turn(gs):
        blockers = await game.get_end_turn_blockers()
        before = game.turn
        if blockers:
            return EndTurnOutcome(
                status="blocked",
                turn_before=before,
                turn_after=before,
                blockers=blockers,
            )
        game.end_turn_calls += 1
        game.turn += 1
        for u in game.units.values():
            u.moves_remaining = u.max_moves
        for idx, space in list(game.spaces.items()):
            fresh = {
                fx.WARRIOR_IDX: fx.warrior_space,
                fx.SETTLER_IDX: fx.settler_space,
                fx.BUILDER_IDX: fx.builder_space,
            }[idx]()
            fresh.x, fresh.y = space.x, space.y
            game.spaces[idx] = fresh
        return EndTurnOutcome(
            status="advanced",
            turn_before=before,
            turn_after=game.turn,
            housekeeping=[{"action": "autosave"}],
        )

    return end_turn


def _runner(game, tmp_path, selector=None, **cfg):
    checkpoints = []

    async def checkpoint(gs, turn):
        name = f"DREX_CHECKPOINT_T{turn:04d}"
        checkpoints.append(name)
        return name

    log = DecisionLog(tmp_path / "run.jsonl", run_id="drex-test", secrets=[])
    runner = Runner(
        game,
        selector or PreferSelector(),
        log,
        RunConfig(**{"turns": 1, **cfg}),
        end_turn=_fake_end_turn(game),
        checkpoint=checkpoint,
        run_meta={"selector": "test"},
    )
    return runner, checkpoints


def _records(tmp_path):
    return [
        json.loads(line) for line in (tmp_path / "run.jsonl").read_text().splitlines()
    ]


def test_one_turn_is_played_with_a_full_trace(tmp_path):
    game = FakeGame()
    runner, checkpoints = _runner(game, tmp_path)
    result = asyncio.run(runner.run())
    assert result.stop_reason == "turn_budget_reached"
    assert result.turns_advanced == 1
    methods = [m for m, _ in game.calls]
    assert methods == [
        "set_research",
        "set_city_production",
        "skip_unit",
        "skip_unit",
        "skip_unit",
    ]
    recs = _records(tmp_path)
    decisions = [r for r in recs if r["type"] == "decision"]
    assert len(decisions) == 5
    for d in decisions:
        assert d["decision"]["candidate_id"] in {c["id"] for c in d["candidates"]}
        assert d["dispatch"]["method"] in methods
        assert d["outcome"]["status"] in ("confirmed", "pending")
        assert d["observation_version"].startswith("rome:42:T5:")
    turn = next(r for r in recs if r["type"] == "turn")
    assert (turn["turn_before"], turn["turn_after"], turn["status"]) == (
        5,
        6,
        "advanced",
    )
    assert [r["type"] for r in recs][0] == "header" and recs[-1]["type"] == "stop"
    assert checkpoints == []


def test_research_is_not_redecided_on_the_next_turn(tmp_path):
    game = FakeGame()
    runner, _ = _runner(game, tmp_path, turns=2)
    asyncio.run(runner.run())
    assert [m for m, _ in game.calls].count("set_research") == 1


def test_unsupported_blocker_checkpoints_and_stops(tmp_path):
    game = FakeGame()
    game.extra_blockers = [
        ("ENDTURN_BLOCKING_GOVERNOR_APPOINTMENT", "Appoint a governor")
    ]
    runner, checkpoints = _runner(game, tmp_path)
    result = asyncio.run(runner.run())
    assert (
        result.stop_reason
        == "unsupported_blocker:ENDTURN_BLOCKING_GOVERNOR_APPOINTMENT"
    )
    assert checkpoints == ["DREX_CHECKPOINT_T0005"]
    assert game.end_turn_calls == 0


def test_selector_pause_checkpoints_and_stops_without_fallback(tmp_path):
    game = FakeGame()
    runner, checkpoints = _runner(game, tmp_path, selector=PausingSelector())
    result = asyncio.run(runner.run())
    assert result.stop_reason.startswith("selector_paused")
    assert checkpoints and game.calls == []


def test_dry_run_never_mutates(tmp_path):
    game = FakeGame()
    runner, _ = _runner(game, tmp_path, dry_run=True)
    result = asyncio.run(runner.run())
    assert result.stop_reason == "dry_run_complete"
    assert game.calls == []
    planned = next(r for r in _records(tmp_path) if r["type"] == "dry_run")
    assert planned["dispatch"]["method"] == "set_research"
    assert planned["context"]["decision"] == "research"


def test_dry_run_without_selector_previews_options_and_captures_fixture(tmp_path):
    game = FakeGame()
    log = DecisionLog(tmp_path / "run.jsonl", run_id="drex-test", secrets=[])
    runner = Runner(
        game, None, log, RunConfig(turns=1, dry_run=True), end_turn=_fake_end_turn(game)
    )
    result = asyncio.run(runner.run())
    assert result.stop_reason == "dry_run_complete" and game.calls == []
    preview = next(r for r in _records(tmp_path) if r["type"] == "dry_run")
    assert preview["decision"] is None and preview["dispatch"] is None
    assert len(preview["request"]["options"]) == 3
    assert runner.preview["spec"] == {"category": "research", "entity": "empire"}
    assert runner.preview["core"]["version"].startswith("rome:42:T5:")


def test_selector_required_outside_dry_run(tmp_path):
    import pytest

    with pytest.raises(ValueError):
        Runner(
            FakeGame(),
            None,
            DecisionLog(tmp_path / "x.jsonl", run_id="r", secrets=[]),
            RunConfig(),
        )


def test_game_identity_change_stops_the_run(tmp_path):
    game = FakeGame()
    runner, _ = _runner(game, tmp_path, turns=3)
    original = game.get_end_turn_blockers

    async def swap_after_first_turn():
        if game.turn >= 6:
            game.seed = 99
        return await original()

    game.get_end_turn_blockers = swap_after_first_turn
    result = asyncio.run(runner.run())
    assert result.stop_reason == "game_identity_changed"


def test_persistently_blocked_end_turn_stops_instead_of_retrying(tmp_path):
    game = FakeGame()
    game.extra_blockers = [("ENDTURN_BLOCKING_UNITS", "Units need orders")]
    runner, checkpoints = _runner(game, tmp_path)
    result = asyncio.run(runner.run())
    assert result.stop_reason == "end_turn_blocked:ENDTURN_BLOCKING_UNITS"
    assert checkpoints == ["DREX_CHECKPOINT_T0005"]
    assert game.end_turn_calls == 0


def test_informational_war_session_is_closed_as_logged_housekeeping(tmp_path):
    game = FakeGame()
    game.sessions = [fx.session(is_at_war=True)]
    runner, _ = _runner(game, tmp_path)
    asyncio.run(runner.run())
    assert ("diplomacy_respond", (1, "EXIT")) in game.calls
    hk = [r for r in _records(tmp_path) if r["type"] == "housekeeping"]
    assert hk and hk[0]["action"] == "informational_session_closed"
