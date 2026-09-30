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
    ArtifactParams,
    AssignGovernorParams,
    AttackParams,
    BeliefParams,
    Candidate,
    CapturedCityParams,
    CityAttackParams,
    CivicParams,
    DealParams,
    DedicationParams,
    DiplomacyParams,
    EnvoyParams,
    EscapeRouteParams,
    Exclusion,
    FoundReligionParams,
    GovernmentParams,
    GreatPersonParams,
    HoldFireParams,
    ImproveParams,
    KeepGovernmentParams,
    MoveParams,
    PantheonParams,
    PolicyParams,
    ProductionParams,
    PromoteGovernorParams,
    PromoteParams,
    PurchaseParams,
    ReligionChoiceParams,
    ResearchParams,
    SaveGoldParams,
    TradeRouteParams,
    UnitOrderParams,
    UnitRef,
    UpgradeParams,
    WaitParams,
)
from civ_mcp.drex.hexgrid import hex_distance

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


def _placement_candidates(
    city: lq.CityInfo,
    opt: lq.ProductionOption,
    tiles: list[Any],
    per_item: int,
) -> list[Candidate]:
    """One candidate per advisor tile (best first) for a district or wonder."""
    out: list[Candidate] = []
    name = pretty(opt.item_name)
    for tile in tiles[:per_item]:
        params = ProductionParams(
            city_id=city.city_id,
            item_type=opt.category,
            item_name=opt.item_name,
            target_x=tile.x,
            target_y=tile.y,
        )
        facts: dict = {
            "category": opt.category.lower(),
            "cost": opt.cost,
            "turns": opt.turns,
            "repair": False,
            "tile": tile.note,
            "score": tile.score,
        }
        out.append(
            Candidate.create(
                ActionKind.SET_PRODUCTION,
                params,
                label=f"{name} at ({tile.x},{tile.y})",
                facts=facts,
            )
        )
    return out


def production_candidates(
    city: lq.CityInfo,
    options: list[lq.ProductionOption],
    wonder_types: set[str],
    placements: dict[str, list[Any]] | None = None,
    placement_errors: dict[str, str] | None = None,
    per_item: int = 3,
) -> tuple[list[Candidate], list[Exclusion]]:
    """Offer every buildable item. Districts and wonders need a tile: with
    ``placements`` (advisor tiles per item) each gets one candidate per top
    tile; without, they are excluded as before. Advisor errors exclude the
    item with the engine's reason.

    District repairs carry their own coordinates and are offered. A building
    listed both normally and as a repair is offered once, as the repair.
    """
    by_id: dict[str, Candidate] = {}
    excluded: list[Exclusion] = []
    errors = placement_errors or {}
    for opt in options:
        target: tuple[int, int] | None = None
        needs_tile = (opt.category == "DISTRICT" and not opt.is_repair) or (
            opt.category == "BUILDING"
            and opt.item_name in wonder_types
            and not opt.is_repair
        )
        if needs_tile:
            if placements is None and opt.item_name not in errors:
                what = "district" if opt.category == "DISTRICT" else "wonder"
                excluded.append(
                    Exclusion(opt.item_name, f"{what} requires placement (unsupported)")
                )
                continue
            if opt.item_name in errors:
                excluded.append(
                    Exclusion(opt.item_name, f"placement: {errors[opt.item_name]}")
                )
                continue
            tiles = (placements or {}).get(opt.item_name) or []
            if not tiles:
                excluded.append(Exclusion(opt.item_name, "placement: no valid tile"))
                continue
            for cand in _placement_candidates(city, opt, tiles, per_item):
                by_id.setdefault(cand.candidate_id, cand)
            continue
        if opt.category == "DISTRICT":
            if opt.repair_x is None:
                excluded.append(
                    Exclusion(opt.item_name, "district repair without coordinates")
                )
                continue
            target = (opt.repair_x, opt.repair_y)
        elif opt.category not in ("UNIT", "PROJECT", "BUILDING"):
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
    space: lq.UnitActionSpace,
    unit: lq.UnitInfo,
    *,
    me: int,
    gold: float | None = None,
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

    if unit.can_upgrade and unit.upgrade_target:
        target = unit.upgrade_target
        if not same_tile:
            excluded.append(
                Exclusion(
                    f"upgrade to {target}", "unit moved since upgrade was validated"
                )
            )
        elif gold is not None and gold < unit.upgrade_cost:
            excluded.append(
                Exclusion(
                    f"upgrade to {target}",
                    f"costs {unit.upgrade_cost} gold, treasury {int(gold)}",
                )
            )
        else:
            out.append(
                Candidate.create(
                    ActionKind.UPGRADE_UNIT,
                    UpgradeParams(unit=ref, target_type=target, cost=unit.upgrade_cost),
                    label=f"Upgrade to {pretty(target.replace('UNIT_', ''))} ({unit.upgrade_cost} gold)",
                    facts={
                        "cost": unit.upgrade_cost,
                        "treasury": None if gold is None else int(gold),
                        "from": pretty(unit.unit_type.replace("UNIT_", "")),
                    },
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


def great_person_candidates(
    people: list[lq.GreatPersonInfo], gold: float, faith: float, *, forced: bool
) -> list[Candidate]:
    """Recruit with points, patronize with gold or faith, or wait. "Wait" is a
    real choice and is only dropped when the engine forces a claim."""
    out: list[Candidate] = []
    for gp in people:
        if gp.claimant != "Unclaimed":
            continue
        facts = {
            "class": gp.class_name,
            "era": gp.era_name,
            "ability": gp.ability,
            "points": f"{gp.player_points}/{gp.cost}",
        }
        if gp.can_recruit:
            out.append(
                Candidate.create(
                    ActionKind.RECRUIT_GREAT_PERSON,
                    GreatPersonParams(gp.individual_id, gp.individual_name),
                    label=f"Recruit {gp.individual_name} ({gp.class_name})",
                    facts=facts,
                )
            )
        if 0 < gp.gold_cost <= gold:
            out.append(
                Candidate.create(
                    ActionKind.PATRONIZE_GREAT_PERSON,
                    GreatPersonParams(
                        gp.individual_id, gp.individual_name, "YIELD_GOLD"
                    ),
                    label=f"Patronize {gp.individual_name} with {gp.gold_cost} gold",
                    facts={**facts, "price": f"{gp.gold_cost} gold of {gold:.0f}"},
                )
            )
        if 0 < gp.faith_cost <= faith:
            out.append(
                Candidate.create(
                    ActionKind.PATRONIZE_GREAT_PERSON,
                    GreatPersonParams(
                        gp.individual_id, gp.individual_name, "YIELD_FAITH"
                    ),
                    label=f"Patronize {gp.individual_name} with {gp.faith_cost} faith",
                    facts={**facts, "price": f"{gp.faith_cost} faith of {faith:.0f}"},
                )
            )
    if out and not forced:
        out.append(
            Candidate.create(
                ActionKind.WAIT_GREAT_PERSON,
                WaitParams("great_people"),
                label="Wait: keep accumulating points and treasury",
                facts={},
            )
        )
    return out


def religion_candidates(
    status: lq.ReligionFoundingStatus, partial: dict[str, str]
) -> list[Candidate]:
    """Founding a religion in three Drex steps: the religion, one follower
    belief, then the founder belief (which dispatches ``found_religion`` with
    the two stored choices). Each step stays under the option limit."""
    if status.has_religion:
        return []
    religion = partial.get("religion_type")
    follower = partial.get("follower_belief")
    if religion is None:
        return [
            Candidate.create(
                ActionKind.CHOOSE_RELIGION,
                ReligionChoiceParams(rtype),
                label=name or pretty(rtype),
                facts={"step": "1 of 3: religion"},
            )
            for rtype, name in status.available_religions
        ]
    if follower is None:
        return [
            Candidate.create(
                ActionKind.CHOOSE_FOLLOWER_BELIEF,
                BeliefParams(b.belief_type, b.belief_class),
                label=b.name or pretty(b.belief_type),
                facts={"effect": b.description, "step": "2 of 3: follower belief"},
            )
            for b in status.beliefs_by_class.get("BELIEF_CLASS_FOLLOWER", [])
        ]
    return [
        Candidate.create(
            ActionKind.FOUND_RELIGION,
            FoundReligionParams(religion, follower, b.belief_type),
            label=b.name or pretty(b.belief_type),
            facts={
                "effect": b.description,
                "step": "3 of 3: founder belief, then found",
                "religion": religion,
                "follower_belief": follower,
            },
        )
        for b in status.beliefs_by_class.get("BELIEF_CLASS_FOUNDER", [])
    ]


def belief_candidates(status: lq.ReligionFoundingStatus) -> list[Candidate]:
    """Add a belief to an existing religion: every available belief of every
    class the engine still offers."""
    # A founded religion already holds its founder and follower beliefs; the
    # engine only lets it add the enhancer and worship classes.
    addable = (
        {"BELIEF_CLASS_ENHANCER", "BELIEF_CLASS_WORSHIP"}
        if status.has_religion
        else set(status.beliefs_by_class)
    )
    out: list[Candidate] = []
    for cls in sorted(status.beliefs_by_class):
        if cls not in addable:
            continue
        for b in status.beliefs_by_class[cls]:
            out.append(
                Candidate.create(
                    ActionKind.ADD_BELIEF,
                    BeliefParams(b.belief_type, cls),
                    label=f"{b.name or pretty(b.belief_type)} ({pretty(cls.replace('BELIEF_CLASS_', ''))})",
                    facts={"effect": b.description, "class": cls},
                )
            )
    return out


def city_attack_candidates(city: lq.CityInfo, targets: list[Any]) -> list[Candidate]:
    """One ranged attack per engine-listed target, plus holding fire. With no
    targets only "hold fire" remains, which the single-legal-option rule
    executes without a Drex call."""
    out: list[Candidate] = [
        Candidate.create(
            ActionKind.CITY_ATTACK,
            CityAttackParams(city.city_id, t.x, t.y, t.unit_type),
            label=f"Attack {pretty(t.unit_type.replace('UNIT_', ''))} at ({t.x},{t.y})",
            facts={
                "unit": t.unit_type,
                "owner": t.owner_id,
                "hp": f"{t.hp}/{t.max_hp}",
                "distance": hex_distance(city.x, city.y, t.x, t.y),
            },
        )
        for t in targets
    ]
    out.append(
        Candidate.create(
            ActionKind.HOLD_FIRE,
            HoldFireParams(city.city_id),
            label=f"Hold fire in {city.name}",
            facts={},
        )
    )
    return out


_CAPTURE_LABELS = {
    "keep": "Keep {name} as our city",
    "raze": "Raze {name} (burn it down over the coming turns)",
    "liberate_founder": "Liberate {name}: return it to its founder{orig}",
    "liberate_previous": "Liberate {name}: return it to its previous owner{prev}",
    "reject": "Reject {name} (refuse to take the city)",
}


def captured_city_candidates(city: Any) -> list[Candidate]:
    """One candidate per directive the engine accepts for the pending city
    (lua.drex_queries.CapturedCity.options), in canonical order."""
    orig = f" ({city.original_owner})" if city.original_owner else ""
    prev = f" ({city.previous_owner})" if city.previous_owner else ""
    return [
        Candidate.create(
            ActionKind.RESOLVE_CAPTURED_CITY,
            CapturedCityParams(city.city_id, city.name, action),
            label=_CAPTURE_LABELS[action].format(name=city.name, orig=orig, prev=prev),
            facts={
                "population": city.population,
                "districts": city.districts,
                "source": city.source,
                "founder": city.original_owner or None,
                "previous_owner": city.previous_owner or None,
            },
        )
        for action in city.options
        if action in _CAPTURE_LABELS
    ]


def escape_route_candidates(choice: Any) -> list[Candidate]:
    """One candidate per escape district the city has (fastest first, as the
    game's popup lists them)."""
    return [
        Candidate.create(
            ActionKind.CHOOSE_ESCAPE_ROUTE,
            EscapeRouteParams(choice.spy_unit_id, choice.spy_name, d),
            label=f"{choice.spy_name} escapes through the {pretty(d.replace('DISTRICT_', ''))}",
            facts={"city": choice.city_name, "route_rank": i + 1},
        )
        for i, d in enumerate(choice.routes)
    ]


def artifact_candidates(choice: Any) -> list[Candidate]:
    """One candidate per civilization the artifact can be credited to (the
    acting player, plus the target when the engine offers a choice)."""
    return [
        Candidate.create(
            ActionKind.CHOOSE_ARTIFACT_PLAYER,
            ArtifactParams(choice.unit_id, pid, name),
            label=f"Credit the artifact to {name}",
            facts={"role": role, "origin": choice.kind, "era": choice.era},
        )
        for pid, name, role in choice.players
    ]


def purchase_candidates(
    cities: list[lq.CityInfo],
    options_by_city: dict[int, list[lq.ProductionOption]],
    gold: float,
    wonder_types: set[str],
) -> tuple[list[Candidate], list[Exclusion]]:
    """Every affordable unit or building purchase in every city (the engine's
    ``gold_cost``; wonders and repairs are never purchasable) plus one "save
    the gold" option. With nothing affordable only "save" remains and the
    single-legal-option rule executes it without a Drex call."""
    out: list[Candidate] = []
    excluded: list[Exclusion] = []
    for city in sorted(cities, key=lambda c: c.city_id):
        for opt in options_by_city.get(city.city_id, []):
            if (
                opt.gold_cost < 0
                or opt.is_repair
                or opt.category not in ("UNIT", "BUILDING")
                or opt.item_name in wonder_types
            ):
                continue
            if opt.gold_cost > gold:
                excluded.append(
                    Exclusion(
                        f"{city.name}: {opt.item_name}",
                        f"costs {opt.gold_cost} gold, treasury {int(gold)}",
                    )
                )
                continue
            out.append(
                Candidate.create(
                    ActionKind.PURCHASE_ITEM,
                    PurchaseParams(
                        city.city_id,
                        city.name,
                        opt.category,
                        opt.item_name,
                        opt.gold_cost,
                    ),
                    label=f"Buy {pretty(opt.item_name)} in {city.name} ({opt.gold_cost} gold)",
                    facts={
                        "city": city.name,
                        "gold_cost": opt.gold_cost,
                        "treasury": int(gold),
                        "production_turns": opt.turns,
                    },
                )
            )
    out.append(
        Candidate.create(
            ActionKind.SAVE_GOLD,
            SaveGoldParams(),
            label="Save the gold (buy nothing this turn)",
            facts={"treasury": int(gold)},
        )
    )
    return out, excluded


def trade_route_candidates(
    unit: lq.UnitInfo,
    space: lq.UnitActionSpace,
    status: lq.TradeRouteStatus | None,
    destinations: list[lq.TradeDestination] | None,
) -> list[Candidate]:
    """One route per destination the engine offers this trader, only while a
    route slot is free and the trader is not already travelling."""
    if status is None or not destinations:
        return []
    if status.capacity <= status.active_count:
        return []
    me = next((t for t in status.traders if t.unit_id == unit.unit_id), None)
    if me is not None and me.on_route:
        return []
    ref = UnitRef(space.unit_id, space.unit_index, space.unit_type, space.x, space.y)
    return [
        Candidate.create(
            ActionKind.MAKE_TRADE_ROUTE,
            TradeRouteParams(ref, d.x, d.y, d.city_name, d.owner_name),
            label=f"Trade route to {d.city_name} ({d.owner_name})",
            facts={
                "domestic": d.is_domestic,
                "city_state": d.is_city_state,
                "quest": d.has_quest,
                "trading_post": d.has_trading_post,
                "distance": hex_distance(space.x, space.y, d.x, d.y),
            },
        )
        for d in destinations
    ]


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
    # Placement candidates (districts/wonders with a tile) give way first,
    # worst advisor score first, so plain items are never crowded out.
    placed = sorted(
        (
            c
            for c in candidates
            if "score" in c.facts and c.kind is ActionKind.SET_PRODUCTION
        ),
        key=lambda c: (c.facts["score"], c.candidate_id),
    )
    over = len(candidates) - limit
    if placed and over > 0:
        drop_ids = {c.candidate_id for c in placed[:over]}
        kept = [c for c in candidates if c.candidate_id not in drop_ids]
        dropped = [
            Exclusion(
                c.candidate_id, f"over option limit {limit} (lowest placement score)"
            )
            for c in candidates
            if c.candidate_id in drop_ids
        ]
        if len(kept) <= limit:
            return kept, dropped
        more_kept, more_dropped = shortlist(kept, limit)
        return more_kept, dropped + more_dropped
    # Purchases give way next, most expensive first; "save the gold" stays.
    buys = sorted(
        (c for c in candidates if c.kind is ActionKind.PURCHASE_ITEM),
        key=lambda c: (-c.params.gold_cost, c.candidate_id),
    )
    over = len(candidates) - limit
    if buys and over > 0:
        drop_ids = {c.candidate_id for c in buys[:over]}
        kept = [c for c in candidates if c.candidate_id not in drop_ids]
        dropped = [
            Exclusion(
                c.candidate_id, f"over option limit {limit} (most expensive purchase)"
            )
            for c in candidates
            if c.candidate_id in drop_ids
        ]
        if len(kept) <= limit:
            return kept, dropped
        more_kept, more_dropped = shortlist(kept, limit)
        return more_kept, dropped + more_dropped
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
