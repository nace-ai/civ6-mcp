"""Deterministic scheduler: fixed category order, budgets, unsupported blockers."""

import drex_fixtures as fx

from civ_mcp import lua as lq
from civ_mcp.drex.candidates import ActionKind, DecisionCategory
from civ_mcp.drex.executor import ActionOutcome, OutcomeStatus
from civ_mcp.drex.observation import Blocker, CoreObservation, DecisionSpec
from civ_mcp.drex.scheduler import EndTurn, Scheduler, Stop, TurnLedger


def _core(
    research=None,
    civic="CIVIC_CODE_OF_LAWS",
    building="nothing",
    blockers=(),
    units=None,
    **kw,
):
    base = dict(
        version="v",
        civ="rome",
        seed=42,
        local_player_id=fx.ME,
        overview=fx.overview(),
        tech=fx.tech_status(),
        progress=lq.ProgressTypes(research, civic),
        cities=[fx.capital(building=building)],
        units=units if units is not None else [fx.warrior(), fx.settler()],
        blockers=[Blocker(b) for b in blockers],
    )
    base.update(kw)
    return CoreObservation(**base)


def _outcome(status=OutcomeStatus.CONFIRMED):
    return ActionOutcome(status, "x", True)


def _next(core, ledger=None, **kw):
    return Scheduler(**kw).next(core, ledger or TurnLedger(turn=5))


def test_order_is_research_civic_production_then_units():
    s = Scheduler()
    ledger = TurnLedger(turn=5)
    core = _core(civic=None)
    seen = []
    for _ in range(10):
        step = s.next(core, ledger)
        if isinstance(step, EndTurn):
            break
        seen.append(step)
        kind = (
            ActionKind.SKIP_UNIT
            if step.category is DecisionCategory.UNIT
            else ActionKind.SET_RESEARCH
        )
        s.note(ledger, step, kind, _outcome())
    assert [(x.category, x.entity) for x in seen] == [
        (DecisionCategory.RESEARCH, "empire"),
        (DecisionCategory.CIVIC, "empire"),
        (DecisionCategory.PRODUCTION, f"city:{fx.CAPITAL_ID}"),
        (DecisionCategory.UNIT, f"unit:{fx.WARRIOR_ID}"),
        (DecisionCategory.UNIT, f"unit:{fx.SETTLER_ID}"),
    ]


def test_reactive_diplomacy_and_deals_come_first():
    core = _core(
        diplomacy_sessions=[fx.session(other_player_id=2)], pending_deals=[fx.deal()]
    )
    assert _next(core) == DecisionSpec(DecisionCategory.DIPLOMACY, "player:2")
    core = _core(pending_deals=[fx.deal()])
    assert _next(core) == DecisionSpec(DecisionCategory.DEAL, "player:1")


def test_session_carrying_a_pending_deal_is_decided_as_a_deal():
    core = _core(
        diplomacy_sessions=[fx.session(deal_summary="They offer: Gold")],
        pending_deals=[fx.deal()],
    )
    assert _next(core) == DecisionSpec(DecisionCategory.DEAL, "player:1")


def test_blocker_driven_categories_precede_research():
    core = _core(
        blockers=[
            "ENDTURN_BLOCKING_CONSIDER_GOVERNMENT_CHANGE",
            "ENDTURN_BLOCKING_FILL_CIVIC_SLOT",
            "ENDTURN_BLOCKING_GIVE_INFLUENCE_TOKEN",
        ]
    )
    assert _next(core).category is DecisionCategory.GOVERNMENT


def test_research_in_progress_is_not_redecided():
    step = _next(_core(research="TECHNOLOGY_MINING", building="UNIT_WARRIOR"))
    assert step.category is DecisionCategory.UNIT


def test_moving_unit_can_act_again_within_budget():
    s = Scheduler(max_unit_decisions=2)
    ledger = TurnLedger(turn=5)
    core = _core(research="T", building="UNIT_WARRIOR", units=[fx.warrior()])
    spec = s.next(core, ledger)
    s.note(ledger, spec, ActionKind.MOVE_UNIT, _outcome())
    assert s.next(core, ledger) == spec
    s.note(ledger, spec, ActionKind.MOVE_UNIT, _outcome())
    assert isinstance(s.next(core, ledger), EndTurn)


def test_partial_move_does_not_finish_the_unit():
    s = Scheduler()
    ledger = TurnLedger(turn=5)
    core = _core(research="T", building="UNIT_WARRIOR", units=[fx.warrior()])
    spec = s.next(core, ledger)
    s.note(ledger, spec, ActionKind.MOVE_UNIT, _outcome(OutcomeStatus.PENDING))
    assert s.next(core, ledger) == spec


def test_last_permitted_unit_decision_is_flagged():
    s = Scheduler(max_unit_decisions=2)
    ledger = TurnLedger(turn=5)
    core = _core(research="T", building="UNIT_WARRIOR", units=[fx.warrior()])
    spec = s.next(core, ledger)
    assert not s.final_unit_decision(ledger, spec)
    s.note(ledger, spec, ActionKind.MOVE_UNIT, _outcome())
    assert s.final_unit_decision(ledger, spec)


def test_non_move_order_finishes_the_unit():
    s = Scheduler()
    ledger = TurnLedger(turn=5)
    core = _core(research="T", building="UNIT_WARRIOR", units=[fx.warrior()])
    spec = s.next(core, ledger)
    s.note(ledger, spec, ActionKind.FORTIFY_UNIT, _outcome(OutcomeStatus.PENDING))
    assert isinstance(s.next(core, ledger), EndTurn)


def test_repeated_failures_give_up_on_that_key_for_the_turn():
    s = Scheduler(max_failures_per_key=2)
    ledger = TurnLedger(turn=5)
    core = _core(building="UNIT_WARRIOR", units=[])
    spec = s.next(core, ledger)
    assert spec.category is DecisionCategory.RESEARCH
    s.note(
        ledger,
        spec,
        ActionKind.SET_RESEARCH,
        _outcome(OutcomeStatus.REJECTED),
        "research:X",
    )
    assert s.next(core, ledger) == spec
    assert "research:X" in ledger.failed_candidates
    s.note(
        ledger,
        spec,
        ActionKind.SET_RESEARCH,
        _outcome(OutcomeStatus.UNKNOWN),
        "research:Y",
    )
    assert isinstance(s.next(core, ledger), EndTurn)


def test_decision_budget_stops_the_run():
    s = Scheduler(max_decisions_per_turn=1)
    ledger = TurnLedger(turn=5)
    core = _core()
    spec = s.next(core, ledger)
    s.note(ledger, spec, ActionKind.SET_RESEARCH, _outcome())
    step = s.next(core, ledger)
    assert isinstance(step, Stop) and "budget" in step.reason


def test_unsupported_blockers_are_reported():
    core = _core(
        blockers=[
            "ENDTURN_BLOCKING_UNITS",
            "ENDTURN_BLOCKING_GOVERNOR_APPOINTMENT",
            "ENDTURN_BLOCKING_WORLD_CONGRESS_LOOK",
        ]
    )
    assert Scheduler().unsupported_blockers(core) == [
        "ENDTURN_BLOCKING_GOVERNOR_APPOINTMENT"
    ]


def test_deal_session_without_pending_deal_is_unsupported():
    core = _core(diplomacy_sessions=[fx.session(deal_summary="They offer: Gold")])
    step = _next(core)
    assert isinstance(step, Stop) and "session" in step.reason


def test_informational_sessions_are_listed_for_housekeeping():
    core = _core(
        diplomacy_sessions=[
            fx.session(is_at_war=True),
            fx.session(other_player_id=2, buttons="GOODBYE"),
        ]
    )
    assert Scheduler().informational_sessions(core) == [1, 2]


def test_at_war_session_carrying_a_deal_is_decided_not_closed():
    core = _core(
        diplomacy_sessions=[fx.session(is_at_war=True)], pending_deals=[fx.deal()]
    )
    assert Scheduler().informational_sessions(core) == []
    assert _next(core) == DecisionSpec(DecisionCategory.DEAL, "player:1")


def test_at_war_session_without_a_deal_is_informational():
    core = _core(diplomacy_sessions=[fx.session(is_at_war=True)])
    assert Scheduler().informational_sessions(core) == [1]
