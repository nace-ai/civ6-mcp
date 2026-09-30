"""Executor: typed dispatch, fresh prechecks, verified outcomes, no blind retries."""

import asyncio
import copy

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
    artifact_candidates,
    belief_candidates,
    captured_city_candidates,
    city_attack_candidates,
    civic_candidates,
    deal_candidates,
    dedication_candidates,
    diplomacy_candidates,
    envoy_candidates,
    escape_route_candidates,
    government_candidates,
    governor_candidates,
    great_person_candidates,
    pantheon_candidates,
    policy_candidates,
    production_candidates,
    promotion_candidates,
    purchase_candidates,
    religion_candidates,
    research_candidates,
    trade_route_candidates,
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
DISPATCH_CASES.append(
    (
        _cand(
            dedication_candidates(fx.dedications()),
            ActionKind.CHOOSE_DEDICATION,
            lambda c: c.params.name == "COMMEMORATION_SCIENTIFIC",
        ),
        ("choose_dedication", (0,)),
    )
)
DISPATCH_CASES.append(
    (
        _cand(
            city_attack_candidates(fx.capital(), fx.city_targets()),
            ActionKind.CITY_ATTACK,
            lambda c: (c.params.target_x, c.params.target_y) == (11, 12),
        ),
        ("city_attack", (fx.CAPITAL_ID, 11, 12)),
    )
)
DISPATCH_CASES.append(
    (
        _cand(
            trade_route_candidates(
                fx.trader(),
                fx.trader_space(),
                fx.trade_status(2, 0),
                fx.trade_destinations(),
            ),
            ActionKind.MAKE_TRADE_ROUTE,
            lambda c: c.params.city_name == "Kabul",
        ),
        ("make_trade_route", (fx.TRADER_IDX, 14, 12)),
    )
)
_FOUND = religion_candidates(
    fx.religion_founding(),
    {"religion_type": "RELIGION_BUDDHISM", "follower_belief": "BELIEF_CHORAL_MUSIC"},
)
DISPATCH_CASES.extend(
    [
        (
            _cand(
                _FOUND,
                ActionKind.FOUND_RELIGION,
                lambda c: c.params.founder_belief == "BELIEF_TITHE",
            ),
            (
                "found_religion",
                ("RELIGION_BUDDHISM", "BELIEF_CHORAL_MUSIC", "BELIEF_TITHE"),
            ),
        ),
        (
            _cand(
                belief_candidates(fx.religion_founding()),
                ActionKind.ADD_BELIEF,
                lambda c: c.params.belief_type == "BELIEF_MISSIONARY_ZEAL",
            ),
            ("add_belief", ("BELIEF_MISSIONARY_ZEAL",)),
        ),
    ]
)
_GP_CANDS = great_person_candidates(fx.great_people(), gold=1000, faith=0, forced=False)
DISPATCH_CASES.extend(
    [
        (
            _cand(_GP_CANDS, ActionKind.RECRUIT_GREAT_PERSON),
            ("recruit_great_person", (7,)),
        ),
        (
            _cand(
                _GP_CANDS,
                ActionKind.PATRONIZE_GREAT_PERSON,
                lambda c: c.params.individual_id == 9,
            ),
            ("patronize_great_person", (9, "YIELD_GOLD")),
        ),
    ]
)
_GOV_CANDS = governor_candidates(
    fx.governors(points=1, unassigned=True), [fx.capital()]
)
DISPATCH_CASES.extend(
    [
        (
            _cand(_GOV_CANDS, ActionKind.APPOINT_GOVERNOR),
            ("appoint_governor", ("GOVERNOR_THE_DEFENDER",)),
        ),
        (
            _cand(_GOV_CANDS, ActionKind.ASSIGN_GOVERNOR),
            ("assign_governor", ("GOVERNOR_THE_EDUCATOR", fx.CAPITAL_ID)),
        ),
        (
            _cand(_GOV_CANDS, ActionKind.PROMOTE_GOVERNOR),
            (
                "promote_governor",
                ("GOVERNOR_THE_EDUCATOR", "GOVERNOR_PROMOTION_EDUCATOR_LIBRARIAN"),
            ),
        ),
    ]
)
DISPATCH_CASES.append(
    (
        _cand(
            promotion_candidates(fx.promotable_warrior(), fx.warrior_promotions()),
            ActionKind.PROMOTE_UNIT,
            lambda c: c.params.promotion_type == "PROMOTION_BATTLECRY",
        ),
        ("promote_unit", (fx.WARRIOR_ID, "PROMOTION_BATTLECRY")),
    )
)


DISPATCH_CASES.append(
    (
        _cand(
            captured_city_candidates(fx.captured_city()),
            ActionKind.RESOLVE_CAPTURED_CITY,
            lambda c: c.params.action == "raze",
        ),
        ("resolve_captured_city", ("raze", 65540)),
    )
)
DISPATCH_CASES.append(
    (
        _cand(
            escape_route_candidates(fx.spy_escape()),
            ActionKind.CHOOSE_ESCAPE_ROUTE,
            lambda c: c.params.district_type == "DISTRICT_HARBOR",
        ),
        ("choose_spy_escape", ("DISTRICT_HARBOR",)),
    )
)
DISPATCH_CASES.append(
    (
        _cand(
            artifact_candidates(fx.artifact_choice()),
            ActionKind.CHOOSE_ARTIFACT_PLAYER,
            lambda c: c.params.player_id == 3,
        ),
        ("choose_artifact_player", (3,)),
    )
)


_UPGRADEABLE = fx.warrior()
_UPGRADEABLE.can_upgrade, _UPGRADEABLE.upgrade_target, _UPGRADEABLE.upgrade_cost = (
    True,
    "UNIT_SWORDSMAN",
    90,
)
DISPATCH_CASES.append(
    (
        _cand(
            unit_candidates(fx.warrior_space(), _UPGRADEABLE, me=0, gold=500)[0],
            ActionKind.UPGRADE_UNIT,
            lambda c: True,
        ),
        ("upgrade_unit", (fx.WARRIOR_ID,)),
    )
)


DISPATCH_CASES.append(
    (
        _cand(
            purchase_candidates(
                [fx.capital()],
                {fx.CAPITAL_ID: fx.production_options()},
                300,
                fx.WONDERS,
            )[0],
            ActionKind.PURCHASE_ITEM,
            lambda c: c.params.item_name == "UNIT_BUILDER",
        ),
        ("purchase_item", (fx.CAPITAL_ID, "UNIT", "UNIT_BUILDER", "YIELD_GOLD")),
    )
)


@pytest.mark.parametrize(
    "cand,expected", DISPATCH_CASES, ids=[c.candidate_id for c, _ in DISPATCH_CASES]
)
def test_dispatch_table_maps_each_kind_to_intended_call(cand, expected):
    call = dispatch_call(cand)
    assert (call.method, call.args) == expected


# Kinds that change nothing in the game (confirmed without a call).
NO_DISPATCH_KINDS = {
    ActionKind.WAIT_GREAT_PERSON,
    ActionKind.CHOOSE_RELIGION,
    ActionKind.CHOOSE_FOLLOWER_BELIEF,
    ActionKind.HOLD_FIRE,
    ActionKind.SAVE_GOLD,
}
# Phase 2 kinds whose candidate builders and fakes land in later tasks; each
# task removes its kinds here and adds them to DISPATCH_CASES.
PENDING_KINDS: set[ActionKind] = set()  # every Phase 2 kind has landed


def test_every_action_kind_has_a_dispatch_case():
    covered = {c.kind for c, _ in DISPATCH_CASES} | NO_DISPATCH_KINDS | PENDING_KINDS
    assert covered == set(ActionKind)
    assert not ({c.kind for c, _ in DISPATCH_CASES} & PENDING_KINDS)


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
    game.promotable = [fx.promotable_warrior()]
    game.governor_status = fx.governors(points=1, unassigned=True)
    game.great_people = fx.great_people()
    game.city_targets = {fx.CAPITAL_ID: fx.city_targets()}
    game.units[fx.TRADER_ID] = fx.trader()
    game.spaces[fx.TRADER_IDX] = fx.trader_space()
    game.trade_status = fx.trade_status(2, 0)
    game.trade_destinations = fx.trade_destinations()
    game.captured = fx.captured_city()
    game.spy_escape = fx.spy_escape()
    game.artifact = fx.artifact_choice()
    if cand.kind is ActionKind.UPGRADE_UNIT:
        game.units[fx.WARRIOR_ID] = copy.deepcopy(_UPGRADEABLE)
        game.gold = 500
    if cand.kind is ActionKind.PURCHASE_ITEM:
        game.gold = 500
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


# ------------------------------------------ review fix: production verify order
def test_production_confirmed_from_dispatch_without_readback_poll():
    game = FakeGame()
    obs, point, inputs = _live_point(
        game, DecisionCategory.PRODUCTION, f"city:{fx.CAPITAL_ID}"
    )
    calls = {"n": 0}
    orig = game.verify_production

    async def counting(city_id, item_name):
        calls["n"] += 1
        return await orig(city_id, item_name)

    game.verify_production = counting
    outcome = _execute(game, obs, point, inputs, point.candidates[0])
    assert outcome.status is OutcomeStatus.CONFIRMED
    assert outcome.reason == "production_confirmed_from_dispatch"
    assert calls["n"] == 0


def test_production_not_confirmed_when_dispatch_is_inconclusive_and_readback_says_no():
    game = FakeGame()
    obs, point, inputs = _live_point(
        game, DecisionCategory.PRODUCTION, f"city:{fx.CAPITAL_ID}"
    )
    orig = game.set_city_production

    async def maybe(*a, **kw):
        await orig(*a, **kw)
        game.cities[fx.CAPITAL_ID].currently_building = "nothing"
        return "MAYBE:PRODUCING|BUILDING_MONUMENT|canStart=false"

    game.set_city_production = maybe
    outcome = _execute(game, obs, point, inputs, point.candidates[0])
    assert outcome.status is not OutcomeStatus.CONFIRMED
    assert outcome.reason == "production_not_observed"


def test_production_readback_error_after_inconclusive_dispatch_is_not_confirmed():
    from civ_mcp.connection import LuaError

    game = FakeGame()
    obs, point, inputs = _live_point(
        game, DecisionCategory.PRODUCTION, f"city:{fx.CAPITAL_ID}"
    )
    orig = game.set_city_production

    async def maybe(*a, **kw):
        await orig(*a, **kw)
        return "MAYBE:PRODUCING|BUILDING_MONUMENT|pillaged"

    game.set_city_production = maybe
    game.fail["verify_production"] = (LuaError("ERR: nil"), False)
    outcome = _execute(game, obs, point, inputs, point.candidates[0])
    assert outcome.status is OutcomeStatus.UNKNOWN


# ------------------------------------------------- Phase 2 shared plumbing
def test_no_dispatch_candidates_are_confirmed_without_touching_the_game():
    from civ_mcp.drex.candidates import WaitParams
    from civ_mcp.drex.executor import NO_DISPATCH

    game = FakeGame()
    cand = Candidate.create(
        ActionKind.WAIT_GREAT_PERSON, WaitParams("Hypatia"), label="Wait"
    )
    assert dispatch_call(cand) is NO_DISPATCH
    assert NO_DISPATCH.to_record() == {"method": None, "args": []}
    outcome = _run(game, cand)
    assert outcome.status is OutcomeStatus.CONFIRMED and outcome.reason == "no_action"
    assert outcome.dispatched is False and game.calls == []


def test_every_new_action_kind_has_a_dispatch_mapping():
    from civ_mcp.drex import candidates as c

    ref = c.UnitRef(131073, 1, "UNIT_WARRIOR", 10, 12)
    samples = {
        ActionKind.PROMOTE_UNIT: c.PromoteParams(ref, "PROMOTION_BATTLECRY"),
        ActionKind.APPOINT_GOVERNOR: c.AppointGovernorParams("GOVERNOR_THE_EDUCATOR"),
        ActionKind.ASSIGN_GOVERNOR: c.AssignGovernorParams(
            "GOVERNOR_THE_EDUCATOR", fx.CAPITAL_ID
        ),
        ActionKind.PROMOTE_GOVERNOR: c.PromoteGovernorParams(
            "GOVERNOR_THE_EDUCATOR", "GOVERNOR_PROMOTION_EDUCATOR_LIBRARIAN"
        ),
        ActionKind.CHOOSE_DEDICATION: c.DedicationParams(0, "COMMEMORATION_SCIENTIFIC"),
        ActionKind.RECRUIT_GREAT_PERSON: c.GreatPersonParams(7, "Hypatia"),
        ActionKind.PATRONIZE_GREAT_PERSON: c.GreatPersonParams(
            7, "Hypatia", "YIELD_GOLD"
        ),
        ActionKind.WAIT_GREAT_PERSON: c.WaitParams("great_people"),
        ActionKind.CHOOSE_RELIGION: c.ReligionChoiceParams("RELIGION_BUDDHISM"),
        ActionKind.CHOOSE_FOLLOWER_BELIEF: c.BeliefParams(
            "BELIEF_CHORAL_MUSIC", "BELIEF_CLASS_FOLLOWER"
        ),
        ActionKind.FOUND_RELIGION: c.FoundReligionParams(
            "RELIGION_BUDDHISM", "BELIEF_CHORAL_MUSIC", "BELIEF_TITHE"
        ),
        ActionKind.ADD_BELIEF: c.BeliefParams("BELIEF_TITHE", "BELIEF_CLASS_ENHANCER"),
        ActionKind.CITY_ATTACK: c.CityAttackParams(
            fx.CAPITAL_ID, 11, 12, "UNIT_WARRIOR"
        ),
        ActionKind.HOLD_FIRE: c.HoldFireParams(fx.CAPITAL_ID),
    }
    expected = {
        ActionKind.PROMOTE_UNIT: ("promote_unit", (131073, "PROMOTION_BATTLECRY")),
        ActionKind.APPOINT_GOVERNOR: ("appoint_governor", ("GOVERNOR_THE_EDUCATOR",)),
        ActionKind.ASSIGN_GOVERNOR: (
            "assign_governor",
            ("GOVERNOR_THE_EDUCATOR", fx.CAPITAL_ID),
        ),
        ActionKind.PROMOTE_GOVERNOR: (
            "promote_governor",
            ("GOVERNOR_THE_EDUCATOR", "GOVERNOR_PROMOTION_EDUCATOR_LIBRARIAN"),
        ),
        ActionKind.CHOOSE_DEDICATION: ("choose_dedication", (0,)),
        ActionKind.RECRUIT_GREAT_PERSON: ("recruit_great_person", (7,)),
        ActionKind.PATRONIZE_GREAT_PERSON: (
            "patronize_great_person",
            (7, "YIELD_GOLD"),
        ),
        ActionKind.FOUND_RELIGION: (
            "found_religion",
            ("RELIGION_BUDDHISM", "BELIEF_CHORAL_MUSIC", "BELIEF_TITHE"),
        ),
        ActionKind.ADD_BELIEF: ("add_belief", ("BELIEF_TITHE",)),
        ActionKind.CITY_ATTACK: ("city_attack", (fx.CAPITAL_ID, 11, 12)),
    }
    for kind, params in samples.items():
        call = dispatch_call(Candidate.create(kind, params, label="x"))
        if kind in expected:
            assert (call.method, call.args) == expected[kind], kind
        else:
            assert call.method == "__none__", kind
