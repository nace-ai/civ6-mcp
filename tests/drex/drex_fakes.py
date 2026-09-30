"""In-memory stand-in for GameState used by executor/scheduler/runner tests.

Implements the GameState methods the Drex controller calls, applies the same
kind of state change the engine would, and records every call. Failures can
be injected before or after a mutation is applied.
"""

import contextlib
import copy
from collections import Counter

import drex_fixtures as fx

from civ_mcp import lua as lq


class FakeConn:
    """Mimics GameConnection.replay_disabled() so tests can see its state."""

    def __init__(self):
        self.replay_on_disconnect = True
        self.roundtrips = 0
        self.roundtrip_ms = 0.0

    def snapshot_counters(self):
        return self.roundtrips, self.roundtrip_ms

    async def reconnect(self):
        self.reconnects = getattr(self, "reconnects", 0) + 1

    @contextlib.contextmanager
    def replay_disabled(self):
        previous = self.replay_on_disconnect
        self.replay_on_disconnect = False
        try:
            yield
        finally:
            self.replay_on_disconnect = previous


class FakeGame:
    def __init__(self):
        self.conn = FakeConn()
        self.replay_at_call: list[bool] = []
        self.query_counts: Counter[str] = Counter()
        self.session_rounds: dict[int, int] = {}
        self.deal_sticky = False
        self.attack_refused = False
        self.calls: list[tuple[str, tuple]] = []
        self.turn = 5
        self.civ, self.seed = "rome", 42
        self.research: str | None = None
        self.civic: str | None = "CIVIC_CODE_OF_LAWS"
        self.gold = 35.0
        self.cities = {fx.CAPITAL_ID: fx.capital()}
        self.production = {fx.CAPITAL_ID: fx.production_options()}
        self.units = {
            fx.WARRIOR_ID: fx.warrior(),
            fx.SETTLER_ID: fx.settler(),
            fx.BUILDER_ID: fx.builder(),
        }
        self.spaces = {
            fx.WARRIOR_IDX: fx.warrior_space(),
            fx.SETTLER_IDX: fx.settler_space(),
            fx.BUILDER_IDX: fx.builder_space(),
        }
        self.fortify = {}
        self.improvements: dict[tuple[int, int], str] = {}
        self.founded: set[tuple[int, int]] = set()
        self.sessions: list[lq.DiplomacySession] = []
        self.deals: list[lq.PendingDeal] = []
        self.policy_status = fx.policies()
        self.envoy_status = fx.envoys()
        self.govs = fx.governments()
        self.current_gov = "NONE"
        self.pantheon_status = fx.pantheon()
        self.extra_blockers: list[tuple[str, str]] = []
        self.promotable: list = []  # Phase 2: units with a promotion available
        self.governor_status = fx.governors(points=0, unassigned=False)
        self.dedication_status = fx.dedications()
        self.great_people: list = []
        self.religion_status = fx.religion_founding()
        self.city_targets: dict[int, list] = {}
        self.placements: dict[int, dict] = {}
        self.trade_status = fx.trade_status(capacity=0, active=0)
        self.trade_destinations: list = []
        # blockers the fake keeps raising even after the matching action
        self.sticky_blockers: set[str] = set()
        self.fail: dict[str, tuple[Exception, bool]] = {}
        self.ignore: set[str] = set()
        self.end_turn_calls = 0
        # like the real game: the move result reports the position read back
        self.move_result_suffix = "|now_at:{x},{y}"
        self.move_predismiss: list[bool] = []

    # ---------------------------------------------------------------- helpers
    def _record(self, method, *args):
        self.calls.append((method, args))
        self.conn.roundtrips += 1
        self.replay_at_call.append(self.conn.replay_on_disconnect)
        fail = self.fail.pop(method, None)
        if fail and not fail[1]:
            raise fail[0]
        return fail

    async def _record_only(self, method, *args):
        """Test helper: record a call without the fake's side effects."""
        self.calls.append((method, args))
        self.conn.roundtrips += 1

    async def dismiss_blocker_notifications(self, blocking_types):
        self._record("dismiss_blocker_notifications", tuple(blocking_types))
        self.extra_blockers = [
            b for b in self.extra_blockers if b[0] not in blocking_types
        ]
        self.sticky_blockers -= set(blocking_types)
        return f"DISMISSED|{len(blocking_types)}"

    def _maybe_fail(self, method):
        fail = self.fail.pop(method, None)
        if fail:
            raise fail[0]

    def _after(self, fail):
        if fail and fail[1]:
            raise fail[0]

    def _by_index(self, idx):
        return next((u for u in self.units.values() if u.unit_index == idx), None)

    def _set_pos(self, idx, x, y, moves):
        u = self._by_index(idx)
        u.x, u.y, u.moves_remaining = x, y, moves
        space = self.spaces[idx]
        space.x, space.y, space.moves_remaining = x, y, moves
        if moves <= 0:
            space.reachable, space.targets = [], []

    # ---------------------------------------------------------------- queries
    async def get_game_identity(self):
        return (self.civ, self.seed)

    async def get_game_overview(self):
        self.query_counts["get_game_overview"] += 1
        self.conn.roundtrips += 1
        research = "None"
        if self.research:
            research = next(
                t.name
                for t in fx.tech_status().available_techs
                if t.tech_type == self.research
            )
        ov = fx.overview(turn=self.turn, research=research)
        ov.gold = self.gold
        ov.num_units = len(self.units)
        ov.num_cities = len(self.cities)
        return ov

    async def get_tech_civics(self):
        status = fx.tech_status()
        if self.research:
            status.current_research = self.research
        if self.civic is None:
            status.current_civic = "None"
        return status

    async def get_progress_types(self):
        self.query_counts["get_progress_types"] += 1
        self.conn.roundtrips += 1
        return lq.ProgressTypes(self.research, self.civic)

    async def check_eligibility(self, kind, type_name):
        pool = fx.tech_status()
        types = (
            {t.tech_type for t in pool.available_techs}
            if kind == "tech"
            else {c.civic_type for c in pool.available_civics}
        )
        return (type_name in types, "engine")

    async def get_cities(self):
        self._maybe_fail("get_cities")
        self.query_counts["get_cities"] += 1
        self.conn.roundtrips += 1
        return [copy.deepcopy(c) for c in self.cities.values()], []

    async def list_city_production(self, city_id):
        self.query_counts["list_city_production"] += 1
        self.conn.roundtrips += 1
        return copy.deepcopy(self.production.get(city_id, []))

    async def get_wonder_types(self):
        return set(fx.WONDERS)

    async def get_units(self):
        self._maybe_fail("get_units")
        self.query_counts["get_units"] += 1
        self.conn.roundtrips += 1
        return [copy.deepcopy(u) for u in self.units.values()]

    async def get_unit_action_space(self, unit_index):
        self.query_counts["get_unit_action_space"] += 1
        self.conn.roundtrips += 1
        return copy.deepcopy(self.spaces.get(unit_index))

    async def get_unit_state(self, unit_index):
        self.query_counts["get_unit_state"] += 1
        self.conn.roundtrips += 1
        u = self._by_index(unit_index)
        if u is None:
            return None
        return lq.UnitState(
            u.x,
            u.y,
            u.moves_remaining,
            self.fortify.get(u.unit_id, 0),
            u.health,
            u.build_charges,
        )

    async def get_map_area(self, x, y, radius=2):
        tiles = []
        for tx in range(x - radius, x + radius + 1):
            for ty in range(y - radius, y + radius + 1):
                tiles.append(
                    lq.TileInfo(
                        x=tx,
                        y=ty,
                        terrain="TERRAIN_GRASS",
                        feature=None,
                        resource=None,
                        is_hills=False,
                        is_river=False,
                        is_coastal=False,
                        improvement=self.improvements.get((tx, ty)),
                        owner_id=0,
                    )
                )
        return tiles

    async def verify_production(self, city_id, item_name):
        fail = self.fail.pop("verify_production", None)
        if fail:
            raise fail[0]
        return self.cities[city_id].currently_building == item_name

    async def city_exists_at(self, x, y):
        return (x, y) in self.founded

    async def get_diplomacy_sessions(self):
        self.query_counts["get_diplomacy_sessions"] += 1
        self.conn.roundtrips += 1
        return copy.deepcopy(self.sessions)

    async def get_pending_deals(self):
        self.query_counts["get_pending_deals"] += 1
        self.conn.roundtrips += 1
        return copy.deepcopy(self.deals)

    async def get_policies(self):
        status = copy.deepcopy(self.policy_status)
        status.government_type = self.current_gov
        return status

    async def get_city_states(self):
        return copy.deepcopy(self.envoy_status)

    async def get_available_governments(self):
        govs = copy.deepcopy(self.govs)
        for g in govs:
            g.is_current = g.government_type == self.current_gov
        return govs

    async def get_pantheon_status(self):
        return copy.deepcopy(self.pantheon_status)

    async def get_end_turn_blockers(self):
        self.query_counts["get_end_turn_blockers"] += 1
        self.conn.roundtrips += 1
        blockers = list(self.extra_blockers)
        for name in self.sticky_blockers:
            if name not in {b[0] for b in blockers}:
                blockers.append((name, "sticky"))
        if self.research is None:
            blockers.append(("ENDTURN_BLOCKING_RESEARCH", "Choose research"))
        if self.civic is None:
            blockers.append(("ENDTURN_BLOCKING_CIVIC", "Choose civic"))
        for c in self.cities.values():
            if c.currently_building in ("nothing", "NONE"):
                blockers.append(("ENDTURN_BLOCKING_PRODUCTION", "Choose production"))
                break
        if any(u.moves_remaining > 0 for u in self.units.values()):
            blockers.append(("ENDTURN_BLOCKING_UNITS", "Units need orders"))
        return blockers

    async def get_promotable_units(self):
        self.query_counts["get_promotable_units"] += 1
        self.conn.roundtrips += 1
        return [copy.deepcopy(u) for u in self.promotable]

    async def get_unit_promotions(self, unit_id):
        self.query_counts["get_unit_promotions"] += 1
        self.conn.roundtrips += 1
        if any(u.unit_id == unit_id for u in self.promotable):
            return fx.warrior_promotions()
        return lq.UnitPromotionStatus(unit_id, unit_id % 65536, "UNIT_WARRIOR")

    async def get_trade_routes(self):
        self.query_counts["get_trade_routes"] += 1
        self.conn.roundtrips += 1
        return copy.deepcopy(self.trade_status)

    async def get_trade_destinations(self, unit_index):
        self.query_counts["get_trade_destinations"] += 1
        self.conn.roundtrips += 1
        return copy.deepcopy(self.trade_destinations)

    async def get_placement_options(self, city_id, districts, wonders):
        self.query_counts["get_placement_options"] += 1
        self.conn.roundtrips += 1
        wanted = set(districts) | set(wonders)
        have = self.placements.get(city_id, {})
        return ({k: copy.deepcopy(v) for k, v in have.items() if k in wanted}, {})

    async def get_city_attack_targets(self, city_id):
        self.query_counts["get_city_attack_targets"] += 1
        self.conn.roundtrips += 1
        return [copy.deepcopy(t) for t in self.city_targets.get(city_id, [])]

    async def get_religion_founding_status(self):
        self.query_counts["get_religion_founding_status"] += 1
        self.conn.roundtrips += 1
        return copy.deepcopy(self.religion_status)

    async def get_great_people(self):
        self.query_counts["get_great_people"] += 1
        self.conn.roundtrips += 1
        return [copy.deepcopy(p) for p in self.great_people]

    async def get_dedications(self):
        self.query_counts["get_dedications"] += 1
        self.conn.roundtrips += 1
        return copy.deepcopy(self.dedication_status)

    async def get_governors(self):
        self.query_counts["get_governors"] += 1
        self.conn.roundtrips += 1
        return copy.deepcopy(self.governor_status)

    # ---------------------------------------------------------------- actions
    async def dismiss_popup(self):
        self._record("dismiss_popup")
        return "Dismissed nothing"

    async def set_research(self, tech):
        fail = self._record("set_research", tech)
        if "set_research" not in self.ignore:
            self.research = tech
        self._after(fail)
        return f"RESEARCHING|{tech}"

    async def set_civic(self, civic):
        fail = self._record("set_civic", civic)
        self.civic = civic
        self._after(fail)
        return f"PROGRESSING|{civic}"

    async def set_city_production(
        self, city_id, item_type, item_name, target_x=None, target_y=None
    ):
        fail = self._record(
            "set_city_production", city_id, item_type, item_name, target_x, target_y
        )
        self.cities[city_id].currently_building = item_name
        self._after(fail)
        return f"PRODUCING|{item_name}|8 turns"

    async def move_unit(self, unit_index, x, y, predismiss=True):
        fail = self._record("move_unit", unit_index, x, y)
        self.move_predismiss.append(predismiss)
        if "move_unit" not in self.ignore:
            self._set_pos(unit_index, x, y, 0.0)
        self._after(fail)
        # Like the real game, the readback reports where the unit actually is.
        u = self._by_index(unit_index)
        now = self.move_result_suffix.format(x=u.x, y=u.y) if u else ""
        return f"MOVING_TO|{x},{y}|from:0,0" + now

    async def attack_unit(self, unit_index, x, y):
        fail = self._record("attack_unit", unit_index, x, y)
        estimate = "Combat estimate: WARRIOR (CS 20) vs WARRIOR (CS 20) -> ~30 dmg\n"
        if self.attack_refused:
            self._after(fail)
            return estimate + f"Error: NO_ENEMY|No hostile unit at ({x},{y})"
        u = self._by_index(unit_index)
        self._set_pos(unit_index, u.x, u.y, 0.0)
        self._after(fail)
        return estimate + f"MELEE_ATTACK|target:UNIT_WARRIOR at ({x},{y})"

    async def found_city(self, unit_index):
        fail = self._record("found_city", unit_index)
        u = self._by_index(unit_index)
        self.founded.add((u.x, u.y))
        del self.units[u.unit_id]
        del self.spaces[unit_index]
        self._after(fail)
        return f"FOUNDED|{u.x},{u.y}"

    async def improve_tile(self, unit_index, improvement):
        fail = self._record("improve_tile", unit_index, improvement)
        u = self._by_index(unit_index)
        self.improvements[(u.x, u.y)] = improvement
        u.build_charges -= 1
        self._set_pos(unit_index, u.x, u.y, 0.0)
        self._after(fail)
        return f"IMPROVING|{improvement}|{u.x},{u.y}"

    async def fortify_unit(self, unit_index):
        fail = self._record("fortify_unit", unit_index)
        u = self._by_index(unit_index)
        self._set_pos(unit_index, u.x, u.y, 0.0)
        self._after(fail)
        return "FORTIFIED"

    async def heal_unit(self, unit_index):
        fail = self._record("heal_unit", unit_index)
        u = self._by_index(unit_index)
        self._set_pos(unit_index, u.x, u.y, 0.0)
        self._after(fail)
        return "HEALING|HP:50/100"

    async def skip_unit(self, unit_index):
        fail = self._record("skip_unit", unit_index)
        u = self._by_index(unit_index)
        self._set_pos(unit_index, u.x, u.y, 0.0)
        self._after(fail)
        return "SKIPPED"

    async def diplomacy_respond(self, other_player_id, response):
        fail = self._record("diplomacy_respond", other_player_id, response)
        remaining = self.session_rounds.get(other_player_id, 1) - 1
        self.session_rounds[other_player_id] = remaining
        if response == "EXIT" or remaining <= 0:
            self.sessions = [
                s for s in self.sessions if s.other_player_id != other_player_id
            ]
            status = "SESSION_CLOSED"
        else:
            for s in self.sessions:
                if s.other_player_id == other_player_id:
                    s.dialogue_text = f"{s.dialogue_text} (round {remaining})"
            status = "SESSION_CONTINUES"
        self._after(fail)
        return f"OK:RESPONDED|{response}|{status}"

    async def respond_to_deal(self, other_player_id, accept):
        fail = self._record("respond_to_deal", other_player_id, accept)
        if not self.deal_sticky:
            self.deals = [d for d in self.deals if d.other_player_id != other_player_id]
            self.sessions = [
                s for s in self.sessions if s.other_player_id != other_player_id
            ]
        self._after(fail)
        return "DEAL_ACCEPTED|Egypt" if accept else "DEAL_REJECTED|Egypt"

    async def set_policies(self, assignments):
        fail = self._record("set_policies", assignments)
        for slot in self.policy_status.slots:
            if slot.slot_index in assignments:
                slot.current_policy = assignments[slot.slot_index]
        self._after(fail)
        return "POLICIES_SET|Policies updated."

    async def send_envoy(self, city_state_player_id):
        fail = self._record("send_envoy", city_state_player_id)
        self.envoy_status.tokens_available -= 1
        self._after(fail)
        return "ENVOY_SENT|Kabul"

    async def change_government(self, government_type):
        fail = self._record("change_government", government_type)
        self.current_gov = government_type
        self.extra_blockers = [
            b
            for b in self.extra_blockers
            if b[0] != "ENDTURN_BLOCKING_CONSIDER_GOVERNMENT_CHANGE"
        ]
        self._after(fail)
        return f"GOVERNMENT_CHANGED|{government_type}|Chiefdom"

    async def keep_current_government(self):
        fail = self._record("keep_current_government")
        self.extra_blockers = [
            b
            for b in self.extra_blockers
            if b[0] != "ENDTURN_BLOCKING_CONSIDER_GOVERNMENT_CHANGE"
        ]
        self._after(fail)
        return "GOVERNMENT_CHANGE_CONSIDERED"

    def _governor_blockers_done(self):
        st = self.governor_status
        busy = st.can_appoint or any(
            g.assigned_city_id == -1
            or (st.points_available > 0 and g.available_promotions)
            for g in st.appointed
        )
        if not busy:
            self.extra_blockers = [
                b for b in self.extra_blockers if "GOVERNOR" not in b[0]
            ]

    async def appoint_governor(self, governor_type):
        fail = self._record("appoint_governor", governor_type)
        st = self.governor_status
        st.appointed.append(
            lq.AppointedGovernor(
                governor_type, governor_type.title(), -1, "Unassigned", False
            )
        )
        st.available_to_appoint = [
            g for g in st.available_to_appoint if g.governor_type != governor_type
        ]
        st.points_available -= 1
        st.can_appoint = st.points_available > 0 and bool(st.available_to_appoint)
        self._governor_blockers_done()
        self._after(fail)
        return "APPOINTED|Victor (Castellan)"

    async def assign_governor(self, governor_type, city_id):
        fail = self._record("assign_governor", governor_type, city_id)
        for g in self.governor_status.appointed:
            if g.governor_type == governor_type:
                g.assigned_city_id = city_id
                g.assigned_city_name = self.cities[city_id].name
        self._governor_blockers_done()
        self._after(fail)
        return "ASSIGNED|Pingala to Roma"

    async def promote_governor(self, governor_type, promotion_type):
        fail = self._record("promote_governor", governor_type, promotion_type)
        st = self.governor_status
        for g in st.appointed:
            if g.governor_type == governor_type:
                g.available_promotions = [
                    p
                    for p in g.available_promotions
                    if p.promotion_type != promotion_type
                ]
        st.points_available -= 1
        self._governor_blockers_done()
        self._after(fail)
        return "PROMOTED|Pingala with Librarian"

    async def make_trade_route(self, unit_index, target_x, target_y):
        fail = self._record("make_trade_route", unit_index, target_x, target_y)
        self.trade_status.active_count += 1
        for t in self.trade_status.traders:
            if t.unit_id % 65536 == unit_index:
                t.on_route = True
        self._set_pos(
            unit_index, self._by_index(unit_index).x, self._by_index(unit_index).y, 0.0
        )
        self._after(fail)
        dest = next(
            (d for d in self.trade_destinations if (d.x, d.y) == (target_x, target_y)),
            None,
        )
        return f"TRADE_ROUTE_STARTED|to {dest.city_name if dest else '?'}"

    async def city_attack(self, city_id, target_x, target_y):
        fail = self._record("city_attack", city_id, target_x, target_y)
        self.city_targets[city_id] = []
        self.extra_blockers = [
            b for b in self.extra_blockers if "RANGE_ATTACK" not in b[0]
        ]
        self._after(fail)
        return f"CITY_RANGE_ATTACK|Roma -> UNIT_WARRIOR@{target_x},{target_y}|pre_hp:80/100"

    async def found_religion(self, religion_type, follower_belief, founder_belief):
        fail = self._record(
            "found_religion", religion_type, follower_belief, founder_belief
        )
        st = self.religion_status
        st.has_religion = True
        st.religion_type = religion_type
        st.religion_name = dict(st.available_religions).get(
            religion_type, religion_type
        )
        for cls in ("BELIEF_CLASS_FOLLOWER", "BELIEF_CLASS_FOUNDER"):
            st.beliefs_by_class[cls] = [
                b
                for b in st.beliefs_by_class.get(cls, [])
                if b.belief_type not in (follower_belief, founder_belief)
            ]
        self.extra_blockers = [
            b for b in self.extra_blockers if b[0] != "ENDTURN_BLOCKING_RELIGION"
        ]
        self._after(fail)
        return f"RELIGION_FOUNDED|{st.religion_name}|{follower_belief}|{founder_belief}"

    async def add_belief(self, belief_type):
        fail = self._record("add_belief", belief_type)
        st = self.religion_status
        for cls, beliefs in st.beliefs_by_class.items():
            st.beliefs_by_class[cls] = [
                b for b in beliefs if b.belief_type != belief_type
            ]
        self.extra_blockers = [
            b for b in self.extra_blockers if b[0] != "ENDTURN_BLOCKING_BELIEF"
        ]
        self._after(fail)
        return f"BELIEF_ADDED|{belief_type}"

    def _claim(self, individual_id):
        for gp in self.great_people:
            if gp.individual_id == individual_id:
                gp.claimant = "Rome"
                gp.can_recruit = False
                self.extra_blockers = [
                    b
                    for b in self.extra_blockers
                    if b[0] != "ENDTURN_BLOCKING_CLAIM_GREAT_PERSON"
                ]
                return gp
        return None

    async def recruit_great_person(self, individual_id):
        fail = self._record("recruit_great_person", individual_id)
        gp = self._claim(individual_id)
        self._after(fail)
        return f"RECRUITED|{gp.individual_name if gp else individual_id}"

    async def patronize_great_person(self, individual_id, yield_type="YIELD_GOLD"):
        fail = self._record("patronize_great_person", individual_id, yield_type)
        gp = self._claim(individual_id)
        self._after(fail)
        return f"PATRONIZED|{gp.individual_name if gp else individual_id}|{yield_type}"

    async def choose_dedication(self, dedication_index):
        fail = self._record("choose_dedication", dedication_index)
        choice = next(
            c for c in self.dedication_status.choices if c.index == dedication_index
        )
        self.dedication_status.active.append(choice.name)
        self.extra_blockers = [
            b
            for b in self.extra_blockers
            if b[0] != "ENDTURN_BLOCKING_COMMEMORATION_AVAILABLE"
        ]
        self._after(fail)
        return f"DEDICATION_CHOSEN|{choice.name}"

    async def promote_unit(self, unit_id, promotion_type):
        fail = self._record("promote_unit", unit_id, promotion_type)
        self.promotable = [u for u in self.promotable if u.unit_id != unit_id]
        if not self.promotable:
            self.extra_blockers = [
                b
                for b in self.extra_blockers
                if b[0] != "ENDTURN_BLOCKING_UNIT_PROMOTION"
            ]
        self._after(fail)
        return f"PROMOTED|{promotion_type}|stored:1->0"

    async def choose_pantheon(self, belief_type):
        fail = self._record("choose_pantheon", belief_type)
        self.pantheon_status.has_pantheon = True
        self.pantheon_status.current_belief = belief_type
        self._after(fail)
        return f"PANTHEON_FOUNDED|{belief_type}"
