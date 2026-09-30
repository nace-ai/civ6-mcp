"""GameState-backed observation for the decision loop."""

from __future__ import annotations

import dataclasses
from typing import Any

from civ_mcp.drex.candidates import DecisionCategory
from civ_mcp.drex.observation import (
    CLAIM_BLOCKER,
    GREAT_PEOPLE_EVERY_TURNS,
    PROMOTION_BLOCKER,
    Blocker,
    CoreObservation,
    DecisionInputs,
    DecisionSpec,
)


class LiveObserver:
    """Builds typed observations; each ``core()`` call gets a new version."""

    def __init__(self, gs: Any, *, nearby_radius: int = 2):
        self.gs = gs
        self.nearby_radius = nearby_radius
        self._counter = 0
        self._version = ""
        self._wonders: tuple[tuple[str, int], set[str]] | None = None

    @property
    def version(self) -> str:
        return self._version

    async def core(self) -> CoreObservation:
        gs = self.gs
        if hasattr(gs, "get_core_snapshot"):
            return await self._with_promotable(
                self._from_snapshot(await gs.get_core_snapshot())
            )
        civ, seed = await gs.get_game_identity()
        overview = await gs.get_game_overview()
        tech = await gs.get_tech_civics()
        progress = await gs.get_progress_types()
        cities, _ = await gs.get_cities()
        units = await gs.get_units()
        sessions = await gs.get_diplomacy_sessions()
        deals = await gs.get_pending_deals()
        blockers = await gs.get_end_turn_blockers()
        self._counter += 1
        self._version = f"{civ}:{seed}:T{overview.turn}:{self._counter}"
        return await self._with_promotable(
            CoreObservation(
                version=self._version,
                civ=civ,
                seed=seed,
                local_player_id=overview.player_id,
                overview=overview,
                tech=tech,
                progress=progress,
                cities=cities,
                units=units,
                diplomacy_sessions=sessions,
                pending_deals=deals,
                blockers=[Blocker(t, m) for t, m in blockers],
            )
        )

    async def _with_promotable(self, core: CoreObservation) -> CoreObservation:
        """Read promotable units only while the promotion blocker stands, and
        the Great People pool every few turns or while a claim is forced."""
        blockers = core.blocker_types()
        if PROMOTION_BLOCKER in blockers:
            core = dataclasses.replace(
                core, promotable=list(await self.gs.get_promotable_units())
            )
        elif core.promotable:
            core = dataclasses.replace(core, promotable=[])
        if CLAIM_BLOCKER in blockers or (
            core.great_people is None and core.turn % GREAT_PEOPLE_EVERY_TURNS == 0
        ):
            core = dataclasses.replace(
                core, great_people=list(await self.gs.get_great_people())
            )
        return core

    def _from_snapshot(self, snap: Any) -> CoreObservation:
        missing = [
            p
            for p in (
                "overview",
                "tech",
                "progress",
                "cities",
                "units",
                "sessions",
                "deals",
                "blockers",
            )
            if getattr(snap, p) is None
        ]
        if "identity" in snap.errors or snap.civ == "unknown":
            missing.insert(0, "identity")
        if missing:
            raise ConnectionError(
                f"core snapshot incomplete: {missing}; errors={snap.errors}"
            )
        self._counter += 1
        self._version = f"{snap.civ}:{snap.seed}:T{snap.overview.turn}:{self._counter}"
        return CoreObservation(
            version=self._version,
            civ=snap.civ,
            seed=snap.seed,
            local_player_id=snap.overview.player_id,
            overview=snap.overview,
            tech=snap.tech,
            progress=snap.progress,
            cities=snap.cities,
            units=snap.units,
            diplomacy_sessions=snap.sessions,
            pending_deals=snap.deals,
            blockers=[Blocker(t, m) for t, m in snap.blockers],
            popup_state=snap.popup_state or "CLEAR",
        )

    async def reactive(self, previous: CoreObservation) -> CoreObservation:
        """Refresh only sessions and deals, e.g. while an end turn is in flight
        and the AI is still processing (heavier queries risk stalling it)."""
        return await self.refresh(previous, frozenset({"sessions", "deals"}))

    async def refresh(
        self, previous: CoreObservation, parts: frozenset[str]
    ) -> CoreObservation:
        """Re-read only ``parts`` of the observation; everything else is kept
        from ``previous``. The version is bumped so stale decisions are still
        rejected."""
        gs = self.gs
        changes: dict[str, Any] = {}
        if hasattr(gs, "get_core_snapshot"):
            snap = await gs.get_core_snapshot(frozenset(parts))
            mapping = (
                ("overview", "overview"),
                ("tech", "tech"),
                ("progress", "progress"),
                ("cities", "cities"),
                ("units", "units"),
                ("sessions", "diplomacy_sessions"),
                ("deals", "pending_deals"),
            )
            for part, attr in mapping:
                if part in parts and getattr(snap, part) is not None:
                    changes[attr] = getattr(snap, part)
            if "blockers" in parts and snap.blockers is not None:
                changes["blockers"] = [Blocker(t, m) for t, m in snap.blockers]
            if "popup" in parts and snap.popup_state is not None:
                changes["popup_state"] = snap.popup_state
            failed = [
                p for p in parts if p not in ("identity", "popup") and p in snap.errors
            ]
            if failed:
                raise ConnectionError(f"refresh failed for {failed}: {snap.errors}")
        else:
            if "overview" in parts:
                changes["overview"] = await gs.get_game_overview()
            if "tech" in parts:
                changes["tech"] = await gs.get_tech_civics()
            if "progress" in parts:
                changes["progress"] = await gs.get_progress_types()
            if "cities" in parts:
                changes["cities"] = (await gs.get_cities())[0]
            if "units" in parts:
                changes["units"] = await gs.get_units()
            if "sessions" in parts:
                changes["diplomacy_sessions"] = await gs.get_diplomacy_sessions()
            if "deals" in parts:
                changes["pending_deals"] = await gs.get_pending_deals()
            if "blockers" in parts:
                changes["blockers"] = [
                    Blocker(t, m) for t, m in await gs.get_end_turn_blockers()
                ]
        self._counter += 1
        turn = changes.get("overview", previous.overview).turn
        self._version = f"{previous.civ}:{previous.seed}:T{turn}:{self._counter}"
        core = dataclasses.replace(previous, version=self._version, **changes)
        if "blockers" in parts or "units" in parts:
            core = await self._with_promotable(core)
        return core

    async def _wonder_types(self, identity: tuple[str, int]) -> set[str]:
        if self._wonders is None or self._wonders[0] != identity:
            self._wonders = (identity, await self.gs.get_wonder_types())
        return self._wonders[1]

    async def inputs(self, spec: DecisionSpec, core: CoreObservation) -> DecisionInputs:
        gs = self.gs
        eid = spec.entity_id
        match spec.category:
            case DecisionCategory.UNIT:
                unit = core.unit(eid)
                if unit is None:
                    return DecisionInputs()
                space = await gs.get_unit_action_space(unit.unit_index)
                tiles = await gs.get_map_area(unit.x, unit.y, self.nearby_radius)
                status = destinations = None
                if unit.unit_type == "UNIT_TRADER":
                    status = await gs.get_trade_routes()
                    me = next(
                        (t for t in status.traders if t.unit_id == unit.unit_id), None
                    )
                    if status.capacity > status.active_count and not (
                        me is not None and me.on_route
                    ):
                        destinations = await gs.get_trade_destinations(unit.unit_index)
                return DecisionInputs(
                    unit=unit,
                    action_space=space,
                    nearby_tiles=tiles,
                    trade_status=status,
                    trade_destinations=destinations,
                )
            case DecisionCategory.PRODUCTION:
                city = core.city(eid)
                if city is None:
                    return DecisionInputs()
                options = await gs.list_city_production(city.city_id)
                wonders = await self._wonder_types(core.game_identity)
                districts = [
                    o.item_name
                    for o in options
                    if o.category == "DISTRICT" and not o.is_repair
                ]
                wonder_items = [
                    o.item_name
                    for o in options
                    if o.category == "BUILDING"
                    and o.item_name in wonders
                    and not o.is_repair
                ]
                placements: dict[str, list[Any]] | None = None
                errors: dict[str, str] | None = None
                if (districts or wonder_items) and hasattr(gs, "get_placement_options"):
                    placements, errors = await gs.get_placement_options(
                        city.city_id, districts, wonder_items
                    )
                return DecisionInputs(
                    city=city,
                    production_options=options,
                    wonder_types=sorted(wonders),
                    placements=placements,
                    placement_errors=errors,
                )
            case DecisionCategory.DIPLOMACY:
                session = next(
                    (s for s in core.diplomacy_sessions if s.other_player_id == eid),
                    None,
                )
                return DecisionInputs(session=session)
            case DecisionCategory.DEAL:
                deal = next(
                    (d for d in core.pending_deals if d.other_player_id == eid), None
                )
                return DecisionInputs(deal=deal)
            case DecisionCategory.POLICY:
                return DecisionInputs(policies=await gs.get_policies())
            case DecisionCategory.GOVERNMENT:
                return DecisionInputs(governments=await gs.get_available_governments())
            case DecisionCategory.ENVOY:
                return DecisionInputs(envoys=await gs.get_city_states())
            case DecisionCategory.PANTHEON:
                return DecisionInputs(pantheon=await gs.get_pantheon_status())
            case DecisionCategory.RESEARCH | DecisionCategory.CIVIC:
                return DecisionInputs(progress=core.progress)
            case DecisionCategory.CITY_ATTACK:
                city = core.city(eid)
                if city is None:
                    return DecisionInputs()
                return DecisionInputs(
                    city_attack_city=city,
                    city_targets=await gs.get_city_attack_targets(city.city_id),
                )
            case DecisionCategory.CAPTURED_CITY:
                return DecisionInputs(captured_city=await gs.get_captured_city())
            case DecisionCategory.SPY_ESCAPE:
                return DecisionInputs(spy_escape=await gs.get_spy_escape_choice())
            case DecisionCategory.RELIGION | DecisionCategory.BELIEF:
                return DecisionInputs(religion=await gs.get_religion_founding_status())
            case DecisionCategory.GREAT_PERSON:
                people = core.great_people
                if people is None:
                    people = await gs.get_great_people()
                return DecisionInputs(great_people=list(people))
            case DecisionCategory.DEDICATION:
                return DecisionInputs(dedications=await gs.get_dedications())
            case DecisionCategory.GOVERNOR:
                return DecisionInputs(governors=await gs.get_governors())
            case DecisionCategory.PROMOTION:
                unit_id = int(eid.split(":", 1)[1]) if isinstance(eid, str) else eid
                pu = next((u for u in core.promotable if u.unit_id == unit_id), None)
                if pu is None:
                    return DecisionInputs()
                return DecisionInputs(
                    promotable=pu, promotions=await gs.get_unit_promotions(unit_id)
                )
        return DecisionInputs()
