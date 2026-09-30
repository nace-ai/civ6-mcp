"""Gold purchases: once per turn, buy an affordable item in a city or save."""

import asyncio

import drex_fixtures as fx
from drex_fakes import FakeGame

from civ_mcp import lua as lq
from civ_mcp.drex.candidates import (
    ActionKind,
    Candidate,
    DecisionCategory,
    PurchaseParams,
    SaveGoldParams,
)
from civ_mcp.drex.enumerate import purchase_candidates, shortlist
from civ_mcp.drex.executor import ActionOutcome, Executor, OutcomeStatus
from civ_mcp.drex.live import LiveObserver
from civ_mcp.drex.observation import DecisionMemory, DecisionSpec
from civ_mcp.drex.points import build_decision_point
from civ_mcp.drex.scheduler import PURCHASE_MIN_GOLD, Scheduler, TurnLedger


async def _no_sleep(_):
    return None


def _point(obs, core, failed=frozenset()):
    spec = DecisionSpec(DecisionCategory.PURCHASE, "empire")
    inputs = asyncio.run(obs.inputs(spec, core))
    point, excluded = build_decision_point(
        spec,
        core,
        inputs,
        DecisionMemory(),
        objective="o",
        decision_id="T5#1",
        max_options=255,
        failed=failed,
    )
    return point, inputs, excluded


def _options_by_city():
    return {fx.CAPITAL_ID: fx.production_options()}


def test_purchase_candidates_one_per_affordable_item_plus_save():
    cands, _ = purchase_candidates(
        [fx.capital()], _options_by_city(), gold=250, wonder_types=fx.WONDERS
    )
    buys = [c for c in cands if c.kind is ActionKind.PURCHASE_ITEM]
    assert sorted(c.params.item_name for c in buys) == [
        "BUILDING_MONUMENT",
        "UNIT_BUILDER",
    ]
    builder = next(c for c in buys if c.params.item_name == "UNIT_BUILDER")
    assert builder.candidate_id == f"purchase:{fx.CAPITAL_ID}:UNIT:UNIT_BUILDER"
    assert builder.facts["gold_cost"] == 200 and builder.facts["treasury"] == 250
    assert builder.facts["city"] == fx.capital().name
    save = [c for c in cands if c.kind is ActionKind.SAVE_GOLD]
    assert len(save) == 1 and save[0].candidate_id == "save_gold"


def test_unaffordable_items_are_excluded_with_the_reason():
    cands, excl = purchase_candidates(
        [fx.capital()], _options_by_city(), gold=210, wonder_types=fx.WONDERS
    )
    names = [c.params.item_name for c in cands if c.kind is ActionKind.PURCHASE_ITEM]
    assert names == ["UNIT_BUILDER"]
    assert any(
        "BUILDING_MONUMENT" in e.option and "costs 240 gold" in e.reason for e in excl
    )


def test_wonders_repairs_and_unpurchasable_items_are_never_offered():
    opts = fx.production_options()
    for o in opts:
        if o.item_name == "BUILDING_PYRAMIDS":
            o.gold_cost = 500
        if o.is_repair:
            o.gold_cost = 100
    cands, _ = purchase_candidates(
        [fx.capital()], {fx.CAPITAL_ID: opts}, gold=5000, wonder_types=fx.WONDERS
    )
    names = {c.params.item_name for c in cands if c.kind is ActionKind.PURCHASE_ITEM}
    assert names == {"UNIT_BUILDER", "BUILDING_MONUMENT"}


def _walk(game):
    core = asyncio.run(LiveObserver(game).core())
    s = Scheduler()
    ledger = TurnLedger(turn=5)
    seen = []
    for _ in range(40):
        step = s.next(core, ledger)
        cat = getattr(step, "category", None)
        if cat is None:
            break
        seen.append(cat)
        kind = None
        if cat is DecisionCategory.CITY_ATTACK:
            kind = ActionKind.HOLD_FIRE
        elif cat is DecisionCategory.PURCHASE:
            kind = ActionKind.SAVE_GOLD
        s.note(ledger, step, kind, ActionOutcome(OutcomeStatus.CONFIRMED, "x", False))
    return seen


def test_scheduler_offers_one_purchase_decision_per_turn_after_production():
    game = FakeGame()
    game.gold = 300
    seen = _walk(game)
    assert seen.count(DecisionCategory.PURCHASE) == 1
    assert seen.index(DecisionCategory.PURCHASE) > seen.index(
        DecisionCategory.PRODUCTION
    )
    assert seen.index(DecisionCategory.PURCHASE) < seen.index(DecisionCategory.UNIT)


def test_scheduler_skips_the_purchase_read_below_the_treasury_gate():
    game = FakeGame()
    game.gold = PURCHASE_MIN_GOLD - 1
    assert DecisionCategory.PURCHASE not in _walk(game)


def test_nothing_affordable_is_forced_save_and_resolves_the_turn():
    game = FakeGame()
    game.gold = 70  # above the gate, below every purchase
    obs = LiveObserver(game)
    core = asyncio.run(obs.core())
    point, inputs, _ = _point(obs, core)
    assert [c.kind for c in point.candidates] == [ActionKind.SAVE_GOLD]
    assert point.forced_rule == "forced_single_candidate"
    outcome = asyncio.run(
        Executor(game, sleep=_no_sleep).execute(
            point.candidates[0],
            point,
            current_version=obs.version,
            turn=5,
            inputs=inputs,
        )
    )
    assert outcome.status is OutcomeStatus.CONFIRMED and game.calls == []
    s = Scheduler()
    ledger = TurnLedger(turn=5)
    spec = DecisionSpec(DecisionCategory.PURCHASE, "empire")
    s.note(ledger, spec, ActionKind.SAVE_GOLD, outcome)
    assert not s._open(ledger, spec)


def test_purchase_dispatches_confirms_and_spends_gold():
    game = FakeGame()
    game.gold = 300
    obs = LiveObserver(game)
    core = asyncio.run(obs.core())
    point, inputs, _ = _point(obs, core)
    cand = next(
        c
        for c in point.candidates
        if getattr(c.params, "item_name", "") == "UNIT_BUILDER"
    )
    outcome = asyncio.run(
        Executor(game, sleep=_no_sleep).execute(
            cand, point, current_version=obs.version, turn=5, inputs=inputs
        )
    )
    assert outcome.status is OutcomeStatus.CONFIRMED
    assert (
        "purchase_item",
        (fx.CAPITAL_ID, "UNIT", "UNIT_BUILDER", "YIELD_GOLD"),
    ) in game.calls
    assert game.gold == 100


def test_purchase_precheck_uses_fresh_treasury():
    game = FakeGame()
    game.gold = 300
    obs = LiveObserver(game)
    core = asyncio.run(obs.core())
    point, _, _ = _point(obs, core)
    cand = next(
        c
        for c in point.candidates
        if getattr(c.params, "item_name", "") == "UNIT_BUILDER"
    )
    game.gold = 50  # spent earlier this turn
    outcome = asyncio.run(
        Executor(game, sleep=_no_sleep).execute(
            cand, point, current_version=obs.version, turn=5, inputs=None
        )
    )
    assert outcome.status is OutcomeStatus.REJECTED and game.calls == []
    assert "treasury" in outcome.reason


def test_stacking_conflict_is_rejected_and_excluded():
    game = FakeGame()
    game.gold = 300
    game.stacked_units_on_tile = {"UNIT_BUILDER"}
    obs = LiveObserver(game)
    core = asyncio.run(obs.core())
    point, inputs, _ = _point(obs, core)
    cand = next(
        c
        for c in point.candidates
        if getattr(c.params, "item_name", "") == "UNIT_BUILDER"
    )
    outcome = asyncio.run(
        Executor(game, sleep=_no_sleep).execute(
            cand, point, current_version=obs.version, turn=5, inputs=inputs
        )
    )
    assert (
        outcome.status is OutcomeStatus.REJECTED
        and "purchase_refused" in outcome.reason
    )
    again, _, excluded = _point(obs, core, failed={cand.candidate_id})
    assert cand.candidate_id not in {c.candidate_id for c in again.candidates}
    assert any(e.option == cand.candidate_id for e in excluded)


def test_purchase_shortlist_drops_most_expensive_first_and_keeps_save():
    cands = [
        Candidate.create(
            ActionKind.PURCHASE_ITEM,
            PurchaseParams(fx.CAPITAL_ID, "Rome", "UNIT", f"UNIT_X{i}", i),
            label=f"Buy X{i}",
            facts={"gold_cost": i},
        )
        for i in range(1, 301)
    ]
    cands.append(
        Candidate.create(ActionKind.SAVE_GOLD, SaveGoldParams(), label="Save", facts={})
    )
    kept, dropped = shortlist(cands, 255)
    assert len(kept) == 255
    assert any(c.kind is ActionKind.SAVE_GOLD for c in kept)
    costs = sorted(
        c.params.gold_cost for c in kept if c.kind is ActionKind.PURCHASE_ITEM
    )
    assert costs == list(range(1, 255))
    assert len(dropped) == 46


def test_production_option_gold_cost_drives_purchasability():
    assert lq.ProductionOption("UNIT", "UNIT_WARRIOR", 40, 8).gold_cost == -1
