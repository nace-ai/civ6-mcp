"""Typed observations and the model-visible context built from them.

Model-visible data is a strict subset of what the player could see: own
empire totals, the decision subject, currently visible or previously revealed
tiles near it (fogged tiles lose units, owner and yields), the configured
objective, and recent confirmed outcomes. Rival scores, total map size and
other privileged evaluation data are never included.
"""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any

from civ_mcp import lua as lq
from civ_mcp.drex.candidates import DecisionCategory
from civ_mcp.drex.enumerate import pretty


@dataclass(frozen=True)
class DecisionSpec:
    """What the scheduler wants decided next: a category and one entity."""

    category: DecisionCategory
    entity: str  # "empire", "unit:<id>", "city:<id>", "player:<id>", "slot:<idx>"

    @property
    def entity_id(self) -> int | None:
        _, _, raw = self.entity.partition(":")
        return int(raw) if raw.lstrip("-").isdigit() else None


@dataclass
class Blocker:
    blocking_type: str
    message: str = ""


@dataclass
class CoreObservation:
    """Cheap per-step observation used by the scheduler."""

    version: str
    civ: str
    seed: int
    local_player_id: int
    overview: lq.GameOverview
    tech: lq.TechCivicStatus
    progress: lq.ProgressTypes
    cities: list[lq.CityInfo]
    units: list[lq.UnitInfo]
    diplomacy_sessions: list[lq.DiplomacySession] = field(default_factory=list)
    pending_deals: list[lq.PendingDeal] = field(default_factory=list)
    blockers: list[Blocker] = field(default_factory=list)

    @property
    def game_identity(self) -> tuple[str, int]:
        return (self.civ, self.seed)

    @property
    def turn(self) -> int:
        return self.overview.turn

    def blocker_types(self) -> set[str]:
        return {b.blocking_type for b in self.blockers}

    def unit(self, unit_id: int) -> lq.UnitInfo | None:
        return next((u for u in self.units if u.unit_id == unit_id), None)

    def city(self, city_id: int) -> lq.CityInfo | None:
        return next((c for c in self.cities if c.city_id == city_id), None)


@dataclass
class DecisionInputs:
    """Entity-specific detail fetched for one decision."""

    unit: lq.UnitInfo | None = None
    action_space: lq.UnitActionSpace | None = None
    city: lq.CityInfo | None = None
    production_options: list[lq.ProductionOption] | None = None
    wonder_types: list[str] | None = None
    nearby_tiles: list[lq.TileInfo] | None = None
    session: lq.DiplomacySession | None = None
    deal: lq.PendingDeal | None = None
    policies: lq.GovernmentStatus | None = None
    slot_index: int | None = None
    governments: list[lq.GovernmentOption] | None = None
    envoys: lq.EnvoyStatus | None = None
    pantheon: lq.PantheonStatus | None = None


@dataclass
class Fact:
    turn: int
    category: str
    subject: str
    action: str
    result: str


class DecisionMemory:
    """Bounded structured record of recent outcomes, scoped to one game."""

    def __init__(self, max_facts: int = 12):
        self._facts: deque[Fact] = deque(maxlen=max_facts)
        self._game: tuple[str, int] | None = None

    def bind_game(self, identity: tuple[str, int]) -> None:
        if self._game is not None and identity != self._game:
            self._facts.clear()
        self._game = identity

    def record(self, fact: Fact) -> None:
        self._facts.append(fact)

    def recent(self, n: int | None = None) -> list[dict[str, Any]]:
        facts = list(self._facts)
        if n is not None:
            facts = facts[-n:]
        return [asdict(f) for f in facts]


def empire_summary(core: CoreObservation) -> dict[str, Any]:
    ov = core.overview
    return {
        "turn": ov.turn,
        "civilization": ov.civ_name,
        "era": ov.era_name,
        "gold": round(ov.gold),
        "gold_per_turn": round(ov.gold_per_turn, 1),
        "science_per_turn": round(ov.science_yield, 1),
        "culture_per_turn": round(ov.culture_yield, 1),
        "faith": round(ov.faith),
        "cities": len(core.cities),
        "units": len(core.units),
        "current_research": core.tech.current_research,
        "current_civic": core.tech.current_civic,
    }


def visible_tiles(tiles: list[lq.TileInfo], *, me: int) -> list[dict[str, Any]]:
    out = []
    for t in tiles:
        if t.visibility == "unexplored":
            continue
        tile: dict[str, Any] = {
            "xy": [t.x, t.y],
            "visibility": t.visibility,
            "terrain": pretty(t.terrain),
        }
        if t.resource:
            tile["resource"] = pretty(t.resource)
        if t.is_hills:
            tile["hills"] = True
        if t.is_river:
            tile["river"] = True
        if t.own_units:
            tile["own_units"] = list(t.own_units)
        # The map query reports current feature/improvement/district even
        # for fogged tiles, not what the player last saw, so they are only
        # shown for tiles that are visible now.
        if t.visibility == "visible":
            if t.feature:
                tile["feature"] = pretty(t.feature)
            if t.improvement:
                tile["improvement"] = pretty(t.improvement)
            if t.district:
                tile["district"] = pretty(t.district)
            if t.owner_id == me:
                tile["owner"] = "yours"
            elif t.owner_id >= 0:
                tile["owner"] = t.owner_name or "foreign"
            if t.units:
                tile["foreign_units"] = list(t.units)
            if t.yields:
                tile["yields"] = dict(
                    zip(
                        ("food", "production", "gold", "science", "culture", "faith"),
                        t.yields,
                    )
                )
        out.append(tile)
    return out


def _unit_subject(
    unit: lq.UnitInfo, space: lq.UnitActionSpace | None
) -> dict[str, Any]:
    subject: dict[str, Any] = {
        "unit": pretty(unit.unit_type),
        "position": [unit.x, unit.y],
        "hp": unit.health,
        "max_hp": unit.max_health,
        "moves_left": space.moves_remaining if space else unit.moves_remaining,
    }
    if unit.combat_strength:
        subject["combat_strength"] = unit.combat_strength
    if unit.ranged_strength:
        subject["ranged_strength"] = unit.ranged_strength
    if unit.build_charges:
        subject["charges"] = unit.build_charges
    if space and space.fortify_turns > 0:
        subject["fortified"] = True
    return subject


def _city_subject(city: lq.CityInfo) -> dict[str, Any]:
    return {
        "city": city.name,
        "position": [city.x, city.y],
        "population": city.population,
        "food_surplus": city.food_surplus,
        "production_per_turn": city.production,
        "turns_to_grow": city.turns_to_grow,
        "housing": city.housing,
        "amenities": city.amenities,
        "districts": [pretty(d) for d in city.districts],
        "buildings": [pretty(b) for b in city.buildings],
    }


def _deal_items(items: list[lq.DealItem]) -> list[dict[str, Any]]:
    return [
        {
            "item": i.name,
            "type": i.item_type.lower(),
            "amount": i.amount,
            "turns": i.duration,
        }
        for i in items
    ]


def build_context(
    spec: DecisionSpec,
    core: CoreObservation,
    inputs: DecisionInputs,
    memory: DecisionMemory,
    *,
    objective: str,
) -> dict[str, Any]:
    ctx: dict[str, Any] = {
        "objective": objective,
        "turn": core.turn,
        "empire": empire_summary(core),
        "decision": str(spec.category),
    }
    cat = spec.category
    if cat is DecisionCategory.UNIT and inputs.unit:
        ctx["subject"] = _unit_subject(inputs.unit, inputs.action_space)
    elif cat is DecisionCategory.PRODUCTION and inputs.city:
        ctx["subject"] = _city_subject(inputs.city)
    elif cat is DecisionCategory.DIPLOMACY and inputs.session:
        s = inputs.session
        ctx["subject"] = {
            "civilization": s.other_civ_name,
            "leader": s.other_leader_name,
            "dialogue": s.dialogue_text,
            "reason": s.reason_text,
            "visible_buttons": [b for b in s.buttons.split(";") if b],
        }
    elif cat is DecisionCategory.DEAL and inputs.deal:
        d = inputs.deal
        ctx["subject"] = {
            "civilization": d.other_player_name,
            "they_give": _deal_items(d.items_from_them),
            "we_give": _deal_items(d.items_from_us),
        }
    elif cat is DecisionCategory.POLICY and inputs.policies is not None:
        slot = next(
            (s for s in inputs.policies.slots if s.slot_index == inputs.slot_index),
            None,
        )
        ctx["subject"] = {
            "government": inputs.policies.government_name,
            "slot": pretty(slot.slot_type) if slot else None,
            "filled_slots": [
                s.current_policy_name or pretty(s.current_policy)
                for s in inputs.policies.slots
                if s.current_policy
            ],
        }
    elif cat is DecisionCategory.ENVOY and inputs.envoys is not None:
        ctx["subject"] = {"envoys_available": inputs.envoys.tokens_available}
    elif cat is DecisionCategory.PANTHEON and inputs.pantheon is not None:
        ctx["subject"] = {"faith": inputs.pantheon.faith_balance}
    if inputs.nearby_tiles:
        ctx["surroundings"] = visible_tiles(
            inputs.nearby_tiles, me=core.local_player_id
        )
    recent = memory.recent()
    if recent:
        ctx["recent_outcomes"] = recent
    return ctx
