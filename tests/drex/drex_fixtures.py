"""Realistic early-game typed observations shared by the Drex tests.

Composite unit IDs (e.g. 131073) deliberately differ from per-player unit
indices (e.g. 1) so tests catch any confusion between them.
"""

from civ_mcp import lua as lq

ME = 0
WARRIOR_ID, WARRIOR_IDX = 131073, 1
SETTLER_ID, SETTLER_IDX = 262146, 2
BUILDER_ID, BUILDER_IDX = 393219, 3
CAPITAL_ID = 65536


def overview(turn: int = 5, research: str = "None", civic: str = "Code of Laws"):
    return lq.GameOverview(
        turn=turn,
        player_id=ME,
        civ_name="Rome",
        leader_name="Trajan",
        gold=35.0,
        gold_per_turn=3.0,
        science_yield=3.5,
        culture_yield=2.2,
        faith=0.0,
        current_research=research,
        current_civic=civic,
        num_cities=1,
        num_units=3,
        score=12,
        explored_land=40,
        total_land=900,
        rankings=[lq.ScoreEntry(player_id=1, civ_name="Egypt", score=15)],
        era_name="Ancient Era",
        difficulty="Prince",
        game_speed="GAMESPEED_STANDARD",
    )


def tech_status(research: str = "None", civic: str = "Code of Laws"):
    return lq.TechCivicStatus(
        current_research=research,
        current_research_turns=-1,
        current_civic=civic,
        current_civic_turns=4,
        available_techs=[
            lq.TechOption(
                "Pottery", "TECHNOLOGY_POTTERY", 25, 0, 5, False, "", "Granary"
            ),
            lq.TechOption(
                "Mining", "TECHNOLOGY_MINING", 25, 20, 4, True, "Build a mine", "Mine"
            ),
            lq.TechOption(
                "Animal Husbandry",
                "TECHNOLOGY_ANIMAL_HUSBANDRY",
                25,
                0,
                5,
                False,
                "",
                "Pasture",
            ),
        ],
        available_civics=[
            lq.CivicOption("Code of Laws", "CIVIC_CODE_OF_LAWS", 20, 50, 4, False, ""),
        ],
    )


def capital(building: str = "NONE"):
    return lq.CityInfo(
        city_id=CAPITAL_ID,
        name="Roma",
        x=10,
        y=10,
        population=2,
        food=4.0,
        production=3.0,
        gold=5.0,
        science=2.5,
        culture=1.6,
        faith=0.0,
        housing=5.0,
        amenities=1,
        turns_to_grow=6,
        food_surplus=2.0,
        currently_building=building,
        districts=["DISTRICT_CITY_CENTER"],
    )


def production_options():
    return [
        lq.ProductionOption("UNIT", "UNIT_WARRIOR", 40, 8),
        lq.ProductionOption("UNIT", "UNIT_SETTLER", 80, 16),
        lq.ProductionOption("UNIT", "UNIT_BUILDER", 50, 10, gold_cost=200),
        lq.ProductionOption("BUILDING", "BUILDING_MONUMENT", 60, 12, gold_cost=240),
        lq.ProductionOption("BUILDING", "BUILDING_PYRAMIDS", 220, 44),
        lq.ProductionOption("DISTRICT", "DISTRICT_HOLY_SITE", 54, 11),
        lq.ProductionOption(
            "DISTRICT",
            "DISTRICT_ENCAMPMENT",
            54,
            11,
            is_repair=True,
            repair_x=11,
            repair_y=11,
        ),
        lq.ProductionOption("PROJECT", "PROJECT_ENHANCE_DISTRICT_ENCAMPMENT", 30, 6),
    ]


WONDERS = {"BUILDING_PYRAMIDS", "BUILDING_STONEHENGE"}


def warrior():
    return lq.UnitInfo(
        unit_id=WARRIOR_ID,
        unit_index=WARRIOR_IDX,
        name="Warrior",
        unit_type="UNIT_WARRIOR",
        x=10,
        y=12,
        moves_remaining=2.0,
        max_moves=2.0,
        health=100,
        max_health=100,
        combat_strength=20,
        targets=["UNIT_WARRIOR@9,12(70hp)"],
    )


def settler():
    return lq.UnitInfo(
        unit_id=SETTLER_ID,
        unit_index=SETTLER_IDX,
        name="Settler",
        unit_type="UNIT_SETTLER",
        x=13,
        y=9,
        moves_remaining=2.0,
        max_moves=2.0,
        health=100,
        max_health=100,
    )


def builder(valid=("IMPROVEMENT_FARM", "IMPROVEMENT_MINE")):
    return lq.UnitInfo(
        unit_id=BUILDER_ID,
        unit_index=BUILDER_IDX,
        name="Builder",
        unit_type="UNIT_BUILDER",
        x=11,
        y=10,
        moves_remaining=2.0,
        max_moves=2.0,
        health=100,
        max_health=100,
        build_charges=3,
        valid_improvements=list(valid),
    )


def _reach(x, y, **kw):
    base = dict(
        terrain="TERRAIN_GRASS",
        feature=None,
        resource=None,
        is_hills=False,
        is_river=False,
        owner_id=-1,
        visibility="visible",
        own_stack_conflict=False,
        visible_foreign_unit=False,
        distance=1,
    )
    base.update(kw)
    return lq.ReachableTile(x=x, y=y, **base)


def warrior_space(fortify_turns: int = 0):
    return lq.UnitActionSpace(
        unit_id=WARRIOR_ID,
        unit_index=WARRIOR_IDX,
        unit_type="UNIT_WARRIOR",
        x=10,
        y=12,
        moves_remaining=2.0,
        is_civilian=False,
        can_found=False,
        can_fortify=True,
        can_heal=False,
        moved_into_zoc=False,
        fortify_turns=fortify_turns,
        hp=100,
        max_hp=100,
        reachable=[
            _reach(11, 12, owner_id=ME),
            _reach(11, 13, terrain="TERRAIN_PLAINS", is_hills=True, distance=1),
            _reach(10, 11, own_stack_conflict=True),
            _reach(9, 11, visible_foreign_unit=True),
            _reach(12, 13, visibility="revealed", owner_id=-2, distance=2),
        ],
        targets=[
            lq.AttackTarget(
                9, 12, "MELEE", 63, "Barbarian", "UNIT_WARRIOR", 70, 100, 1, 20
            )
        ],
    )


def settler_space():
    return lq.UnitActionSpace(
        unit_id=SETTLER_ID,
        unit_index=SETTLER_IDX,
        unit_type="UNIT_SETTLER",
        x=13,
        y=9,
        moves_remaining=2.0,
        is_civilian=True,
        can_found=True,
        can_fortify=False,
        can_heal=False,
        moved_into_zoc=False,
        fortify_turns=0,
        hp=100,
        max_hp=100,
        reachable=[
            _reach(14, 9, is_river=True),
            _reach(14, 8, feature="FEATURE_FOREST", resource="RESOURCE_DEER"),
        ],
    )


def builder_space():
    return lq.UnitActionSpace(
        unit_id=BUILDER_ID,
        unit_index=BUILDER_IDX,
        unit_type="UNIT_BUILDER",
        x=11,
        y=10,
        moves_remaining=2.0,
        is_civilian=True,
        can_found=False,
        can_fortify=False,
        can_heal=False,
        moved_into_zoc=False,
        fortify_turns=0,
        hp=100,
        max_hp=100,
        reachable=[_reach(12, 10, owner_id=ME)],
    )


def tiles_around_warrior():
    def tile(x, y, **kw):
        base = dict(
            terrain="TERRAIN_GRASS",
            feature=None,
            resource=None,
            is_hills=False,
            is_river=False,
            is_coastal=False,
            improvement=None,
            owner_id=-1,
        )
        base.update(kw)
        return lq.TileInfo(x=x, y=y, **base)

    return [
        tile(10, 12, own_units=["WARRIOR"], yields=(2, 0, 0, 0, 0, 0)),
        tile(9, 12, units=["Barbarian WARRIOR"], yields=(2, 0, 0, 0, 0, 0)),
        tile(
            12,
            13,
            visibility="revealed",
            units=["Egypt SPEARMAN"],
            owner_id=1,
            owner_name="Egypt",
            yields=(1, 1, 0, 0, 0, 0),
            resource="RESOURCE_HORSES",
        ),
        tile(8, 12, visibility="unexplored", terrain="TERRAIN_DESERT"),
    ]


def session(**kw):
    base = dict(
        session_id=5,
        other_player_id=1,
        other_civ_name="Egypt",
        other_leader_name="Cleopatra",
        choices=[],
        dialogue_text="Greetings, Rome.",
        reason_text="",
        buttons="Pleasure to meet you;Leave",
    )
    base.update(kw)
    return lq.DiplomacySession(**base)


def deal():
    return lq.PendingDeal(
        other_player_id=1,
        other_player_name="Egypt",
        other_leader_name="Cleopatra",
        items_from_them=[
            lq.DealItem(1, "Egypt", "GOLD", "Gold per turn", 2, 30, False)
        ],
        items_from_us=[lq.DealItem(-1, "Us", "RESOURCE", "Wine", 1, 30, True)],
    )


def policies(slot_policy: str | None = None):
    return lq.GovernmentStatus(
        government_name="Chiefdom",
        government_type="GOVERNMENT_CHIEFDOM",
        slots=[
            lq.PolicySlot(0, "SLOT_MILITARY", slot_policy, None),
            lq.PolicySlot(1, "SLOT_ECONOMIC", None, None),
        ],
        available_policies=[
            lq.PolicyInfo(
                "POLICY_DISCIPLINE",
                "Discipline",
                "+5 CS vs barbarians",
                "SLOT_MILITARY",
            ),
            lq.PolicyInfo(
                "POLICY_SURVEY", "Survey", "Doubles scout XP", "SLOT_MILITARY"
            ),
            lq.PolicyInfo(
                "POLICY_URBAN_PLANNING",
                "Urban Planning",
                "+1 production",
                "SLOT_ECONOMIC",
            ),
        ],
    )


def governments():
    return [
        lq.GovernmentOption(
            "GOVERNMENT_CHIEFDOM",
            1,
            False,
            "Chiefdom",
            ["SLOT_MILITARY", "SLOT_ECONOMIC"],
            "",
        ),
    ]


def envoys(tokens: int = 1):
    return lq.EnvoyStatus(
        tokens_available=tokens,
        city_states=[
            lq.CityStateInfo(20, "Kabul", "Militaristic", 0, -1, "None", True),
            lq.CityStateInfo(21, "Geneva", "Scientific", 1, -1, "None", True),
            lq.CityStateInfo(22, "Hattusa", "Industrial", 0, 1, "Egypt", False),
        ],
    )


def pantheon(has: bool = False):
    return lq.PantheonStatus(
        has_pantheon=has,
        current_belief=None,
        current_belief_name=None,
        faith_balance=25.0,
        available_beliefs=[
            lq.BeliefInfo(
                "BELIEF_GOD_OF_THE_FORGE",
                "God of the Forge",
                "+25% production toward ancient military units",
            ),
            lq.BeliefInfo(
                "BELIEF_RELIGIOUS_SETTLEMENTS",
                "Religious Settlements",
                "Border expansion +15%",
            ),
        ],
    )


# ------------------------------------------------------------ Phase 2 kinds
def promotable_warrior():
    from civ_mcp.lua.drex_queries import PromotableUnit

    return PromotableUnit(
        WARRIOR_ID, WARRIOR_IDX, "UNIT_WARRIOR", xp=30, xp_needed=15, promotion_count=1
    )


def warrior_promotions():
    return lq.UnitPromotionStatus(
        unit_id=WARRIOR_ID,
        unit_index=WARRIOR_IDX,
        unit_type="UNIT_WARRIOR",
        promotions=[
            lq.PromotionOption(
                "PROMOTION_BATTLECRY",
                "Battlecry",
                "+7 Combat Strength vs. melee and ranged units",
            ),
            lq.PromotionOption(
                "PROMOTION_TORTOISE",
                "Tortoise",
                "+10 Combat Strength when defending against ranged attacks",
            ),
        ],
        xp=30,
        xp_needed=15,
        promotion_count=1,
    )


def governors(points: int = 0, unassigned: bool = False):
    promo = lq.GovernorPromotion(
        "GOVERNOR_PROMOTION_EDUCATOR_LIBRARIAN",
        "Librarian",
        "+1 Science per Citizen in this city",
        1,
        0,
    )
    return lq.GovernorStatus(
        points_available=points,
        points_spent=1,
        can_appoint=points > 0,
        appointed=[
            lq.AppointedGovernor(
                "GOVERNOR_THE_EDUCATOR",
                "Pingala",
                -1 if unassigned else CAPITAL_ID,
                "Unassigned" if unassigned else "Roma",
                not unassigned,
                0,
                [promo] if points > 0 else [],
            )
        ],
        available_to_appoint=[
            lq.GovernorInfo(
                "GOVERNOR_THE_DEFENDER",
                "Victor",
                "Castellan",
                "Military governor",
                "Redoubt",
                "+5 Combat Strength for units within the city",
            )
        ],
    )


def dedications(age: str = "Normal"):
    return lq.DedicationStatus(
        age_type=age,
        era=1,
        era_score=12,
        dark_threshold=5,
        golden_threshold=17,
        selections_allowed=1,
        active=[],
        choices=[
            lq.DedicationChoice(
                0,
                "COMMEMORATION_SCIENTIFIC",
                "normal: +1 Era Score per tech boost",
                "golden: free tech per era",
                "dark: +2 loyalty from campuses",
            ),
            lq.DedicationChoice(
                1,
                "COMMEMORATION_MILITARY",
                "normal: +1 Era Score per unit promoted",
                "golden: +1 Movement for all units",
                "dark: +3 loyalty from encampments",
            ),
        ],
    )
