"""Deterministic choice of what to decide next.

The scheduler picks one category and entity; Drex only chooses within that
entity's candidates. Order (documented because it affects results):

1. reactive diplomacy sessions (ascending player id)
2. incoming deals (ascending player id)
3. blocker-driven: government prompt, policy slots, envoys, pantheon
4. research, only when none is selected
5. civic, only when none is selected
6. production for empty queues (ascending city id)
7. units with moves left (ascending composite unit id)
8. end turn
"""

from __future__ import annotations

import dataclasses
from collections import Counter
from dataclasses import dataclass, field

from civ_mcp.drex.candidates import ActionKind, DecisionCategory
from civ_mcp.drex.executor import EMPTY_QUEUE_STATES, ActionOutcome, OutcomeStatus
from civ_mcp.drex.observation import (
    CLAIM_BLOCKER,
    PROMOTION_BLOCKER,
    CoreObservation,
    DecisionSpec,
)

SCHEDULER_ORDER = (
    "diplomacy: open sessions, ascending player id",
    "deal: pending incoming deals, ascending player id",
    "captured_city: when CONSIDER_RAZE_CITY / CONSIDER_DISLOYAL_CITY blocks (keep, raze, liberate, reject)",
    "spy_escape: when SPY_CHOOSE_ESCAPE_ROUTE / SPY_CHOOSE_DRAGNET_PRIORITY blocks (route per available district)",
    "artifact: when ARTIFACT blocks (credit the find to a claimant)",
    "government: when CONSIDER_GOVERNMENT_CHANGE blocks",
    "policy: when FILL_CIVIC_SLOT blocks, lowest empty slot",
    "envoy: when GIVE_INFLUENCE_TOKEN blocks",
    "pantheon: when PANTHEON blocks",
    "promotion: when UNIT_PROMOTION blocks, ascending unit id",
    "governor: when a GOVERNOR_* blocker stands (appoint / assign / promote)",
    "dedication: when COMMEMORATION_AVAILABLE blocks",
    "religion: when RELIGION blocks (religion, follower belief, then found)",
    "belief: when BELIEF blocks",
    "city_attack: when CITY/DISTRICT_RANGE_ATTACK blocks, per city, ascending id",
    "research: only when none selected",
    "civic: only when none selected",
    "production: empty queues, ascending city id",
    "city_attack: every city once per turn (attack a target or hold fire)",
    "great_person: forced claim, or once per turn when someone is claimable",
    "unit: moves left, ascending unit id, bounded decisions per unit",
    "end turn",
)

GOVERNMENT_BLOCKER = "ENDTURN_BLOCKING_CONSIDER_GOVERNMENT_CHANGE"
POLICY_BLOCKER = "ENDTURN_BLOCKING_FILL_CIVIC_SLOT"
ENVOY_BLOCKER = "ENDTURN_BLOCKING_GIVE_INFLUENCE_TOKEN"
PANTHEON_BLOCKER = "ENDTURN_BLOCKING_PANTHEON"
DEDICATION_BLOCKER = "ENDTURN_BLOCKING_COMMEMORATION_AVAILABLE"
RELIGION_BLOCKER = "ENDTURN_BLOCKING_RELIGION"
BELIEF_BLOCKER = "ENDTURN_BLOCKING_BELIEF"
CITY_ATTACK_BLOCKERS = frozenset(
    {"ENDTURN_BLOCKING_CITY_RANGE_ATTACK", "ENDTURN_BLOCKING_DISTRICT_RANGE_ATTACK"}
)
GOVERNOR_BLOCKERS = frozenset(
    {
        "ENDTURN_BLOCKING_GOVERNOR_APPOINTMENT",
        "ENDTURN_BLOCKING_GOVERNOR_IDLE",
        "ENDTURN_BLOCKING_GOVERNOR_OPPORTUNITY",
        "ENDTURN_BLOCKING_GOVERNOR_PROMOTION",
    }
)
CAPTURED_CITY_BLOCKERS = frozenset(
    {
        "ENDTURN_BLOCKING_CONSIDER_RAZE_CITY",
        "ENDTURN_BLOCKING_CONSIDER_DISLOYAL_CITY",
    }
)
SPY_ESCAPE_BLOCKERS = frozenset(
    {
        "ENDTURN_BLOCKING_SPY_CHOOSE_ESCAPE_ROUTE",
        # the Base UI opens the same escape popup for the dragnet prompt
        "ENDTURN_BLOCKING_SPY_CHOOSE_DRAGNET_PRIORITY",
    }
)
ARTIFACT_BLOCKER = "ENDTURN_BLOCKING_ARTIFACT"
# Modal prompts: decided first, repeat while the blocker stands (bounded), and
# a blocker that stands once nothing is pending is dismissed as housekeeping.
PROMPT_BLOCKERS: tuple[tuple[frozenset[str], DecisionCategory], ...] = (
    (CAPTURED_CITY_BLOCKERS, DecisionCategory.CAPTURED_CITY),
    (SPY_ESCAPE_BLOCKERS, DecisionCategory.SPY_ESCAPE),
    (frozenset({ARTIFACT_BLOCKER}), DecisionCategory.ARTIFACT),
)
PROMPT_CATEGORIES = frozenset(c for _, c in PROMPT_BLOCKERS)

SUPPORTED_BLOCKERS = frozenset(
    {
        "ENDTURN_BLOCKING_UNITS",
        "ENDTURN_BLOCKING_UNIT_NEEDS_ORDERS",
        "ENDTURN_BLOCKING_STACKED_UNITS",
        "ENDTURN_BLOCKING_PRODUCTION",
        "ENDTURN_BLOCKING_RESEARCH",
        "ENDTURN_BLOCKING_CIVIC",
        GOVERNMENT_BLOCKER,
        POLICY_BLOCKER,
        ENVOY_BLOCKER,
        PANTHEON_BLOCKER,
        PROMOTION_BLOCKER,
        *GOVERNOR_BLOCKERS,
        DEDICATION_BLOCKER,
        CLAIM_BLOCKER,
        RELIGION_BLOCKER,
        BELIEF_BLOCKER,
        *CITY_ATTACK_BLOCKERS,
        *CAPTURED_CITY_BLOCKERS,
        *SPY_ESCAPE_BLOCKERS,
        ARTIFACT_BLOCKER,
    }
)
# Informational blockers that execute_end_turn clears and logs as housekeeping.
HOUSEKEEPING_BLOCKERS = frozenset({"ENDTURN_BLOCKING_WORLD_CONGRESS_LOOK"})
# Engine blockers whose decision kinds are scheduled for later phases (spec
# Section 8). Listing them here is a deliberate classification: the run waits
# on them (unsupported_blocker) rather than stopping, and
# tests/drex/test_blocker_coverage.py fails if the engine has a type nobody
# classified.
PHASE_LATER_BLOCKERS = frozenset(
    {
        "ENDTURN_BLOCKING_EMERGENCY_NEEDS_ATTENTION",
        "ENDTURN_BLOCKING_WORLD_CONGRESS_SESSION",
        "ENDTURN_BLOCKING_WORLD_CONGRESS_SPECIAL_SESSION",
    }
)


@dataclass
class EndTurn:
    pass


@dataclass
class TurnLedger:
    turn: int
    resolved: set[str] = field(default_factory=set)
    counts: Counter[str] = field(default_factory=Counter)
    failures: Counter[str] = field(default_factory=Counter)
    failed_candidates: set[str] = field(default_factory=set)
    decisions: int = 0
    full_observed: bool = False
    # Loop-guard state: the per-turn decision cap was hit (end the turn), the
    # sessions already offered "close the screen", players whose informational
    # session would not close, and how often a blocked end turn repeated.
    budget_hit: bool = False
    budget_logged: bool = False
    exit_offered: set[str] = field(default_factory=set)
    stuck_sessions: set[int] = field(default_factory=set)
    blocked_repeats: int = 0
    # Multi-step religion founding: choices stored between Drex decisions in
    # the same turn (dropped with the ledger on a new turn).
    religion_partial: dict[str, str] = field(default_factory=dict)
    great_people_offered: bool = False
    dismissed_blockers: set[str] = field(default_factory=set)
    # the city attack blocker was seen this turn and the per-city checks were
    # re-opened for it once (a blocker raised mid-turn is decided, not dismissed)
    attack_reopened: bool = False


def key_for(spec: DecisionSpec) -> str:
    if spec.category in (
        DecisionCategory.RESEARCH,
        DecisionCategory.CIVIC,
        DecisionCategory.GOVERNMENT,
        DecisionCategory.POLICY,
        DecisionCategory.ENVOY,
        DecisionCategory.PANTHEON,
        DecisionCategory.GOVERNOR,
        DecisionCategory.DEDICATION,
        DecisionCategory.GREAT_PERSON,
        DecisionCategory.RELIGION,
        DecisionCategory.BELIEF,
        *PROMPT_CATEGORIES,
    ):
        return str(spec.category)
    return f"{spec.category}:{spec.entity_id}"


def _claimable(core: CoreObservation) -> bool:
    """Someone in the Great People pool can be recruited or bought now."""
    people = core.great_people or []
    gold, faith = core.overview.gold, core.overview.faith
    return any(
        gp.claimant == "Unclaimed"
        and (
            gp.can_recruit or (0 < gp.gold_cost <= gold) or (0 < gp.faith_cost <= faith)
        )
        for gp in people
    )


def _informational(session, deal_players: set[int]) -> bool:
    """Goodbye phases, and sessions from players we are at war with that carry
    no deal (war declarations cannot be declined). ``is_at_war`` describes the
    relationship, not the session, so an at-war session with a deal (e.g. a
    peace offer) is a decision."""
    if session.buttons == "GOODBYE":
        return True
    return (
        session.is_at_war
        and not session.deal_summary
        and session.other_player_id not in deal_players
    )


class Scheduler:
    def __init__(
        self,
        *,
        max_unit_decisions: int = 3,
        max_decisions_per_turn: int = 80,
        max_failures_per_key: int = 2,
        max_diplomacy_rounds: int = 4,
        max_repeat_decisions: int = 6,
        max_governor_decisions: int = 5,
    ):
        self.max_unit_decisions = max_unit_decisions
        self.max_decisions_per_turn = max_decisions_per_turn
        self.max_failures_per_key = max_failures_per_key
        self.max_diplomacy_rounds = max_diplomacy_rounds
        self.max_repeat_decisions = max_repeat_decisions
        self.max_governor_decisions = max_governor_decisions

    def _limit(self, spec: DecisionSpec) -> int | None:
        match spec.category:
            case DecisionCategory.UNIT:
                return self.max_unit_decisions
            case DecisionCategory.DIPLOMACY:
                return self.max_diplomacy_rounds
            case DecisionCategory.POLICY | DecisionCategory.ENVOY:
                return self.max_repeat_decisions
            case DecisionCategory.PROMOTION:
                return 2
            case DecisionCategory.GOVERNOR:
                return self.max_governor_decisions
            case DecisionCategory.RELIGION:
                return 6  # two stored steps + found, with room to re-pick once
            case DecisionCategory.DEDICATION:
                return 3  # Heroic Ages allow up to three dedications
        if spec.category in PROMPT_CATEGORIES:
            return 4  # a second captured city the same turn; never unbounded
        return None

    def _open(self, ledger: TurnLedger, spec: DecisionSpec) -> bool:
        key = key_for(spec)
        if key in ledger.resolved:
            return False
        limit = self._limit(spec)
        return limit is None or ledger.counts[key] < limit

    def informational_sessions(
        self, core: CoreObservation, exclude: set[int] | frozenset[int] = frozenset()
    ) -> list[int]:
        deal_players = {d.other_player_id for d in core.pending_deals}
        return sorted(
            s.other_player_id
            for s in core.diplomacy_sessions
            if _informational(s, deal_players) and s.other_player_id not in exclude
        )

    def peek(
        self, core: CoreObservation, ledger: TurnLedger, after: DecisionSpec
    ) -> DecisionSpec | EndTurn:
        """What ``next`` would return once ``after`` is resolved, without
        touching the real ledger (used to prefetch the next decision's reads)."""
        trial = dataclasses.replace(
            ledger,
            resolved=set(ledger.resolved) | {key_for(after)},
            counts=Counter(ledger.counts),
            failures=Counter(ledger.failures),
            failed_candidates=set(ledger.failed_candidates),
            exit_offered=set(ledger.exit_offered),
            stuck_sessions=set(ledger.stuck_sessions),
        )
        return self.next(core, trial)

    def session_exhausted(self, ledger: TurnLedger, spec: DecisionSpec) -> bool:
        """True once the failure budget for this session is spent: the next
        decision also offers Drex "close the screen"."""
        return key_for(spec) in ledger.exit_offered

    def stale_blockers(self, core: CoreObservation, ledger: TurnLedger) -> list[str]:
        """Supported blockers that stand although nothing is left to decide:
        a promotion flag with no promotable unit, or a city attack prompt once
        every city has attacked or held fire. These are engine leftovers the
        runner dismisses as housekeeping (once per turn)."""
        blockers = core.blocker_types()
        stale: list[str] = []
        if PROMOTION_BLOCKER in blockers and not core.promotable:
            stale.append(PROMOTION_BLOCKER)
        attack = blockers & CITY_ATTACK_BLOCKERS
        if (
            attack
            and ledger.attack_reopened
            and all(
                not self._open(
                    ledger,
                    DecisionSpec(DecisionCategory.CITY_ATTACK, f"city:{c.city_id}"),
                )
                for c in core.cities
            )
        ):
            stale.extend(sorted(attack))
        for group, category in PROMPT_BLOCKERS:
            standing = blockers & group
            if standing and not self._open(ledger, DecisionSpec(category, "empire")):
                stale.extend(sorted(standing))
        return [b for b in stale if b not in ledger.dismissed_blockers]

    def unsupported_blockers(self, core: CoreObservation) -> list[str]:
        return sorted(
            b
            for b in core.blocker_types()
            if b not in SUPPORTED_BLOCKERS and b not in HOUSEKEEPING_BLOCKERS
        )

    def next(self, core: CoreObservation, ledger: TurnLedger) -> DecisionSpec | EndTurn:
        reactive = self.next_reactive(core, ledger)
        if reactive is not None:
            return reactive
        return self._next_proactive(core, ledger)

    def next_reactive(
        self, core: CoreObservation, ledger: TurnLedger
    ) -> DecisionSpec | None:
        """Diplomacy sessions and incoming deals only (safe while an end turn
        is in flight); None when neither needs a decision."""
        if ledger.decisions >= self.max_decisions_per_turn:
            ledger.budget_hit = True
            return None

        deal_players = {d.other_player_id for d in core.pending_deals}
        for s in sorted(core.diplomacy_sessions, key=lambda s: s.other_player_id):
            pid = s.other_player_id
            if _informational(s, deal_players) or pid in deal_players:
                continue
            # A session carrying a deal but no pending deal items is decided
            # through the dialogue itself (accept / reject).
            spec = DecisionSpec(DecisionCategory.DIPLOMACY, f"player:{pid}")
            if self._open(ledger, spec):
                return spec
            key = key_for(spec)
            if key not in ledger.exit_offered:
                # Failure budget spent: one more decision, now including
                # "close the screen", then the session is left alone.
                ledger.exit_offered.add(key)
                return spec
            continue

        for d in sorted(core.pending_deals, key=lambda d: d.other_player_id):
            spec = DecisionSpec(DecisionCategory.DEAL, f"player:{d.other_player_id}")
            if self._open(ledger, spec):
                return spec
        return None

    def _next_proactive(
        self, core: CoreObservation, ledger: TurnLedger
    ) -> DecisionSpec | EndTurn:
        if ledger.budget_hit:
            return EndTurn()
        blockers = core.blocker_types()
        for group, category in PROMPT_BLOCKERS:
            if blockers & group:
                spec = DecisionSpec(category, "empire")
                if self._open(ledger, spec):
                    return spec

        for blocker, category in (
            (GOVERNMENT_BLOCKER, DecisionCategory.GOVERNMENT),
            (POLICY_BLOCKER, DecisionCategory.POLICY),
            (ENVOY_BLOCKER, DecisionCategory.ENVOY),
            (PANTHEON_BLOCKER, DecisionCategory.PANTHEON),
        ):
            spec = DecisionSpec(category, "empire")
            if blocker in blockers and self._open(ledger, spec):
                return spec

        if blockers & GOVERNOR_BLOCKERS:
            spec = DecisionSpec(DecisionCategory.GOVERNOR, "empire")
            if self._open(ledger, spec):
                return spec

        if DEDICATION_BLOCKER in blockers:
            spec = DecisionSpec(DecisionCategory.DEDICATION, "empire")
            if self._open(ledger, spec):
                return spec

        if CLAIM_BLOCKER in blockers:
            spec = DecisionSpec(DecisionCategory.GREAT_PERSON, "empire")
            if self._open(ledger, spec):
                return spec

        if RELIGION_BLOCKER in blockers:
            spec = DecisionSpec(DecisionCategory.RELIGION, "empire")
            if self._open(ledger, spec):
                return spec
        elif ledger.religion_partial:
            # the founding prompt is gone: partial choices are stale
            ledger.religion_partial.clear()

        if BELIEF_BLOCKER in blockers:
            spec = DecisionSpec(DecisionCategory.BELIEF, "empire")
            if self._open(ledger, spec):
                return spec

        if blockers & CITY_ATTACK_BLOCKERS:
            if not ledger.attack_reopened:
                # A blocker raised after this turn's proactive checks means new
                # targets: let every city decide again, once.
                ledger.attack_reopened = True
                for city in core.cities:
                    ledger.resolved.discard(
                        key_for(
                            DecisionSpec(
                                DecisionCategory.CITY_ATTACK, f"city:{city.city_id}"
                            )
                        )
                    )
            for city in sorted(core.cities, key=lambda c: c.city_id):
                spec = DecisionSpec(
                    DecisionCategory.CITY_ATTACK, f"city:{city.city_id}"
                )
                if self._open(ledger, spec):
                    return spec

        if PROMOTION_BLOCKER in blockers:
            for pu in sorted(core.promotable, key=lambda u: u.unit_id):
                spec = DecisionSpec(DecisionCategory.PROMOTION, f"unit:{pu.unit_id}")
                if self._open(ledger, spec):
                    return spec

        if core.progress.research_type is None:
            spec = DecisionSpec(DecisionCategory.RESEARCH, "empire")
            if self._open(ledger, spec):
                return spec
        if core.progress.civic_type is None:
            spec = DecisionSpec(DecisionCategory.CIVIC, "empire")
            if self._open(ledger, spec):
                return spec

        for city in sorted(core.cities, key=lambda c: c.city_id):
            if city.currently_building in EMPTY_QUEUE_STATES:
                spec = DecisionSpec(DecisionCategory.PRODUCTION, f"city:{city.city_id}")
                if self._open(ledger, spec):
                    return spec

        # Cities may fire their ranged attack whether or not the engine raises a
        # blocker for it (the Base ruleset never does): one check per city per
        # turn; with no targets it resolves as a forced hold fire, no Drex call.
        for city in sorted(core.cities, key=lambda c: c.city_id):
            spec = DecisionSpec(DecisionCategory.CITY_ATTACK, f"city:{city.city_id}")
            if self._open(ledger, spec):
                return spec

        if not ledger.great_people_offered and _claimable(core):
            spec = DecisionSpec(DecisionCategory.GREAT_PERSON, "empire")
            if self._open(ledger, spec):
                return spec

        for unit in sorted(core.units, key=lambda u: u.unit_id):
            if unit.moves_remaining > 0:
                spec = DecisionSpec(DecisionCategory.UNIT, f"unit:{unit.unit_id}")
                if self._open(ledger, spec):
                    return spec
        return EndTurn()

    def note(
        self,
        ledger: TurnLedger,
        spec: DecisionSpec,
        kind: ActionKind | None,
        outcome: ActionOutcome,
        candidate_id: str | None = None,
    ) -> None:
        key = key_for(spec)
        ledger.decisions += 1
        ledger.counts[key] += 1
        if spec.category is DecisionCategory.GREAT_PERSON:
            # one Great People decision per turn, whatever its outcome
            ledger.great_people_offered = True
        if outcome.status not in (OutcomeStatus.CONFIRMED, OutcomeStatus.PENDING):
            ledger.failures[key] += 1
            if candidate_id:
                ledger.failed_candidates.add(candidate_id)
            if ledger.failures[key] >= self.max_failures_per_key:
                ledger.resolved.add(key)
            return
        if spec.category is DecisionCategory.UNIT:
            if kind is not ActionKind.MOVE_UNIT:
                ledger.resolved.add(key)
        elif spec.category not in (
            DecisionCategory.DIPLOMACY,
            DecisionCategory.POLICY,
            DecisionCategory.ENVOY,
            DecisionCategory.GOVERNOR,
            DecisionCategory.RELIGION,  # resolved by the runner after FOUND_RELIGION
            DecisionCategory.DEDICATION,  # repeats while the blocker stands
            *PROMPT_CATEGORIES,  # a second prompt of the same kind this turn
        ):
            ledger.resolved.add(key)

    def final_unit_decision(self, ledger: TurnLedger, spec: DecisionSpec) -> bool:
        """True when this is the unit's last permitted decision this turn.

        The caller then offers only turn-ending orders, so a unit cannot be
        left awake with moves (which would block the turn) once its budget
        is spent.
        """
        return (
            spec.category is DecisionCategory.UNIT
            and ledger.counts[key_for(spec)] >= self.max_unit_decisions - 1
        )

    def exhaust(self, ledger: TurnLedger, spec: DecisionSpec) -> None:
        """Nothing left to offer for this key this turn."""
        ledger.resolved.add(key_for(spec))
