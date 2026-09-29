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
        game.end_turn_attempts = getattr(game, "end_turn_attempts", 0) + 1
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


def test_multi_round_diplomacy_keeps_the_models_reply(tmp_path):
    game = FakeGame()
    game.sessions = [fx.session()]
    game.session_rounds[1] = 2
    runner, _ = _runner(
        game,
        tmp_path,
        selector=PreferSelector(
            prefixes=("diplomacy:1:POSITIVE", "skip:", "research:", "produce:")
        ),
    )
    asyncio.run(runner.run())
    replies = [args for m, args in game.calls if m == "diplomacy_respond"]
    assert replies == [(1, "POSITIVE"), (1, "POSITIVE")]


def test_unobserved_deal_rejection_is_never_inverted_into_acceptance(tmp_path):
    game = FakeGame()
    game.deals = [fx.deal()]
    game.sessions = [fx.session(deal_summary="They offer: Gold per turn")]
    game.deal_sticky = True
    runner, checkpoints = _runner(
        game, tmp_path, selector=PreferSelector(prefixes=("deal:1:reject",))
    )
    result = asyncio.run(runner.run())
    assert ("respond_to_deal", (1, True)) not in game.calls
    assert result.stop_reason.startswith(("deal_unresolved", "selector_paused"))
    assert checkpoints


def test_at_war_peace_deal_reaches_the_selector_instead_of_being_closed(tmp_path):
    game = FakeGame()
    game.sessions = [fx.session(is_at_war=True)]
    game.deals = [fx.deal()]
    selector = PreferSelector(
        prefixes=("deal:1:accept", "skip:", "research:", "produce:")
    )
    runner, _ = _runner(game, tmp_path, selector=selector)
    asyncio.run(runner.run())
    assert ("diplomacy_respond", (1, "EXIT")) not in game.calls
    assert ("respond_to_deal", (1, True)) in game.calls


def test_dry_run_never_closes_sessions(tmp_path):
    game = FakeGame()
    game.sessions = [fx.session(is_at_war=True)]
    runner, _ = _runner(game, tmp_path, dry_run=True)
    result = asyncio.run(runner.run())
    assert result.stop_reason == "dry_run_complete"
    assert game.calls == []


def test_unexpected_error_stops_with_record_and_checkpoint(tmp_path):
    from civ_mcp.connection import LuaError

    game = FakeGame()

    async def broken(unit_index):
        raise LuaError("ERR: attempt to index a nil value")

    game.get_unit_action_space = broken
    runner, checkpoints = _runner(game, tmp_path)
    result = asyncio.run(runner.run())
    assert result.stop_reason.startswith("error:LuaError")
    assert checkpoints
    assert _records(tmp_path)[-1]["type"] == "stop"


def test_blocked_end_turn_with_nothing_new_to_decide_is_not_retried(tmp_path):
    game = FakeGame()
    game.extra_blockers = [("ENDTURN_BLOCKING_UNITS", "Units need orders")]
    runner, _ = _runner(game, tmp_path)
    result = asyncio.run(runner.run())
    assert result.stop_reason == "end_turn_blocked:ENDTURN_BLOCKING_UNITS"
    assert game.end_turn_attempts == 1


def test_war_interruption_allows_exactly_one_new_end_turn_request(tmp_path):
    game = FakeGame()
    normal = _fake_end_turn(game)
    first = True

    async def end_turn(gs):
        nonlocal first
        if first:
            first = False
            game.end_turn_attempts = 1
            return EndTurnOutcome(
                status="interrupted",
                turn_before=5,
                turn_after=5,
                housekeeping=[
                    {"action": "war_declaration_dismissed", "detail": "Egypt"}
                ],
            )
        return await normal(gs)

    log = DecisionLog(tmp_path / "run.jsonl", run_id="drex-test", secrets=[])
    runner = Runner(
        game,
        PreferSelector(),
        log,
        RunConfig(turns=1),
        end_turn=end_turn,
        checkpoint=lambda gs, t: None,
    )
    result = asyncio.run(runner.run())
    assert result.stop_reason == "turn_budget_reached"
    assert game.end_turn_attempts == 2


def test_only_sessions_and_deals_are_observed_while_end_turn_is_in_flight(tmp_path):
    game = FakeGame()
    normal = _fake_end_turn(game)
    seen = {}
    first = True

    async def end_turn(gs):
        nonlocal first
        if first:
            first = False
            game.sessions = [fx.session()]
            seen["units_queries"] = game.query_counts["get_units"]
            return EndTurnOutcome(
                status="blocked",
                turn_before=5,
                turn_after=5,
                diplomacy_pending=[1],
                end_turn_in_flight=True,
            )
        seen["units_queries_at_resume"] = game.query_counts["get_units"]
        return await normal(gs)

    log = DecisionLog(tmp_path / "run.jsonl", run_id="drex-test", secrets=[])
    selector = PreferSelector(
        prefixes=("diplomacy:1:POSITIVE", "skip:", "research:", "produce:")
    )
    runner = Runner(
        game,
        selector,
        log,
        RunConfig(turns=1),
        end_turn=end_turn,
        checkpoint=lambda gs, t: None,
    )
    result = asyncio.run(runner.run())
    assert result.stop_reason == "turn_budget_reached"
    assert ("diplomacy_respond", (1, "POSITIVE")) in game.calls
    assert seen["units_queries_at_resume"] == seen["units_queries"]


def test_pending_world_congress_stops_without_retrying(tmp_path):
    game = FakeGame()
    attempts = 0

    async def end_turn(gs):
        nonlocal attempts
        attempts += 1
        return EndTurnOutcome(
            status="blocked", turn_before=5, turn_after=5, world_congress_pending=True
        )

    checkpoints = []

    async def checkpoint(gs, turn):
        checkpoints.append(turn)
        return "cp"

    log = DecisionLog(tmp_path / "run.jsonl", run_id="drex-test", secrets=[])
    runner = Runner(
        game,
        PreferSelector(),
        log,
        RunConfig(turns=1),
        end_turn=end_turn,
        checkpoint=checkpoint,
    )
    result = asyncio.run(runner.run())
    assert result.stop_reason == "unsupported_blocker:world_congress"
    assert attempts == 1 and checkpoints == [5]


def test_move_is_followed_by_partial_refresh_not_full_observe(tmp_path):
    class TracingGame(FakeGame):
        def __init__(self):
            super().__init__()
            self.trace = []

        async def get_units(self):
            self.trace.append("get_units")
            return await super().get_units()

        async def get_cities(self):
            self.trace.append("get_cities")
            return await super().get_cities()

        async def move_unit(self, unit_index, x, y, *a, **kw):
            self.trace.append("move_unit")
            return await super().move_unit(unit_index, x, y, *a, **kw)

    game = TracingGame()
    runner, _ = _runner(
        game,
        tmp_path,
        selector=PreferSelector(prefixes=("move:", "research:", "produce:", "skip:")),
    )
    asyncio.run(runner.run())
    i = game.trace.index("move_unit")
    after = game.trace[i + 1 :]
    assert after and after[0] == "get_units"
    # cities are not re-read until the pre-end-turn full observe
    assert "get_cities" not in after[: after.index("get_units") + 1]


def test_full_observe_precedes_end_turn_after_partial_refreshes(tmp_path):
    class TracingGame(FakeGame):
        def __init__(self):
            super().__init__()
            self.trace = []

        async def get_cities(self):
            self.trace.append("get_cities")
            return await super().get_cities()

        async def skip_unit(self, unit_index):
            self.trace.append("skip_unit")
            return await super().skip_unit(unit_index)

    game = TracingGame()
    normal = _fake_end_turn(game)

    async def end_turn(gs):
        game.trace.append("end_turn")
        return await normal(gs)

    log = DecisionLog(tmp_path / "run.jsonl", run_id="drex-test", secrets=[])
    runner = Runner(game, PreferSelector(), log, RunConfig(turns=1), end_turn=end_turn)
    asyncio.run(runner.run())
    last_skip = len(game.trace) - 1 - game.trace[::-1].index("skip_unit")
    between = game.trace[last_skip + 1 : game.trace.index("end_turn")]
    # unit orders refresh units only; a full read (cities included) must run
    # before the turn is ended
    assert "get_cities" in between, game.trace


def test_no_redundant_full_read_when_nothing_was_dispatched(tmp_path):
    class TracingGame(FakeGame):
        def __init__(self):
            super().__init__()
            self.trace = []

        async def get_units(self):
            self.trace.append("get_units")
            return await super().get_units()

    game = TracingGame()
    game.research = "TECHNOLOGY_POTTERY"
    game.cities[fx.CAPITAL_ID].currently_building = "UNIT_SCOUT"
    for u in game.units.values():
        u.moves_remaining = 0
    normal = _fake_end_turn(game)

    async def end_turn(gs):
        game.trace.append("end_turn")
        return await normal(gs)

    log = DecisionLog(tmp_path / "run.jsonl", run_id="drex-test", secrets=[])
    runner = Runner(game, PreferSelector(), log, RunConfig(turns=1), end_turn=end_turn)
    asyncio.run(runner.run())
    before_end = game.trace[: game.trace.index("end_turn")]
    assert before_end.count("get_units") == 1, game.trace


# ------------------------------------------------------ timing records (C8)
def test_decision_records_carry_observe_time_and_roundtrips(tmp_path):
    game = FakeGame()
    runner, _ = _runner(game, tmp_path)
    asyncio.run(runner.run())
    rec = next(r for r in _records(tmp_path) if r["type"] == "decision")
    assert set(rec["timing_ms"]) >= {"api", "execute", "observe", "roundtrips"}
    assert rec["timing_ms"]["roundtrips"] >= 1
    turn = next(r for r in _records(tmp_path) if r["type"] == "turn")
    assert "roundtrips" in turn and "phase_ms" in turn


def test_speed_record_written_per_advanced_turn_and_on_turn_called(tmp_path):
    game = FakeGame()
    seen = []
    log = DecisionLog(tmp_path / "run.jsonl", run_id="drex-test", secrets=[])
    runner = Runner(
        game,
        PreferSelector(),
        log,
        RunConfig(turns=1),
        end_turn=_fake_end_turn(game),
        on_turn=seen.append,
    )
    asyncio.run(runner.run())
    speed = [r for r in _records(tmp_path) if r["type"] == "speed"]
    assert len(speed) == 1 and len(seen) == 1
    assert seen[0]["turn"] == speed[0]["turn"] == 5
    assert {
        "turn",
        "decisions",
        "seconds",
        "roundtrips",
        "drex_seconds",
        "end_turn_seconds",
    } <= set(speed[0])
    assert speed[0]["decisions"] == sum(
        1 for r in _records(tmp_path) if r["type"] == "decision"
    )
