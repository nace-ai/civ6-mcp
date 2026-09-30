"""Typed candidate actions and decision points.

A candidate's ``candidate_id`` is derived from its action content, so the same
legal action keeps the same identity regardless of enumeration order. The
``label`` and ``facts`` are what the model sees; ``kind`` + ``params`` are what
the executor dispatches.
"""

from __future__ import annotations

import dataclasses
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class DecisionCategory(StrEnum):
    RESEARCH = "research"
    CIVIC = "civic"
    PRODUCTION = "production"
    UNIT = "unit"
    DIPLOMACY = "diplomacy"
    DEAL = "deal"
    POLICY = "policy"
    ENVOY = "envoy"
    GOVERNMENT = "government"
    PANTHEON = "pantheon"
    PROMOTION = "promotion"
    GOVERNOR = "governor"
    DEDICATION = "dedication"
    GREAT_PERSON = "great_person"
    RELIGION = "religion"
    BELIEF = "belief"
    CITY_ATTACK = "city_attack"
    CAPTURED_CITY = "captured_city"
    SPY_ESCAPE = "spy_escape"
    ARTIFACT = "artifact"
    PURCHASE = "purchase"
    FOREIGN_POLICY = "foreign_policy"


class ActionKind(StrEnum):
    SET_RESEARCH = "set_research"
    SET_CIVIC = "set_civic"
    SET_PRODUCTION = "set_production"
    MOVE_UNIT = "move_unit"
    ATTACK = "attack"
    FOUND_CITY = "found_city"
    IMPROVE_TILE = "improve_tile"
    FORTIFY_UNIT = "fortify_unit"
    HEAL_UNIT = "heal_unit"
    SKIP_UNIT = "skip_unit"
    DIPLOMACY_RESPOND = "diplomacy_respond"
    DEAL_RESPOND = "deal_respond"
    SET_POLICY = "set_policy"
    SEND_ENVOY = "send_envoy"
    CHANGE_GOVERNMENT = "change_government"
    KEEP_GOVERNMENT = "keep_government"
    CHOOSE_PANTHEON = "choose_pantheon"
    PROMOTE_UNIT = "promote_unit"
    APPOINT_GOVERNOR = "appoint_governor"
    ASSIGN_GOVERNOR = "assign_governor"
    PROMOTE_GOVERNOR = "promote_governor"
    CHOOSE_DEDICATION = "choose_dedication"
    RECRUIT_GREAT_PERSON = "recruit_great_person"
    PATRONIZE_GREAT_PERSON = "patronize_great_person"
    WAIT_GREAT_PERSON = "wait_great_person"  # no dispatch: a real "wait" choice
    CHOOSE_RELIGION = "choose_religion"  # no dispatch: stored for found_religion
    CHOOSE_FOLLOWER_BELIEF = "choose_follower_belief"  # no dispatch: stored
    FOUND_RELIGION = "found_religion"
    ADD_BELIEF = "add_belief"
    CITY_ATTACK = "city_attack"
    HOLD_FIRE = "hold_fire"  # no dispatch
    MAKE_TRADE_ROUTE = "make_trade_route"
    RESOLVE_CAPTURED_CITY = "resolve_captured_city"
    CHOOSE_ESCAPE_ROUTE = "choose_escape_route"
    CHOOSE_ARTIFACT_PLAYER = "choose_artifact_player"
    UPGRADE_UNIT = "upgrade_unit"
    PURCHASE_ITEM = "purchase_item"
    SAVE_GOLD = "save_gold"  # no dispatch: a real "buy nothing" choice
    DIPLOMATIC_ACTION = "diplomatic_action"
    PROPOSE_PEACE = "propose_peace"
    FORM_ALLIANCE = "form_alliance"
    NO_DIPLOMACY = "no_diplomacy"  # no dispatch: a real "do nothing" choice


@dataclass(frozen=True)
class UnitRef:
    """Identity and position of the acting unit when the candidate was built.

    ``unit_id`` is the composite ID (owner * 65536 + index); most GameState
    unit actions take ``unit_index``.
    """

    unit_id: int
    unit_index: int
    unit_type: str
    x: int
    y: int


@dataclass(frozen=True)
class ResearchParams:
    tech_type: str


@dataclass(frozen=True)
class CivicParams:
    civic_type: str


@dataclass(frozen=True)
class ProductionParams:
    city_id: int
    item_type: str  # UNIT / BUILDING / DISTRICT / PROJECT
    item_name: str
    target_x: int | None = None
    target_y: int | None = None


@dataclass(frozen=True)
class MoveParams:
    unit: UnitRef
    to_x: int
    to_y: int


@dataclass(frozen=True)
class AttackParams:
    unit: UnitRef
    target_x: int
    target_y: int
    attack_type: str  # MELEE / RANGED
    target_unit_type: str
    target_owner_id: int


@dataclass(frozen=True)
class UnitOrderParams:
    unit: UnitRef


@dataclass(frozen=True)
class ImproveParams:
    unit: UnitRef
    improvement_type: str


@dataclass(frozen=True)
class DiplomacyParams:
    other_player_id: int
    response: str  # POSITIVE / NEGATIVE


@dataclass(frozen=True)
class DealParams:
    other_player_id: int
    accept: bool


@dataclass(frozen=True)
class PolicyParams:
    slot_index: int
    policy_type: str


@dataclass(frozen=True)
class EnvoyParams:
    city_state_player_id: int


@dataclass(frozen=True)
class GovernmentParams:
    government_type: str


@dataclass(frozen=True)
class KeepGovernmentParams:
    current_government_type: str


@dataclass(frozen=True)
class PantheonParams:
    belief_type: str


@dataclass(frozen=True)
class PromoteParams:
    unit: UnitRef
    promotion_type: str


@dataclass(frozen=True)
class AppointGovernorParams:
    governor_type: str


@dataclass(frozen=True)
class AssignGovernorParams:
    governor_type: str
    city_id: int


@dataclass(frozen=True)
class PromoteGovernorParams:
    governor_type: str
    promotion_type: str


@dataclass(frozen=True)
class DedicationParams:
    index: int
    name: str


@dataclass(frozen=True)
class GreatPersonParams:
    individual_id: int
    individual_name: str
    yield_type: str | None = None  # None = recruit with points; else patronize


@dataclass(frozen=True)
class WaitParams:
    subject: str


@dataclass(frozen=True)
class ReligionChoiceParams:
    religion_type: str


@dataclass(frozen=True)
class BeliefParams:
    belief_type: str
    belief_class: str


@dataclass(frozen=True)
class FoundReligionParams:
    religion_type: str
    follower_belief: str
    founder_belief: str


@dataclass(frozen=True)
class CityAttackParams:
    city_id: int
    target_x: int
    target_y: int
    target_unit_type: str


@dataclass(frozen=True)
class HoldFireParams:
    city_id: int


@dataclass(frozen=True)
class TradeRouteParams:
    unit: UnitRef
    target_x: int
    target_y: int
    city_name: str
    owner_name: str


@dataclass(frozen=True)
class CapturedCityParams:
    city_id: int
    city_name: str
    action: str  # one of lua.drex_queries.CAPTURE_ACTIONS


@dataclass(frozen=True)
class EscapeRouteParams:
    spy_unit_id: int
    spy_name: str
    district_type: str


@dataclass(frozen=True)
class ArtifactParams:
    archaeologist_unit_id: int
    player_id: int
    player_name: str


@dataclass(frozen=True)
class UpgradeParams:
    unit: UnitRef
    target_type: str
    cost: int


@dataclass(frozen=True)
class PurchaseParams:
    city_id: int
    city_name: str
    item_type: str  # UNIT / BUILDING
    item_name: str
    gold_cost: int


@dataclass(frozen=True)
class SaveGoldParams:
    pass


@dataclass(frozen=True)
class DiplomaticActionParams:
    player_id: int
    civ_name: str
    action: str  # DIPLOMATIC_DELEGATION, DECLARE_FRIENDSHIP, RESIDENT_EMBASSY,
    # DENOUNCE, OPEN_BORDERS, DECLARE_SURPRISE_WAR, DECLARE_FORMAL_WAR


@dataclass(frozen=True)
class PeaceParams:
    player_id: int
    civ_name: str


@dataclass(frozen=True)
class AllianceParams:
    player_id: int
    civ_name: str
    alliance_type: str


@dataclass(frozen=True)
class NoDiplomacyParams:
    pass


ActionParams = (
    ResearchParams
    | CivicParams
    | ProductionParams
    | MoveParams
    | AttackParams
    | UnitOrderParams
    | ImproveParams
    | DiplomacyParams
    | DealParams
    | PolicyParams
    | EnvoyParams
    | GovernmentParams
    | KeepGovernmentParams
    | PantheonParams
    | PromoteParams
    | AppointGovernorParams
    | AssignGovernorParams
    | PromoteGovernorParams
    | DedicationParams
    | GreatPersonParams
    | WaitParams
    | ReligionChoiceParams
    | BeliefParams
    | FoundReligionParams
    | CityAttackParams
    | HoldFireParams
    | TradeRouteParams
    | CapturedCityParams
    | EscapeRouteParams
    | ArtifactParams
    | UpgradeParams
    | PurchaseParams
    | SaveGoldParams
    | DiplomaticActionParams
    | PeaceParams
    | AllianceParams
    | NoDiplomacyParams
)

PARAMS_FOR_KIND: dict[ActionKind, type] = {
    ActionKind.SET_RESEARCH: ResearchParams,
    ActionKind.SET_CIVIC: CivicParams,
    ActionKind.SET_PRODUCTION: ProductionParams,
    ActionKind.MOVE_UNIT: MoveParams,
    ActionKind.ATTACK: AttackParams,
    ActionKind.FOUND_CITY: UnitOrderParams,
    ActionKind.IMPROVE_TILE: ImproveParams,
    ActionKind.FORTIFY_UNIT: UnitOrderParams,
    ActionKind.HEAL_UNIT: UnitOrderParams,
    ActionKind.SKIP_UNIT: UnitOrderParams,
    ActionKind.DIPLOMACY_RESPOND: DiplomacyParams,
    ActionKind.DEAL_RESPOND: DealParams,
    ActionKind.SET_POLICY: PolicyParams,
    ActionKind.SEND_ENVOY: EnvoyParams,
    ActionKind.CHANGE_GOVERNMENT: GovernmentParams,
    ActionKind.KEEP_GOVERNMENT: KeepGovernmentParams,
    ActionKind.CHOOSE_PANTHEON: PantheonParams,
    ActionKind.PROMOTE_UNIT: PromoteParams,
    ActionKind.APPOINT_GOVERNOR: AppointGovernorParams,
    ActionKind.ASSIGN_GOVERNOR: AssignGovernorParams,
    ActionKind.PROMOTE_GOVERNOR: PromoteGovernorParams,
    ActionKind.CHOOSE_DEDICATION: DedicationParams,
    ActionKind.RECRUIT_GREAT_PERSON: GreatPersonParams,
    ActionKind.PATRONIZE_GREAT_PERSON: GreatPersonParams,
    ActionKind.WAIT_GREAT_PERSON: WaitParams,
    ActionKind.CHOOSE_RELIGION: ReligionChoiceParams,
    ActionKind.CHOOSE_FOLLOWER_BELIEF: BeliefParams,
    ActionKind.FOUND_RELIGION: FoundReligionParams,
    ActionKind.ADD_BELIEF: BeliefParams,
    ActionKind.CITY_ATTACK: CityAttackParams,
    ActionKind.HOLD_FIRE: HoldFireParams,
    ActionKind.MAKE_TRADE_ROUTE: TradeRouteParams,
    ActionKind.RESOLVE_CAPTURED_CITY: CapturedCityParams,
    ActionKind.CHOOSE_ESCAPE_ROUTE: EscapeRouteParams,
    ActionKind.CHOOSE_ARTIFACT_PLAYER: ArtifactParams,
    ActionKind.UPGRADE_UNIT: UpgradeParams,
    ActionKind.PURCHASE_ITEM: PurchaseParams,
    ActionKind.SAVE_GOLD: SaveGoldParams,
    ActionKind.DIPLOMATIC_ACTION: DiplomaticActionParams,
    ActionKind.PROPOSE_PEACE: PeaceParams,
    ActionKind.FORM_ALLIANCE: AllianceParams,
    ActionKind.NO_DIPLOMACY: NoDiplomacyParams,
}


def candidate_id_for(kind: ActionKind, params: ActionParams) -> str:
    """Stable identity derived from the action's executable content."""
    match kind, params:
        case ActionKind.SET_RESEARCH, ResearchParams(tech_type=t):
            return f"research:{t}"
        case ActionKind.SET_CIVIC, CivicParams(civic_type=c):
            return f"civic:{c}"
        case ActionKind.SET_PRODUCTION, ProductionParams() as p:
            at = "" if p.target_x is None else f"@{p.target_x},{p.target_y}"
            return f"produce:{p.city_id}:{p.item_type}:{p.item_name}{at}"
        case ActionKind.MOVE_UNIT, MoveParams(unit=u, to_x=x, to_y=y):
            return f"move:{u.unit_id}:{x},{y}"
        case ActionKind.ATTACK, AttackParams(unit=u, target_x=x, target_y=y):
            return f"attack:{u.unit_id}:{x},{y}"
        case ActionKind.IMPROVE_TILE, ImproveParams(unit=u, improvement_type=i):
            return f"improve:{u.unit_id}:{i}"
        case (
            (
                ActionKind.FOUND_CITY
                | ActionKind.FORTIFY_UNIT
                | ActionKind.HEAL_UNIT
                | ActionKind.SKIP_UNIT
            ),
            UnitOrderParams(unit=u),
        ):
            prefix = {
                ActionKind.FOUND_CITY: "found",
                ActionKind.FORTIFY_UNIT: "fortify",
                ActionKind.HEAL_UNIT: "heal",
                ActionKind.SKIP_UNIT: "skip",
            }[kind]
            return f"{prefix}:{u.unit_id}"
        case ActionKind.DIPLOMACY_RESPOND, DiplomacyParams(
            other_player_id=p, response=r
        ):
            return f"diplomacy:{p}:{r}"
        case ActionKind.DEAL_RESPOND, DealParams(other_player_id=p, accept=a):
            return f"deal:{p}:{'accept' if a else 'reject'}"
        case ActionKind.SET_POLICY, PolicyParams(slot_index=s, policy_type=t):
            return f"policy:{s}:{t}"
        case ActionKind.SEND_ENVOY, EnvoyParams(city_state_player_id=p):
            return f"envoy:{p}"
        case ActionKind.CHANGE_GOVERNMENT, GovernmentParams(government_type=g):
            return f"government:{g}"
        case ActionKind.KEEP_GOVERNMENT, KeepGovernmentParams():
            return "government:keep"
        case ActionKind.CHOOSE_PANTHEON, PantheonParams(belief_type=b):
            return f"pantheon:{b}"
        case ActionKind.PROMOTE_UNIT, PromoteParams(unit=u, promotion_type=t):
            return f"promote:{u.unit_id}:{t}"
        case ActionKind.APPOINT_GOVERNOR, AppointGovernorParams(governor_type=g):
            return f"governor:appoint:{g}"
        case ActionKind.ASSIGN_GOVERNOR, AssignGovernorParams(
            governor_type=g, city_id=c
        ):
            return f"governor:assign:{g}:{c}"
        case ActionKind.PROMOTE_GOVERNOR, PromoteGovernorParams(
            governor_type=g, promotion_type=t
        ):
            return f"governor:promote:{g}:{t}"
        case ActionKind.CHOOSE_DEDICATION, DedicationParams(name=n):
            return f"dedication:{n}"
        case ActionKind.RECRUIT_GREAT_PERSON, GreatPersonParams(individual_id=i):
            return f"great_person:recruit:{i}"
        case ActionKind.PATRONIZE_GREAT_PERSON, GreatPersonParams(
            individual_id=i, yield_type=y
        ):
            return f"great_person:patronize:{i}:{y}"
        case ActionKind.WAIT_GREAT_PERSON, WaitParams():
            return "great_person:wait"
        case ActionKind.CHOOSE_RELIGION, ReligionChoiceParams(religion_type=r):
            return f"religion:{r}"
        case ActionKind.CHOOSE_FOLLOWER_BELIEF, BeliefParams(belief_type=b):
            return f"follower:{b}"
        case ActionKind.FOUND_RELIGION, FoundReligionParams(founder_belief=b):
            return f"found:{b}"
        case ActionKind.ADD_BELIEF, BeliefParams(belief_type=b):
            return f"belief:{b}"
        case ActionKind.CITY_ATTACK, CityAttackParams(
            city_id=c, target_x=x, target_y=y
        ):
            return f"city_attack:{c}:{x},{y}"
        case ActionKind.HOLD_FIRE, HoldFireParams(city_id=c):
            return f"hold_fire:{c}"
        case ActionKind.MAKE_TRADE_ROUTE, TradeRouteParams(
            unit=u, target_x=x, target_y=y
        ):
            return f"trade:{u.unit_id}:{x},{y}"
        case ActionKind.RESOLVE_CAPTURED_CITY, CapturedCityParams(city_id=c, action=a):
            return f"captured:{c}:{a}"
        case ActionKind.CHOOSE_ESCAPE_ROUTE, EscapeRouteParams(
            spy_unit_id=u, district_type=d
        ):
            return f"escape:{u}:{d}"
        case ActionKind.CHOOSE_ARTIFACT_PLAYER, ArtifactParams(
            archaeologist_unit_id=u, player_id=pid
        ):
            return f"artifact:{u}:{pid}"
        case ActionKind.UPGRADE_UNIT, UpgradeParams(unit=u):
            return f"upgrade:{u.unit_id}"
        case ActionKind.PURCHASE_ITEM, PurchaseParams(
            city_id=c, item_type=t, item_name=n
        ):
            return f"purchase:{c}:{t}:{n}"
        case ActionKind.SAVE_GOLD, SaveGoldParams():
            return "save_gold"
        case ActionKind.DIPLOMATIC_ACTION, DiplomaticActionParams(
            player_id=p, action=a
        ):
            return f"diplo:{p}:{a}"
        case ActionKind.PROPOSE_PEACE, PeaceParams(player_id=p):
            return f"peace:{p}"
        case ActionKind.FORM_ALLIANCE, AllianceParams(player_id=p, alliance_type=t):
            return f"alliance:{p}:{t}"
        case ActionKind.NO_DIPLOMACY, NoDiplomacyParams():
            return "no_diplomacy"
    raise TypeError(f"{kind} does not accept {type(params).__name__}")


@dataclass(frozen=True)
class Candidate:
    candidate_id: str
    kind: ActionKind
    params: ActionParams
    label: str
    facts: Mapping[str, Any] = field(default_factory=dict, compare=False, hash=False)

    @classmethod
    def create(
        cls,
        kind: ActionKind,
        params: ActionParams,
        *,
        label: str,
        facts: Mapping[str, Any] | None = None,
    ) -> Candidate:
        expected = PARAMS_FOR_KIND[kind]
        if not isinstance(params, expected):
            raise TypeError(
                f"{kind} requires {expected.__name__}, got {type(params).__name__}"
            )
        if not label:
            raise ValueError("candidate label must be non-empty")
        return cls(
            candidate_id=candidate_id_for(kind, params),
            kind=kind,
            params=params,
            label=label,
            facts=dict(facts or {}),
        )

    def to_record(self) -> dict[str, Any]:
        return {
            "id": self.candidate_id,
            "kind": str(self.kind),
            "params": dataclasses.asdict(self.params),
            "label": self.label,
            "facts": dict(self.facts),
        }


@dataclass(frozen=True)
class Exclusion:
    """An option the controller did not offer, with the reason."""

    option: str
    reason: str


@dataclass(frozen=True)
class DecisionPoint:
    """One choice presented to the selector, fixed to an observation version.

    Candidates are stored in canonical ``candidate_id`` order and labels are
    made unique in that order, so neither depends on enumeration order.
    """

    decision_id: str
    category: DecisionCategory
    entity: str
    observation_version: str
    question: str
    candidates: tuple[Candidate, ...]
    context: Mapping[str, Any] = field(default_factory=dict, compare=False, hash=False)
    exclusions: tuple[Exclusion, ...] = ()
    # Legal candidates the engine offered before controller filtering; a
    # single remaining candidate may be executed without a selector only
    # under ``forced_rule``.
    legal_count: int = 0
    forced_rule: str | None = None

    @classmethod
    def create(
        cls,
        *,
        decision_id: str,
        category: DecisionCategory,
        entity: str,
        observation_version: str,
        question: str,
        candidates: list[Candidate] | tuple[Candidate, ...],
        context: Mapping[str, Any],
        exclusions: list[Exclusion] | tuple[Exclusion, ...] = (),
        legal_count: int | None = None,
        forced_rule: str | None = None,
    ) -> DecisionPoint:
        if not candidates:
            raise ValueError(f"decision {decision_id} has no candidates")
        ordered = sorted(candidates, key=lambda c: c.candidate_id)
        ids = [c.candidate_id for c in ordered]
        dupes = sorted(i for i, n in Counter(ids).items() if n > 1)
        if dupes:
            raise ValueError(f"duplicate candidate ids: {dupes}")
        legal = len(ordered) if legal_count is None else legal_count
        if forced_rule is None and len(ordered) == 1 and legal == 1:
            forced_rule = "forced_single_candidate"
        return cls(
            decision_id=decision_id,
            category=category,
            entity=entity,
            observation_version=observation_version,
            question=question,
            candidates=tuple(_unique_labels(ordered)),
            context=dict(context),
            exclusions=tuple(exclusions),
            legal_count=legal,
            forced_rule=forced_rule,
        )

    @property
    def label_to_id(self) -> dict[str, str]:
        return {c.label: c.candidate_id for c in self.candidates}

    def get(self, candidate_id: str) -> Candidate | None:
        for c in self.candidates:
            if c.candidate_id == candidate_id:
                return c
        return None

    def to_record(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "category": str(self.category),
            "entity": self.entity,
            "observation_version": self.observation_version,
            "question": self.question,
            "candidates": [c.to_record() for c in self.candidates],
            "exclusions": [dataclasses.asdict(e) for e in self.exclusions],
            "legal_count": self.legal_count,
            "forced_rule": self.forced_rule,
        }


def _unique_labels(ordered: list[Candidate]) -> list[Candidate]:
    totals = Counter(c.label for c in ordered)
    seen: Counter[str] = Counter()
    taken = {c.label for c in ordered if totals[c.label] == 1}
    out: list[Candidate] = []
    for c in ordered:
        if totals[c.label] == 1:
            out.append(c)
            continue
        seen[c.label] += 1
        n = seen[c.label]
        label = f"{c.label} [{n}]"
        while label in taken:
            n += 1
            label = f"{c.label} [{n}]"
        taken.add(label)
        out.append(dataclasses.replace(c, label=label))
    return out
