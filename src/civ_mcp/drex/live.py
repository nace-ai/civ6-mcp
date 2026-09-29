"""GameState-backed observation for the decision loop."""

from __future__ import annotations

import dataclasses
from typing import Any

from civ_mcp.drex.candidates import DecisionCategory
from civ_mcp.drex.observation import (
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
            return self._from_snapshot(await gs.get_core_snapshot())
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
        return CoreObservation(
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
        return dataclasses.replace(previous, version=self._version, **changes)

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
                return DecisionInputs(unit=unit, action_space=space, nearby_tiles=tiles)
            case DecisionCategory.PRODUCTION:
                city = core.city(eid)
                if city is None:
                    return DecisionInputs()
                options = await gs.list_city_production(city.city_id)
                wonders = await self._wonder_types(core.game_identity)
                return DecisionInputs(
                    city=city, production_options=options, wonder_types=sorted(wonders)
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
        return DecisionInputs()
