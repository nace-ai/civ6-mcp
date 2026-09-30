"""Foreign policy: once per turn, one proactive diplomatic move or nothing."""

import asyncio

import drex_fixtures as fx
from drex_fakes import FakeGame

from civ_mcp.drex.candidates import ActionKind, DecisionCategory
from civ_mcp.drex.enumerate import foreign_policy_candidates
from civ_mcp.drex.executor import ActionOutcome, Executor, OutcomeStatus
from civ_mcp.drex.live import LiveObserver
from civ_mcp.drex.observation import DecisionMemory, DecisionSpec
from civ_mcp.drex.points import build_decision_point
from civ_mcp.drex.scheduler import Scheduler, TurnLedger


async def _no_sleep(_):
    return None


def _point(obs, core):
    spec = DecisionSpec(DecisionCategory.FOREIGN_POLICY, "empire")
    inputs = asyncio.run(obs.inputs(spec, core))
    point, excluded = build_decision_point(
        spec,
        core,
        inputs,
        DecisionMemory(),
        objective="o",
        decision_id="T5#1",
        max_options=255,
    )
    return point, inputs, excluded


def _ids(cands):
    return sorted(c.candidate_id for c in cands)


def test_candidates_cover_every_engine_allowed_action_plus_nothing():
    cands = foreign_policy_candidates(fx.civs(), our_strength=100)
    assert _ids(cands) == [
        "diplo:1:DECLARE_SURPRISE_WAR",
        "diplo:1:DIPLOMATIC_DELEGATION",
        "no_diplomacy",
        "peace:2",
    ]
    war = next(c for c in cands if c.candidate_id == "diplo:1:DECLARE_SURPRISE_WAR")
    assert war.kind is ActionKind.DIPLOMATIC_ACTION
    assert war.facts["their_military_strength"] == 120
    assert war.facts["our_military_strength"] == 100
    assert war.facts["their_cities"] == 3 and war.facts["relationship"] == "NEUTRAL"
    assert "Egypt" in war.label
    peace = next(c for c in cands if c.kind is ActionKind.PROPOSE_PEACE)
    assert peace.params.player_id == 2 and "Greece" in peace.label


def test_war_is_offered_only_when_the_engine_allows_it():
    civs = fx.civs()
    civs[0].available_actions = ["DIPLOMATIC_DELEGATION"]
    cands = foreign_policy_candidates(civs, our_strength=100)
    assert not [c for c in cands if "WAR" in c.candidate_id]
    civs[0].available_actions = ["DECLARE_WAR"]
    civs[0].diplomatic_state = "DENOUNCED"
    ids = _ids(foreign_policy_candidates(civs, our_strength=100))
    assert "diplo:1:DECLARE_SURPRISE_WAR" in ids and "diplo:1:DECLARE_FORMAL_WAR" in ids


def test_at_war_civ_offers_only_peace():
    civs = fx.civs()
    civs[1].available_actions = ["DIPLOMATIC_DELEGATION", "DECLARE_FRIENDSHIP"]
    cands = foreign_policy_candidates(civs, our_strength=100)
    greece = [c for c in cands if getattr(c.params, "player_id", None) == 2]
    assert [c.kind for c in greece] == [ActionKind.PROPOSE_PEACE]


def test_friendship_embassy_denounce_open_borders_and_alliance_map_to_kinds():
    civs = fx.civs()
    civs[0].available_actions = [
        "DECLARE_FRIENDSHIP",
        "RESIDENT_EMBASSY",
        "DENOUNCE",
        "Open Borders (via propose_trade)",
        "MAKE_ALLIANCE",
    ]
    cands = foreign_policy_candidates(civs[:1], our_strength=100)
    assert _ids(cands) == [
        "alliance:1:MILITARY",
        "diplo:1:DECLARE_FRIENDSHIP",
        "diplo:1:DENOUNCE",
        "diplo:1:OPEN_BORDERS",
        "diplo:1:RESIDENT_EMBASSY",
        "no_diplomacy",
    ]


def test_no_action_candidate_is_always_present():
    game = FakeGame()
    game.civs = [fx.civs()[2]]  # nobody met
    obs = LiveObserver(game)
    core = asyncio.run(obs.core())
    point, _, _ = _point(obs, core)
    assert [c.kind for c in point.candidates] == [ActionKind.NO_DIPLOMACY]
    assert point.forced_rule == "forced_single_candidate"


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
        kind = {
            DecisionCategory.CITY_ATTACK: ActionKind.HOLD_FIRE,
            DecisionCategory.PURCHASE: ActionKind.SAVE_GOLD,
            DecisionCategory.FOREIGN_POLICY: ActionKind.NO_DIPLOMACY,
        }.get(cat)
        s.note(ledger, step, kind, ActionOutcome(OutcomeStatus.CONFIRMED, "x", False))
    return seen


def test_scheduler_offers_foreign_policy_once_per_turn_after_purchases():
    game = FakeGame()
    game.gold = 300
    seen = _walk(game)
    assert seen.count(DecisionCategory.FOREIGN_POLICY) == 1
    assert seen.index(DecisionCategory.FOREIGN_POLICY) > seen.index(
        DecisionCategory.PURCHASE
    )
    assert seen.index(DecisionCategory.FOREIGN_POLICY) < seen.index(
        DecisionCategory.UNIT
    )


def _execute(game, cand_id, inputs_current=True):
    obs = LiveObserver(game)
    core = asyncio.run(obs.core())
    point, inputs, _ = _point(obs, core)
    cand = next(c for c in point.candidates if c.candidate_id == cand_id)
    return asyncio.run(
        Executor(game, sleep=_no_sleep).execute(
            cand,
            point,
            current_version=obs.version,
            turn=5,
            inputs=inputs if inputs_current else None,
        )
    )


def test_delegation_and_friendship_dispatch_and_confirm():
    game = FakeGame()
    out = _execute(game, "diplo:1:DIPLOMATIC_DELEGATION")
    assert out.status is OutcomeStatus.CONFIRMED
    assert ("send_diplomatic_action", (1, "DIPLOMATIC_DELEGATION")) in game.calls
    game = FakeGame()
    game.civs[0].available_actions = ["DECLARE_FRIENDSHIP"]
    out = _execute(game, "diplo:1:DECLARE_FRIENDSHIP")
    assert out.status is OutcomeStatus.CONFIRMED and "ACCEPTED" in out.evidence.get(
        "answer", ""
    )


def test_declined_proposals_confirm_with_the_answer_as_evidence():
    game = FakeGame()
    out = _execute(game, "peace:2")
    assert out.status is OutcomeStatus.CONFIRMED
    assert out.reason == "offer_declined"
    assert "rejected" in out.evidence.get("answer", "").lower()
    assert ("propose_peace", (2,)) in game.calls


def test_war_declaration_confirms_or_stays_pending():
    game = FakeGame()
    out = _execute(game, "diplo:1:DECLARE_SURPRISE_WAR")
    assert out.status is OutcomeStatus.CONFIRMED
    assert ("send_diplomatic_action", (1, "DECLARE_SURPRISE_WAR")) in game.calls
    game = FakeGame()
    game.war_uncertain = True
    out = _execute(game, "diplo:1:DECLARE_SURPRISE_WAR")
    assert out.status is OutcomeStatus.PENDING


def test_precheck_rejects_an_action_the_engine_withdrew():
    game = FakeGame()
    obs = LiveObserver(game)
    core = asyncio.run(obs.core())
    point, _, _ = _point(obs, core)
    cand = next(
        c for c in point.candidates if c.candidate_id == "diplo:1:DIPLOMATIC_DELEGATION"
    )
    game.civs[0].available_actions = []
    out = asyncio.run(
        Executor(game, sleep=_no_sleep).execute(
            cand, point, current_version=obs.version, turn=5, inputs=None
        )
    )
    assert out.status is OutcomeStatus.REJECTED and game.calls == []
    assert "not_available" in out.reason


def test_alliance_dispatches_and_confirms():
    game = FakeGame()
    game.civs[0].available_actions = ["MAKE_ALLIANCE"]
    out = _execute(game, "alliance:1:MILITARY")
    assert out.status is OutcomeStatus.CONFIRMED
    assert ("form_alliance", (1, "MILITARY")) in game.calls
