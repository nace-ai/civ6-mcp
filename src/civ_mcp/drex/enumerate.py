"""Enumerate executable candidates from existing typed observations.

Pure functions: no game or network access. Anything the controller does not
offer is returned as an ``Exclusion`` with a reason so the omission is logged.
"""

from __future__ import annotations

from typing import Any

from civ_mcp import lua as lq
from civ_mcp.drex.candidates import (
    ActionKind,
    AppointGovernorParams,
    AssignGovernorParams,
    AttackParams,
    Candidate,
    CivicParams,
    DealParams,
    DedicationParams,
    DiplomacyParams,
    EnvoyParams,
    Exclusion,
    GovernmentParams,
    ImproveParams,
    KeepGovernmentParams,
    MoveParams,
    PantheonParams,
    PolicyParams,
    ProductionParams,
    PromoteGovernorParams,
    PromoteParams,
    ResearchParams,
    UnitOrderParams,
    UnitRef,
)

_PREFIXES = (
    "UNIT_",
    "BUILDING_",
    "DISTRICT_",
    "PROJECT_",
    "IMPROVEMENT_",
    "TECHNOLOGY_",
    "CIVIC_",
    "POLICY_",
    "GOVERNMENT_",
    "BELIEF_",
    "TERRAIN_",
    "FEATURE_",
    "RESOURCE_",
)


def pretty(type_name: str) -> str:
    for prefix in _PREFIXES:
        if type_name.startswith(prefix):
            type_name = type_name[len(prefix) :]
            break
    return type_name.replace("_", " ").title()


def research_candidates(status: lq.TechCivicStatus) -> list[Candidate]:
    return [
        Candidate.create(
            ActionKind.SET_RESEARCH,
            ResearchParams(tech_type=t.tech_type),
            label=t.name or pretty(t.tech_type),
            facts={
                "cost": t.cost,
                "turns": t.turns,
                "progress_pct": t.progress_pct,
                "boosted": t.boosted,
                "boost_condition": t.boost_desc,
                "unlocks": t.unlocks,
                "era": t.era,
            },
        )
        for t in status.available_techs
    ]


def civic_candidates(status: lq.TechCivicStatus) -> list[Candidate]:
    return [
        Candidate.create(
            ActionKind.SET_CIVIC,
            CivicParams(civic_type=c.civic_type),
            label=c.name or pretty(c.civic_type),
            facts={
                "cost": c.cost,
                "turns": c.turns,
                "progress_pct": c.progress_pct,
                "boosted": c.boosted,
                "boost_condition": c.boost_desc,
                "era": c.era,
            },
        )
        for c in status.available_civics
    ]


def production_candidates(
    city: lq.CityInfo,
    options: list[lq.ProductionOption],
    wonder_types: set[str],
) -> tuple[list[Candidate], list[Exclusion]]:
    """Offer items that need no placement; districts/wonders are excluded.

    District repairs carry their own coordinates and are offered. A building
    listed both normally and as a repair is offered once, as the repair.
    """
    by_id: dict[str, Candidate] = {}
    excluded: list[Exclusion] = []
    for opt in options:
        target: tuple[int, int] | None = None
        if opt.category == "DISTRICT":
            if not (opt.is_repair and opt.repair_x is not None):
                excluded.append(
                    Exclusion(
                        opt.item_name, "district requires placement (unsupported)"
                    )
                )
                continue
            target = (opt.repair_x, opt.repair_y)
        elif opt.category == "BUILDING":
            if opt.item_name in wonder_types and not opt.is_repair:
                excluded.append(
                    Exclusion(opt.item_name, "wonder requires placement (unsupported)")
                )
                continue
        elif opt.category not in ("UNIT", "PROJECT"):
            excluded.append(
                Exclusion(opt.item_name, f"unknown production category {opt.category}")
            )
            continue
        params = ProductionParams(
            city_id=city.city_id,
            item_type=opt.category,
            item_name=opt.item_name,
            target_x=target[0] if target else None,
            target_y=target[1] if target else None,
        )
        name = pretty(opt.item_name)
        label = (
            f"Repair {name}" + (f" at ({target[0]},{target[1]})" if target else "")
            if opt.is_repair
            else f"{name} ({opt.category.lower()})"
        )
        facts: dict = {
            "category": opt.category.lower(),
            "cost": opt.cost,
            "turns": opt.turns,
            "repair": opt.is_repair,
        }
        if opt.gold_cost >= 0:
            facts["gold_cost"] = opt.gold_cost
        cand = Candidate.create(
            ActionKind.SET_PRODUCTION, params, label=label, facts=facts
        )
        existing = by_id.get(cand.candidate_id)
        if existing is None or (opt.is_repair and not existing.facts["repair"]):
            by_id[cand.candidate_id] = cand
    return list(by_id.values()), excluded


def _owner(owner_id: int, me: int) -> str:
    if owner_id == -2:
        return "unknown"
    if owner_id == -1:
        return "unowned"
    return "yours" if owner_id == me else "foreign"


def unit_candidates(
    space: lq.UnitActionSpace, unit: lq.UnitInfo, *, me: int
) -> tuple[list[Candidate], list[Exclusion]]:
    """Orders for one unit from its engine-reported action space.

    Improvements come from the units query, which validates them only on the
    builder's own tile, so they are dropped if the unit has since moved.
    """
    if space.moves_remaining <= 0:
        return [], []
    ref = UnitRef(
        unit_id=space.unit_id,
        unit_index=space.unit_index,
        unit_type=space.unit_type,
        x=space.x,
        y=space.y,
    )
    out: list[Candidate] = []
    excluded: list[Exclusion] = []

    for t in space.reachable:
        option = f"move to ({t.x},{t.y})"
        if t.own_stack_conflict:
            excluded.append(
                Exclusion(option, "friendly unit of the same class on tile")
            )
            continue
        if t.visible_foreign_unit:
            excluded.append(Exclusion(option, "visible foreign unit on tile"))
            continue
        out.append(
            Candidate.create(
                ActionKind.MOVE_UNIT,
                MoveParams(unit=ref, to_x=t.x, to_y=t.y),
                label=f"Move to ({t.x},{t.y})",
                facts={
                    "terrain": pretty(t.terrain),
                    "feature": pretty(t.feature)
                    if t.feature and t.visibility == "visible"
                    else None,
                    "resource": pretty(t.resource) if t.resource else None,
                    "hills": t.is_hills,
                    "river": t.is_river,
                    "owner": _owner(t.owner_id, me),
                    "visibility": t.visibility,
                    "distance": t.distance,
                },
            )
        )

    for tg in space.targets:
        out.append(
            Candidate.create(
                ActionKind.ATTACK,
                AttackParams(
                    unit=ref,
                    target_x=tg.x,
                    target_y=tg.y,
                    attack_type=tg.attack_type,
                    target_unit_type=tg.unit_type,
                    target_owner_id=tg.owner_id,
                ),
                label=f"Attack {tg.owner_name} {pretty(tg.unit_type)} at ({tg.x},{tg.y})",
                facts={
                    "attack_type": tg.attack_type.lower(),
                    "target_hp": tg.hp,
                    "target_max_hp": tg.max_hp,
                    "target_combat_strength": tg.combat_strength,
                    "distance": tg.distance,
                },
            )
        )

    order = UnitOrderParams(unit=ref)
    if space.can_found:
        out.append(
            Candidate.create(
                ActionKind.FOUND_CITY,
                order,
                label=f"Found a city here ({ref.x},{ref.y})",
            )
        )

    same_tile = (unit.x, unit.y) == (space.x, space.y)
    for imp in unit.valid_improvements:
        if not imp.startswith("IMPROVEMENT_"):
            excluded.append(Exclusion(imp, "not a tile improvement (unsupported)"))
            continue
        if not same_tile:
            excluded.append(
                Exclusion(imp, "unit moved since improvements were validated")
            )
            continue
        out.append(
            Candidate.create(
                ActionKind.IMPROVE_TILE,
                ImproveParams(unit=ref, improvement_type=imp),
                label=f"Build {pretty(imp)} here",
                facts={"charges_left": unit.build_charges},
            )
        )

    if space.can_fortify and space.fortify_turns == 0:
        out.append(Candidate.create(ActionKind.FORTIFY_UNIT, order, label="Fortify"))
    if space.can_heal:
        out.append(
            Candidate.create(
                ActionKind.HEAL_UNIT,
                order,
                label="Heal (fortify until healed)",
                facts={"hp": space.hp, "max_hp": space.max_hp},
            )
        )
    out.append(
        Candidate.create(ActionKind.SKIP_UNIT, order, label="Skip turn (stay here)")
    )
    return out, excluded


def diplomacy_candidates(
    session: lq.DiplomacySession, *, allow_exit: bool = False
) -> list[Candidate]:
    """POSITIVE/NEGATIVE are the responses DiplomacyManager.AddResponse accepts.

    War declarations and goodbye phases have no meaningful choice. A session
    that carries a deal summary is decided here as accept / reject of that
    deal. With ``allow_exit`` (the session's failure budget is spent) Drex is
    also offered closing the screen, so a stuck dialogue never stops the run.
    """
    if session.is_at_war or session.buttons == "GOODBYE":
        return []
    if session.deal_summary:
        labels = (("POSITIVE", "Accept the deal"), ("NEGATIVE", "Reject the deal"))
    else:
        labels = (
            ("POSITIVE", "Respond positively"),
            ("NEGATIVE", "Respond negatively"),
        )
    if allow_exit:
        labels = (*labels, ("EXIT", "Close the screen"))
    return [
        Candidate.create(
            ActionKind.DIPLOMACY_RESPOND,
            DiplomacyParams(other_player_id=session.other_player_id, response=resp),
            label=label,
        )
        for resp, label in labels
    ]


def deal_candidates(deal: lq.PendingDeal) -> list[Candidate]:
    return [
        Candidate.create(
            ActionKind.DEAL_RESPOND,
            DealParams(other_player_id=deal.other_player_id, accept=accept),
            label=label,
        )
        for accept, label in ((True, "Accept the deal"), (False, "Reject the deal"))
    ]


def policy_candidates(
    status: lq.GovernmentStatus, slot: lq.PolicySlot
) -> list[Candidate]:
    slotted = {s.current_policy for s in status.slots if s.current_policy}
    out = []
    for p in status.available_policies:
        if p.policy_type in slotted:
            continue
        if slot.slot_type != "SLOT_WILDCARD" and p.slot_type != slot.slot_type:
            continue
        out.append(
            Candidate.create(
                ActionKind.SET_POLICY,
                PolicyParams(slot_index=slot.slot_index, policy_type=p.policy_type),
                label=p.name or pretty(p.policy_type),
                facts={"effect": p.description, "slot_type": pretty(p.slot_type)},
            )
        )
    return out


def envoy_candidates(status: lq.EnvoyStatus) -> list[Candidate]:
    if status.tokens_available <= 0:
        return []
    return [
        Candidate.create(
            ActionKind.SEND_ENVOY,
            EnvoyParams(city_state_player_id=cs.player_id),
            label=cs.name or f"City-state {cs.player_id}",
            facts={
                "type": cs.city_state_type,
                "envoys_already_sent": cs.envoys_sent,
                "suzerain": cs.suzerain_name,
            },
        )
        for cs in status.city_states
        if cs.can_send_envoy
    ]


def government_candidates(
    options: list[lq.GovernmentOption], *, current_type: str
) -> list[Candidate]:
    current = next((g for g in options if g.is_current), None)
    keep_name = current.name if current else "no government"
    out = [
        Candidate.create(
            ActionKind.KEEP_GOVERNMENT,
            KeepGovernmentParams(current_government_type=current_type),
            label=f"Keep {keep_name}",
        )
    ]
    for g in options:
        if g.is_current or g.government_type == current_type:
            continue
        out.append(
            Candidate.create(
                ActionKind.CHANGE_GOVERNMENT,
                GovernmentParams(government_type=g.government_type),
                label=f"Adopt {g.name}",
                facts={"slots": [pretty(s) for s in g.slots], "bonus": g.bonus},
            )
        )
    return out


def promotion_candidates(unit: Any, status: lq.UnitPromotionStatus) -> list[Candidate]:
    """One candidate per promotion the engine offers this unit."""
    ref = UnitRef(unit.unit_id, unit.unit_index, unit.unit_type, -1, -1)
    return [
        Candidate.create(
            ActionKind.PROMOTE_UNIT,
            PromoteParams(ref, p.promotion_type),
            label=p.name or pretty(p.promotion_type),
            facts={"effect": p.description},
        )
        for p in status.promotions
    ]


def governor_candidates(
    status: lq.GovernorStatus, cities: list[lq.CityInfo]
) -> list[Candidate]:
    """Every governor action the engine allows right now: appoint (points
    available), assign an unplaced governor to a city without one, promote
    (points available)."""
    out: list[Candidate] = []
    if status.can_appoint and status.points_available > 0:
        for g in status.available_to_appoint:
            out.append(
                Candidate.create(
                    ActionKind.APPOINT_GOVERNOR,
                    AppointGovernorParams(g.governor_type),
                    label=f"Appoint {g.name} ({g.title})",
                    facts={"ability": g.base_ability, "effect": g.base_ability_desc},
                )
            )
    governed = {
        g.assigned_city_id for g in status.appointed if g.assigned_city_id != -1
    }
    for g in status.appointed:
        if g.assigned_city_id == -1:
            for city in sorted(cities, key=lambda c: c.city_id):
                if city.city_id in governed:
                    continue
                out.append(
                    Candidate.create(
                        ActionKind.ASSIGN_GOVERNOR,
                        AssignGovernorParams(g.governor_type, city.city_id),
                        label=f"Assign {g.name} to {city.name}",
                        facts={"city": city.name, "population": city.population},
                    )
                )
        if status.points_available > 0:
            for pr in g.available_promotions:
                out.append(
                    Candidate.create(
                        ActionKind.PROMOTE_GOVERNOR,
                        PromoteGovernorParams(g.governor_type, pr.promotion_type),
                        label=f"Promote {g.name}: {pr.name}",
                        facts={"effect": pr.description, "level": pr.level},
                    )
                )
    return out


def dedication_candidates(status: lq.DedicationStatus) -> list[Candidate]:
    """One candidate per dedication not yet active, described for the current age."""
    out: list[Candidate] = []
    for ch in status.choices:
        if ch.name in status.active:
            continue
        if status.age_type in ("Golden", "Heroic"):
            bonus = ch.golden_desc
        elif status.age_type == "Dark":
            bonus = ch.dark_desc
        else:
            bonus = ch.normal_desc
        out.append(
            Candidate.create(
                ActionKind.CHOOSE_DEDICATION,
                DedicationParams(ch.index, ch.name),
                label=pretty(ch.name.replace("COMMEMORATION_", "")),
                facts={"bonus": bonus, "age": status.age_type},
            )
        )
    return out


def pantheon_candidates(status: lq.PantheonStatus) -> list[Candidate]:
    if status.has_pantheon:
        return []
    return [
        Candidate.create(
            ActionKind.CHOOSE_PANTHEON,
            PantheonParams(belief_type=b.belief_type),
            label=b.name or pretty(b.belief_type),
            facts={"effect": b.description},
        )
        for b in status.available_beliefs
    ]


def shortlist(
    candidates: list[Candidate], limit: int
) -> tuple[list[Candidate], list[Exclusion]]:
    """Deterministic cut to the selector's option limit.

    Non-move actions come first (in input order, up to the limit); remaining
    room goes to moves nearest-first (distance, then candidate id).
    """
    if len(candidates) <= limit:
        return list(candidates), []
    fixed = [c for c in candidates if c.kind is not ActionKind.MOVE_UNIT]
    moves = sorted(
        (c for c in candidates if c.kind is ActionKind.MOVE_UNIT),
        key=lambda c: (c.facts.get("distance", 0), c.candidate_id),
    )
    room = max(0, limit - len(fixed))
    kept = fixed[:limit] + moves[:room]
    kept_ids = {c.candidate_id for c in kept}
    dropped = [
        Exclusion(
            c.candidate_id, f"over option limit {limit} (deterministic shortlist)"
        )
        for c in candidates
        if c.candidate_id not in kept_ids
    ]
    return kept, dropped
