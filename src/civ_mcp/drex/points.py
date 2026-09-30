"""Assemble one DecisionPoint: candidates, exclusions and model-visible context."""

from __future__ import annotations

import dataclasses

from civ_mcp import lua as lq
from civ_mcp.drex.candidates import (
    ActionKind,
    Candidate,
    DecisionCategory,
    DecisionPoint,
    Exclusion,
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
    foreign_policy_candidates,
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
    shortlist,
    trade_route_candidates,
    unit_candidates,
)
from civ_mcp.drex.observation import (
    CLAIM_BLOCKER,
    CoreObservation,
    DecisionInputs,
    DecisionMemory,
    DecisionSpec,
    build_context,
)

QUESTIONS: dict[DecisionCategory, str] = {
    DecisionCategory.RESEARCH: "Which technology should the empire research next?",
    DecisionCategory.CIVIC: "Which civic should the empire progress next?",
    DecisionCategory.PRODUCTION: "What should this city produce next?",
    DecisionCategory.UNIT: "What should this unit do now?",
    DecisionCategory.DIPLOMACY: "How should we respond to this leader?",
    DecisionCategory.DEAL: "Should we accept this trade deal?",
    DecisionCategory.POLICY: "Which policy card should fill this government slot?",
    DecisionCategory.ENVOY: "Which city-state should receive our envoy?",
    DecisionCategory.GOVERNMENT: "Should the empire change its government now?",
    DecisionCategory.PANTHEON: "Which pantheon belief should the empire adopt?",
    DecisionCategory.PROMOTION: "Which promotion should this unit take?",
    DecisionCategory.GOVERNOR: "Which governor action should the empire take?",
    DecisionCategory.DEDICATION: "Which dedication should the empire make for this era?",
    DecisionCategory.GREAT_PERSON: "Should the empire claim a Great Person now, and how?",
    DecisionCategory.RELIGION: "Which choice should the empire make to found its religion?",
    DecisionCategory.BELIEF: "Which belief should the empire add to its religion?",
    DecisionCategory.CITY_ATTACK: "Should this city fire its ranged attack, and at whom?",
    DecisionCategory.CAPTURED_CITY: "What should the empire do with this captured city?",
    DecisionCategory.SPY_ESCAPE: "Which escape route should the caught spy take?",
    DecisionCategory.ARTIFACT: "Which civilization should this artifact be credited to?",
    DecisionCategory.PURCHASE: "Should the empire spend gold now, and on what?",
    DecisionCategory.FOREIGN_POLICY: "Which diplomatic move should the empire make this turn?",
}


def _policy_slot(status: lq.GovernmentStatus) -> lq.PolicySlot | None:
    for slot in sorted(status.slots, key=lambda s: s.slot_index):
        if slot.current_policy is None and policy_candidates(status, slot):
            return slot
    return None


def _enumerate(
    spec: DecisionSpec,
    core: CoreObservation,
    inputs: DecisionInputs,
    *,
    allow_exit: bool = False,
) -> tuple[list[Candidate], list[Exclusion], str, DecisionInputs]:
    cat = spec.category
    entity = spec.entity
    if cat is DecisionCategory.RESEARCH:
        return research_candidates(core.tech), [], entity, inputs
    if cat is DecisionCategory.CIVIC:
        return civic_candidates(core.tech), [], entity, inputs
    if (
        cat is DecisionCategory.PRODUCTION
        and inputs.city
        and inputs.production_options is not None
    ):
        cands, excl = production_candidates(
            inputs.city,
            inputs.production_options,
            set(inputs.wonder_types or ()),
            placements=inputs.placements,
            placement_errors=inputs.placement_errors,
        )
        return cands, excl, entity, inputs
    if cat is DecisionCategory.UNIT and inputs.unit and inputs.action_space:
        cands, excl = unit_candidates(
            inputs.action_space,
            inputs.unit,
            me=core.local_player_id,
            gold=core.overview.gold,
        )
        cands = cands + trade_route_candidates(
            inputs.unit,
            inputs.action_space,
            inputs.trade_status,
            inputs.trade_destinations,
        )
        return cands, excl, entity, inputs
    if cat is DecisionCategory.DIPLOMACY and inputs.session:
        cands = diplomacy_candidates(inputs.session, allow_exit=allow_exit)
        return cands, [], entity, inputs
    if cat is DecisionCategory.DEAL and inputs.deal:
        return deal_candidates(inputs.deal), [], entity, inputs
    if cat is DecisionCategory.POLICY and inputs.policies is not None:
        slot = _policy_slot(inputs.policies)
        if slot is None:
            return [], [], entity, inputs
        inputs = dataclasses.replace(inputs, slot_index=slot.slot_index)
        return (
            policy_candidates(inputs.policies, slot),
            [],
            f"slot:{slot.slot_index}",
            inputs,
        )
    if cat is DecisionCategory.ENVOY and inputs.envoys is not None:
        return envoy_candidates(inputs.envoys), [], entity, inputs
    if cat is DecisionCategory.GOVERNMENT and inputs.governments is not None:
        current = next(
            (g.government_type for g in inputs.governments if g.is_current), "NONE"
        )
        return (
            government_candidates(inputs.governments, current_type=current),
            [],
            entity,
            inputs,
        )
    if cat is DecisionCategory.PANTHEON and inputs.pantheon is not None:
        return pantheon_candidates(inputs.pantheon), [], entity, inputs
    if (
        cat is DecisionCategory.CITY_ATTACK
        and inputs.city_attack_city is not None
        and inputs.city_targets is not None
    ):
        cands = city_attack_candidates(inputs.city_attack_city, inputs.city_targets)
        return cands, [], entity, inputs
    if cat is DecisionCategory.RELIGION and inputs.religion is not None:
        partial = inputs.religion_partial or {}
        return religion_candidates(inputs.religion, partial), [], entity, inputs
    if cat is DecisionCategory.BELIEF and inputs.religion is not None:
        return belief_candidates(inputs.religion), [], entity, inputs
    if cat is DecisionCategory.GREAT_PERSON and inputs.great_people is not None:
        forced = CLAIM_BLOCKER in core.blocker_types()
        cands = great_person_candidates(
            inputs.great_people,
            core.overview.gold,
            core.overview.faith,
            forced=forced,
        )
        return cands, [], entity, inputs
    if cat is DecisionCategory.DEDICATION and inputs.dedications is not None:
        return dedication_candidates(inputs.dedications), [], entity, inputs
    if cat is DecisionCategory.GOVERNOR and inputs.governors is not None:
        return governor_candidates(inputs.governors, core.cities), [], entity, inputs
    if cat is DecisionCategory.PROMOTION and inputs.promotable and inputs.promotions:
        return (
            promotion_candidates(inputs.promotable, inputs.promotions),
            [],
            entity,
            inputs,
        )
    if cat is DecisionCategory.CAPTURED_CITY and inputs.captured_city is not None:
        return captured_city_candidates(inputs.captured_city), [], entity, inputs
    if cat is DecisionCategory.SPY_ESCAPE and inputs.spy_escape is not None:
        return escape_route_candidates(inputs.spy_escape), [], entity, inputs
    if cat is DecisionCategory.ARTIFACT and inputs.artifact is not None:
        return artifact_candidates(inputs.artifact), [], entity, inputs
    if cat is DecisionCategory.PURCHASE and inputs.purchase_options is not None:
        cands, excl = purchase_candidates(
            core.cities,
            inputs.purchase_options,
            core.overview.gold,
            set(inputs.wonder_types or ()),
        )
        return cands, excl, entity, inputs
    if cat is DecisionCategory.FOREIGN_POLICY and inputs.civs is not None:
        strength = getattr(core.overview, "military_strength", 0) or 0
        return foreign_policy_candidates(inputs.civs, strength), [], entity, inputs
    return [], [Exclusion(entity, f"no inputs for {cat}")], entity, inputs


def build_decision_point(
    spec: DecisionSpec,
    core: CoreObservation,
    inputs: DecisionInputs,
    memory: DecisionMemory,
    *,
    objective: str,
    decision_id: str,
    max_options: int,
    failed: frozenset[str] | set[str] = frozenset(),
    decided_prefixes: frozenset[str] | set[str] = frozenset(),
    exclude_kinds: frozenset[ActionKind] = frozenset(),
    allow_exit: bool = False,
) -> tuple[DecisionPoint | None, list[Exclusion]]:
    candidates, excluded, entity, inputs = _enumerate(
        spec, core, inputs, allow_exit=allow_exit
    )
    legal = len(candidates)
    untried = []
    for c in candidates:
        if c.candidate_id in failed:
            excluded.append(Exclusion(c.candidate_id, "failed earlier this turn"))
        elif any(c.candidate_id.startswith(pfx) for pfx in decided_prefixes):
            excluded.append(
                Exclusion(
                    c.candidate_id, "already decided this turn; awaiting the engine"
                )
            )
        else:
            untried.append(c)
    exit_only = (
        allow_exit
        and len(untried) == 1
        and getattr(untried[0].params, "response", None) == "EXIT"
    )
    if legal >= 2 and len(untried) < 2 and not exit_only:
        excluded.extend(
            Exclusion(c.candidate_id, "no untried alternative after earlier failures")
            for c in untried
        )
        return None, excluded
    kept = []
    for c in untried:
        if c.kind in exclude_kinds:
            excluded.append(
                Exclusion(
                    c.candidate_id,
                    "final decision for this unit this turn: turn-ending orders only",
                )
            )
        else:
            kept.append(c)
    forced_rule = None
    if len(kept) == 1 and len(untried) > 1:
        forced_rule = "forced_turn_ending_order"
    elif exit_only:
        # Every response failed; closing the screen is the housekeeping act
        # the controller already performs for informational sessions.
        forced_rule = "forced_close_screen"
    kept, cut = shortlist(kept, max_options)
    excluded.extend(cut)
    if not kept:
        return None, excluded
    point = DecisionPoint.create(
        decision_id=decision_id,
        category=spec.category,
        entity=entity,
        observation_version=core.version,
        question=QUESTIONS[spec.category],
        candidates=kept,
        context=build_context(spec, core, inputs, memory, objective=objective),
        exclusions=excluded,
        legal_count=legal,
        forced_rule=forced_rule,
    )
    return point, excluded
