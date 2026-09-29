"""Executor: typed dispatch, fresh prechecks, verified outcomes, no blind retries."""

import asyncio

import drex_fixtures as fx
import pytest
from drex_fakes import FakeGame

from civ_mcp.drex.candidates import (
    ActionKind,
    Candidate,
    DecisionCategory,
    DecisionPoint,
    KeepGovernmentParams,
)
from civ_mcp.drex.enumerate import (
    civic_candidates,
    deal_candidates,
    diplomacy_candidates,
    envoy_candidates,
    government_candidates,
    pantheon_candidates,
    policy_candidates,
    production_candidates,
    research_candidates,
    unit_candidates,
)
from civ_mcp.drex.executor import Executor, OutcomeStatus, dispatch_call


def _cand(cands, kind, pred=lambda c: True):
    return next(c for c in cands if c.kind is kind and pred(c))


def _units(space, unit):
    return unit_candidates(space, unit, me=fx.ME)[0]


def _point(cand, version="v1"):
    return DecisionPoint.create(
        decision_id="T5#1",
        category=DecisionCategory.UNIT,
        entity="test",
        observation_version=version,
        question="q",
        candidates=[cand],
        context={},
    )


async def _no_sleep(_):
    return None


def _run(game, cand, version="v1", turn=5, executor=None):
    ex = executor or Executor(game, sleep=_no_sleep, poll_attempts=2)
    return asyncio.run(
        ex.execute(cand, _point(cand), current_version=version, turn=turn)
    )


# Every supported kind -> (GameState method, positional args). unit_index,
# never the composite unit_id, reaches unit actions.
DISPATCH_CASES = [
    (
        _cand(research_candidates(fx.tech_status()), ActionKind.SET_RESEARCH),
        ("set_research", ("TECHNOLOGY_POTTERY",)),
    ),
    (
        _cand(civic_candidates(fx.tech_status()), ActionKind.SET_CIVIC),
        ("set_civic", ("CIVIC_CODE_OF_LAWS",)),
    ),
    (
        _cand(
            production_candidates(fx.capital(), fx.production_options(), fx.WONDERS)[0],
            ActionKind.SET_PRODUCTION,
            lambda c: c.params.item_name == "DISTRICT_ENCAMPMENT",
        ),
        (
            "set_city_production",
            (fx.CAPITAL_ID, "DISTRICT", "DISTRICT_ENCAMPMENT", 11, 11),
        ),
    ),
    (
        _cand(
            _units(fx.warrior_space(), fx.warrior()),
            ActionKind.MOVE_UNIT,
            lambda c: c.params.to_x == 11 and c.params.to_y == 12,
        ),
        ("move_unit", (fx.WARRIOR_IDX, 11, 12)),
    ),
    (
        _cand(_units(fx.warrior_space(), fx.warrior()), ActionKind.ATTACK),
        ("attack_unit", (fx.WARRIOR_IDX, 9, 12)),
    ),
    (
        _cand(_units(fx.warrior_space(), fx.warrior()), ActionKind.FORTIFY_UNIT),
        ("fortify_unit", (fx.WARRIOR_IDX,)),
    ),
    (
        _cand(_units(fx.warrior_space(), fx.warrior()), ActionKind.SKIP_UNIT),
        ("skip_unit", (fx.WARRIOR_IDX,)),
    ),
    (
        _cand(_units(fx.settler_space(), fx.settler()), ActionKind.FOUND_CITY),
        ("found_city", (fx.SETTLER_IDX,)),
    ),
    (
        _cand(
            _units(fx.builder_space(), fx.builder()),
            ActionKind.IMPROVE_TILE,
            lambda c: c.params.improvement_type == "IMPROVEMENT_FARM",
        ),
        ("improve_tile", (fx.BUILDER_IDX, "IMPROVEMENT_FARM")),
    ),
    (
        _cand(
            diplomacy_candidates(fx.session()),
            ActionKind.DIPLOMACY_RESPOND,
            lambda c: c.params.response == "POSITIVE",
        ),
        ("diplomacy_respond", (1, "POSITIVE")),
    ),
    (
        _cand(
            deal_candidates(fx.deal()),
            ActionKind.DEAL_RESPOND,
            lambda c: not c.params.accept,
        ),
        ("respond_to_deal", (1, False)),
    ),
    (
        _cand(
            policy_candidates(fx.policies(), fx.policies().slots[1]),
            ActionKind.SET_POLICY,
        ),
        ("set_policies", ({1: "POLICY_URBAN_PLANNING"},)),
    ),
    (
        _cand(
            envoy_candidates(fx.envoys()),
            ActionKind.SEND_ENVOY,
            lambda c: c.params.city_state_player_id == 20,
        ),
        ("send_envoy", (20,)),
    ),
    (
        _cand(
            government_candidates(fx.governments(), current_type="NONE"),
            ActionKind.CHANGE_GOVERNMENT,
        ),
        ("change_government", ("GOVERNMENT_CHIEFDOM",)),
    ),
    (
        Candidate.create(
            ActionKind.KEEP_GOVERNMENT, KeepGovernmentParams("NONE"), label="Keep"
        ),
        ("keep_current_government", ()),
    ),
    (
        _cand(
            pantheon_candidates(fx.pantheon()),
            ActionKind.CHOOSE_PANTHEON,
            lambda c: "FORGE" in c.params.belief_type,
        ),
        ("choose_pantheon", ("BELIEF_GOD_OF_THE_FORGE",)),
    ),
]


def _wounded_space():
    space = fx.warrior_space()
    space.hp, space.can_heal = 40, True
    return space


DISPATCH_CASES.append(
    (
        _cand(_units(_wounded_space(), fx.warrior()), ActionKind.HEAL_UNIT),
        ("heal_unit", (fx.WARRIOR_IDX,)),
    )
)


@pytest.mark.parametrize(
    "cand,expected", DISPATCH_CASES, ids=[c.candidate_id for c, _ in DISPATCH_CASES]
)
def test_dispatch_table_maps_each_kind_to_intended_call(cand, expected):
    call = dispatch_call(cand)
    assert (call.method, call.args) == expected


def test_every_action_kind_has_a_dispatch_case():
    assert {c.kind for c, _ in DISPATCH_CASES} == set(ActionKind)


@pytest.mark.parametrize(
    "cand,expected", DISPATCH_CASES, ids=[c.candidate_id for c, _ in DISPATCH_CASES]
)
def test_executor_performs_the_mapped_call_and_confirms_or_acknowledges(cand, expected):
    game = FakeGame()
    game.civic = None
    game.sessions = [fx.session()]
    game.deals = [fx.deal()]
    game.extra_blockers = [("ENDTURN_BLOCKING_CONSIDER_GOVERNMENT_CHANGE", "")]
    if cand.kind is ActionKind.HEAL_UNIT:
        game.spaces[fx.WARRIOR_IDX] = _wounded_space()
    outcome = _run(game, cand)
    assert game.calls == [expected]
    assert outcome.dispatched is True
    assert outcome.status in (OutcomeStatus.CONFIRMED, OutcomeStatus.PENDING), outcome


def test_stale_decision_is_rejected_without_dispatch():
    game = FakeGame()
    cand = research_candidates(fx.tech_status())[0]
    ex = Executor(game, sleep=_no_sleep)
    outcome = asyncio.run(
        ex.execute(cand, _point(cand, "v1"), current_version="v2", turn=5)
    )
    assert outcome.status is OutcomeStatus.REJECTED
    assert outcome.reason == "stale_observation"
    assert game.calls == []


def test_research_is_not_switched_when_already_selected():
    game = FakeGame()
    game.research = "TECHNOLOGY_MINING"
    outcome = _run(game, research_candidates(fx.tech_status())[0])
    assert outcome.status is OutcomeStatus.REJECTED
    assert outcome.reason.startswith("precheck:")
    assert game.calls == []


def test_production_never_overwrites_a_non_empty_queue():
    game = FakeGame()
    game.cities[fx.CAPITAL_ID].currently_building = "UNIT_WARRIOR"
    cand = production_candidates(fx.capital(), fx.production_options(), fx.WONDERS)[0][
        0
    ]
    assert _run(game, cand).status is OutcomeStatus.REJECTED
    assert game.calls == []


def test_move_rejected_if_unit_moved_since_observation():
    game = FakeGame()
    cand = _cand(_units(fx.warrior_space(), fx.warrior()), ActionKind.MOVE_UNIT)
    game._set_pos(fx.WARRIOR_IDX, 3, 3, 1.0)
    assert _run(game, cand).status is OutcomeStatus.REJECTED
    assert game.calls == []


def test_move_rejected_if_index_now_belongs_to_another_unit():
    game = FakeGame()
    cand = _cand(_units(fx.warrior_space(), fx.warrior()), ActionKind.MOVE_UNIT)
    game.spaces[fx.WARRIOR_IDX].unit_id = 999999
    outcome = _run(game, cand)
    assert outcome.status is OutcomeStatus.REJECTED
    assert "identity" in outcome.reason


def test_move_with_no_observed_position_change_is_unknown():
    game = FakeGame()
    game.ignore.add("move_unit")
    cand = _cand(_units(fx.warrior_space(), fx.warrior()), ActionKind.MOVE_UNIT)
    outcome = _run(game, cand)
    assert outcome.status is OutcomeStatus.UNKNOWN
    assert len(game.calls) == 1


def test_transport_error_after_mutation_is_reconciled_not_retried():
    game = FakeGame()
    game.fail["set_research"] = (ConnectionError("socket closed"), True)
    cand = research_candidates(fx.tech_status())[0]
    outcome = _run(game, cand)
    assert outcome.status is OutcomeStatus.CONFIRMED
    assert outcome.reconciled is True
    assert [m for m, _ in game.calls] == ["set_research"]


def test_transport_error_before_mutation_is_unknown_and_never_redispatched():
    game = FakeGame()
    game.fail["set_research"] = (ConnectionError("socket closed"), False)
    game.ignore.add("set_research")
    cand = research_candidates(fx.tech_status())[0]
    ex = Executor(game, sleep=_no_sleep, poll_attempts=1)
    first = _run(game, cand, executor=ex)
    assert first.status is OutcomeStatus.UNKNOWN and first.reconciled
    second = _run(game, cand, executor=ex)
    assert second.status is OutcomeStatus.REJECTED
    assert second.reason == "already_dispatched_this_turn"
    assert [m for m, _ in game.calls] == ["set_research"]


def test_same_attack_is_not_dispatched_twice_in_a_turn():
    game = FakeGame()
    cand = _cand(_units(fx.warrior_space(), fx.warrior()), ActionKind.ATTACK)
    ex = Executor(game, sleep=_no_sleep)
    _run(game, cand, executor=ex)
    game.spaces[fx.WARRIOR_IDX] = fx.warrior_space()
    game._set_pos(fx.WARRIOR_IDX, 10, 12, 2.0)
    game.spaces[fx.WARRIOR_IDX].targets = fx.warrior_space().targets
    again = _run(game, cand, executor=ex)
    assert again.reason == "already_dispatched_this_turn"
    assert [m for m, _ in game.calls] == ["attack_unit"]


def test_fortify_is_pending_until_fortification_is_observed():
    game = FakeGame()
    cand = _cand(_units(fx.warrior_space(), fx.warrior()), ActionKind.FORTIFY_UNIT)
    outcome = _run(game, cand)
    assert outcome.status is OutcomeStatus.PENDING


def test_outcome_record_is_serializable():
    import json

    game = FakeGame()
    outcome = _run(game, research_candidates(fx.tech_status())[0])
    json.dumps(outcome.to_record())


def test_refused_attack_in_real_result_format_is_rejected():
    game = FakeGame()
    game.attack_refused = True
    cand = _cand(_units(fx.warrior_space(), fx.warrior()), ActionKind.ATTACK)
    outcome = _run(game, cand)
    assert outcome.status is OutcomeStatus.REJECTED


def test_second_envoy_to_same_city_state_is_a_new_action():
    game = FakeGame()
    game.envoy_status.tokens_available = 2
    cand = _cand(
        envoy_candidates(fx.envoys()),
        ActionKind.SEND_ENVOY,
        lambda c: c.params.city_state_player_id == 20,
    )
    ex = Executor(game, sleep=_no_sleep, poll_attempts=1)
    assert _run(game, cand, executor=ex).status is OutcomeStatus.CONFIRMED
    assert _run(game, cand, executor=ex).status is OutcomeStatus.CONFIRMED
    assert [m for m, _ in game.calls] == ["send_envoy", "send_envoy"]


def test_same_reply_in_a_new_dialogue_round_is_dispatched_but_not_repeated_in_one():
    game = FakeGame()
    game.sessions = [fx.session()]
    game.session_rounds[1] = 3
    cand = _cand(
        diplomacy_candidates(fx.session()),
        ActionKind.DIPLOMACY_RESPOND,
        lambda c: c.params.response == "POSITIVE",
    )
    ex = Executor(game, sleep=_no_sleep, poll_attempts=1)
    assert _run(game, cand, executor=ex).dispatched
    assert _run(game, cand, executor=ex).dispatched
    game.ignore.add("diplomacy_respond")
    game.sessions[0].dialogue_text = "frozen"
    game.session_rounds[1] = 5

    async def no_change(pid, response):
        game.calls.append(("diplomacy_respond", (pid, response)))
        return "OK:RESPONDED|POSITIVE|SESSION_CONTINUES"

    game.diplomacy_respond = no_change
    _run(game, cand, executor=ex)
    again = _run(game, cand, executor=ex)
    assert again.reason == "already_dispatched_this_turn"


def test_dispatch_runs_with_connection_replay_disabled():
    game = FakeGame()
    _run(game, research_candidates(fx.tech_status())[0])
    assert game.replay_at_call == [False]
    assert game.conn.replay_on_disconnect is True


def test_policy_confirmation_is_polled_not_read_once():
    game = FakeGame()
    original = game.get_policies
    reads = {"n": 0}

    async def lagging():
        reads["n"] += 1
        status = await original()
        if reads["n"] == 2:
            for s in status.slots:
                if s.slot_index == 1:
                    s.current_policy = None
        return status

    game.get_policies = lagging
    cand = _cand(
        policy_candidates(fx.policies(), fx.policies().slots[1]), ActionKind.SET_POLICY
    )
    outcome = _run(
        game, cand, executor=Executor(game, sleep=_no_sleep, poll_attempts=3)
    )
    assert outcome.status is OutcomeStatus.CONFIRMED


# ------------------------------------------------ prechecks reuse inputs (C3)
def _live_point(game, category, entity):
    from civ_mcp.drex.live import LiveObserver
    from civ_mcp.drex.observation import DecisionMemory, DecisionSpec
    from civ_mcp.drex.points import build_decision_point

    obs = LiveObserver(game)
    core = asyncio.run(obs.core())
    spec = DecisionSpec(category, entity)
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
    return obs, point, inputs


def _count(game, name):
    return [c[0] for c in game.calls].count(name) + game.query_counts[name]


def test_precheck_reuses_provided_action_space_without_a_query():
    game = FakeGame()
    obs, point, inputs = _live_point(
        game, DecisionCategory.UNIT, f"unit:{fx.WARRIOR_ID}"
    )
    cand = _cand(point.candidates, ActionKind.MOVE_UNIT)
    before = game.query_counts["get_unit_action_space"]
    outcome = asyncio.run(
        Executor(game, sleep=_no_sleep).execute(
            cand, point, current_version=obs.version, turn=5, inputs=inputs
        )
    )
    assert outcome.status in (OutcomeStatus.CONFIRMED, OutcomeStatus.PENDING)
    assert game.query_counts["get_unit_action_space"] == before


def test_precheck_queries_when_inputs_are_from_an_older_version():
    game = FakeGame()
    obs, point, inputs = _live_point(
        game, DecisionCategory.UNIT, f"unit:{fx.WARRIOR_ID}"
    )
    cand = _cand(point.candidates, ActionKind.MOVE_UNIT)
    asyncio.run(obs.core())  # the observation moves on
    outcome = asyncio.run(
        Executor(game, sleep=_no_sleep).execute(
            cand, point, current_version=obs.version, turn=5, inputs=inputs
        )
    )
    assert outcome.status is OutcomeStatus.REJECTED
    assert outcome.reason == "stale_observation"


def test_research_precheck_reuses_progress_from_inputs():
    game = FakeGame()
    obs, point, inputs = _live_point(game, DecisionCategory.RESEARCH, "empire")
    assert inputs.progress is not None
    cand = point.candidates[0]
    n = game.query_counts["get_progress_types"]
    asyncio.run(
        Executor(game, sleep=_no_sleep).execute(
            cand, point, current_version=obs.version, turn=5, inputs=inputs
        )
    )
    # none for the precheck (inputs carry progress; eligibility was checked at
    # enumeration) and none for the postcondition (confirmed from the dispatch)
    assert game.query_counts["get_progress_types"] == n


# ------------------------------------------ confirm from dispatch output (C4)
def _move_point(game):
    obs, point, inputs = _live_point(
        game, DecisionCategory.UNIT, f"unit:{fx.WARRIOR_ID}"
    )
    return obs, point, inputs, _cand(point.candidates, ActionKind.MOVE_UNIT)


def _execute(game, obs, point, inputs, cand, executor=None):
    ex = executor or Executor(game, sleep=_no_sleep)
    return asyncio.run(
        ex.execute(cand, point, current_version=obs.version, turn=5, inputs=inputs)
    )


def test_move_confirmed_from_dispatch_output_without_polling():
    game = FakeGame()
    obs, point, inputs, cand = _move_point(game)
    n = game.query_counts["get_unit_state"]
    outcome = _execute(game, obs, point, inputs, cand)
    assert outcome.status is OutcomeStatus.CONFIRMED
    assert outcome.reason == "arrived_from_dispatch"
    assert game.query_counts["get_unit_state"] == n


def test_move_without_position_in_output_still_polls():
    game = FakeGame()
    game.move_result_suffix = ""
    obs, point, inputs, cand = _move_point(game)
    n = game.query_counts["get_unit_state"]
    outcome = _execute(game, obs, point, inputs, cand)
    assert outcome.reason in ("arrived", "moved_partially", "no_position_change")
    assert game.query_counts["get_unit_state"] > n


def test_production_confirmed_from_dispatch_when_readback_raises():
    from civ_mcp.connection import LuaError

    game = FakeGame()
    game.fail["verify_production"] = (LuaError("ERR: attempt to call nil"), False)
    obs, point, inputs = _live_point(
        game, DecisionCategory.PRODUCTION, f"city:{fx.CAPITAL_ID}"
    )
    cand = point.candidates[0]
    outcome = _execute(game, obs, point, inputs, cand)
    assert outcome.status is OutcomeStatus.CONFIRMED
    assert outcome.reason == "production_confirmed_from_dispatch"


def test_move_skips_predismiss_when_observation_says_no_popup():
    game = FakeGame()
    obs, point, inputs, cand = _move_point(game)
    ex = Executor(game, sleep=_no_sleep, popup_state_provider=lambda: "CLEAR")
    _execute(game, obs, point, inputs, cand, executor=ex)
    assert game.move_predismiss == [False]


def test_move_keeps_predismiss_when_a_popup_is_visible():
    game = FakeGame()
    obs, point, inputs, cand = _move_point(game)
    ex = Executor(game, sleep=_no_sleep, popup_state_provider=lambda: "POPUP")
    _execute(game, obs, point, inputs, cand, executor=ex)
    assert game.move_predismiss == [True]
