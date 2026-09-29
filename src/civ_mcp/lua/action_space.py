"""Typed legal-action queries for decision-only controllers.

``build_unit_action_space_query`` answers, in one round trip per unit, which
destinations the engine says are reachable this turn, which currently visible
hostile units the engine accepts as attack targets, and whether founding,
fortifying or healing is possible. Only the unit's own tile and its attack
targets are probed with ``CanStartOperation``; remote BUILD_IMPROVEMENT
probes are avoided (see ``build_builder_tasks_query``).
"""

from __future__ import annotations

from civ_mcp.lua._helpers import _LUA_RES_VISIBLE, SENTINEL, _int
from civ_mcp.lua.models import (
    AttackTarget,
    GovernmentOption,
    ProgressTypes,
    ReachableTile,
    UnitActionSpace,
    UnitState,
)


def build_unit_action_space_query(unit_index: int) -> str:
    """InGame context: reachable tiles, visible attack targets, order eligibility."""
    return f"""
local me = Game.GetLocalPlayer()
local unit = UnitManager.GetUnit(me, {unit_index})
if unit == nil then print("ERR:UNIT_NOT_FOUND"); print("{SENTINEL}"); return end
local ux, uy = unit:GetX(), unit:GetY()
if ux == -9999 then print("ERR:UNIT_NOT_ON_MAP"); print("{SENTINEL}"); return end
local info = GameInfo.Units[unit:GetType()]
local ut = info and info.UnitType or "UNKNOWN"
local civilian = (info ~= nil and info.FormationClass == "FORMATION_CLASS_CIVILIAN")
local moves = unit:GetMovesRemaining()
local vis = PlayersVisibility[me]
local pDiplo = Players[me]:GetDiplomacy()
local pTech = Players[me]:GetTechs()
{_LUA_RES_VISIBLE}
local function flag(b) if b then return "1" else return "0" end end
local function can(fn) local ok, r = pcall(fn); return ok and r and true or false end

local canFound = false
if moves > 0 and can(function() return UnitManager.CanStartOperation(unit, UnitOperationTypes.FOUND_CITY, nil, true) end) then
    local plot = Map.GetPlot(ux, uy)
    canFound = plot ~= nil and not plot:IsWater() and not plot:IsMountain()
    for i = 0, 62 do
        if canFound and Players[i] and Players[i]:IsAlive() then
            local cities = Players[i]:GetCities()
            if cities then
                for _, c in cities:Members() do
                    if Map.GetPlotDistance(ux, uy, c:GetX(), c:GetY()) <= 3 then canFound = false; break end
                end
            end
        end
    end
end
local canFortify = moves > 0 and can(function() return UnitManager.CanStartOperation(unit, UnitOperationTypes.FORTIFY, nil, true) end)
local healRow = GameInfo.UnitOperations["UNITOPERATION_HEAL"]
local canHeal = moves > 0 and unit:GetDamage() > 0 and healRow ~= nil
    and can(function() return UnitManager.CanStartOperation(unit, healRow.Hash, nil, nil) end)
local zoc = can(function() return unit:HasMovedIntoZOC() end)
local fortTurns = 0
pcall(function() fortTurns = unit:GetFortifyTurns() end)
print("UNIT|" .. unit:GetID() .. "|{unit_index}|" .. ut .. "|" .. ux .. "," .. uy .. "|" .. moves .. "|" .. flag(civilian) .. "|" .. flag(canFound) .. "|" .. flag(canFortify) .. "|" .. flag(canHeal) .. "|" .. flag(zoc) .. "|" .. fortTurns .. "|" .. (unit:GetMaxDamage() - unit:GetDamage()) .. "/" .. unit:GetMaxDamage())

if moves > 0 then
    local okR, reach = pcall(function() return UnitManager.GetReachableMovement(unit) end)
    if okR and reach then
        for _, pIdx in ipairs(reach) do
            local p = Map.GetPlotByIndex(pIdx)
            if p and not (p:GetX() == ux and p:GetY() == uy) and vis:IsRevealed(pIdx) then
                local px, py = p:GetX(), p:GetY()
                local visible = vis:IsVisible(pIdx)
                local conflict, foreign = false, false
                local us = Map.GetUnitsAt(px, py)
                if us then
                    for other in us:Units() do
                        if other:GetOwner() == me then
                            local oi = GameInfo.Units[other:GetType()]
                            local oCiv = (oi ~= nil and oi.FormationClass == "FORMATION_CLASS_CIVILIAN")
                            if oCiv == civilian then conflict = true end
                        elseif visible then
                            foreign = true
                        end
                    end
                end
                local terrain = GameInfo.Terrains[p:GetTerrainType()].TerrainType
                local fIdx = p:GetFeatureType()
                local feature = (fIdx >= 0) and GameInfo.Features[fIdx].FeatureType or "none"
                local resource = "none"
                local rIdx = p:GetResourceType()
                if rIdx >= 0 then
                    local rEntry = GameInfo.Resources[rIdx]
                    if rEntry and resVisible(rEntry) then
                        resource = rEntry.ResourceType .. ":" .. (rEntry.ResourceClassType or "")
                    end
                end
                local owner = -2
                if visible then owner = p:GetOwner() end
                print("REACH|" .. px .. "," .. py .. "|" .. terrain .. "|" .. feature .. "|" .. resource .. "|" .. flag(p:IsHills()) .. "|" .. flag(p:IsRiver()) .. "|" .. owner .. "|" .. (visible and "visible" or "revealed") .. "|" .. flag(conflict) .. "|" .. flag(foreign) .. "|" .. Map.GetPlotDistance(ux, uy, px, py))
            end
        end
    end
end

local cs = info and info.Combat or 0
local rs = info and info.RangedCombat or 0
if moves > 0 and not civilian and (cs > 0 or rs > 0) then
    local rng = 1
    if rs > 0 then rng = math.max(1, info.Range or 1) end
    local start = Map.GetPlot(ux, uy)
    local seen = {{}}
    seen[start:GetIndex()] = true
    local frontier = {{start}}
    for d = 1, rng do
        local nextF = {{}}
        for _, fp in ipairs(frontier) do
            for dir = 0, 5 do
                local adj = Map.GetAdjacentPlot(fp:GetX(), fp:GetY(), dir)
                if adj and not seen[adj:GetIndex()] then
                    seen[adj:GetIndex()] = true
                    table.insert(nextF, adj)
                    if vis:IsVisible(adj:GetIndex()) then
                        local tx, ty = adj:GetX(), adj:GetY()
                        local best = nil
                        local tus = Map.GetUnitsAt(tx, ty)
                        if tus then
                            for other in tus:Units() do
                                local oo = other:GetOwner()
                                if oo ~= me and (oo == 63 or pDiplo:IsAtWarWith(oo)) then
                                    local oi = GameInfo.Units[other:GetType()]
                                    if best == nil or (oi and oi.Combat or 0) > 0 then best = other end
                                end
                            end
                        end
                        if best then
                            local params = {{}}
                            params[UnitOperationTypes.PARAM_X] = tx
                            params[UnitOperationTypes.PARAM_Y] = ty
                            local kind = nil
                            if rs > 0 then
                                if can(function() return UnitManager.CanStartOperation(unit, UnitOperationTypes.RANGE_ATTACK, nil, params) end) then kind = "RANGED" end
                            elseif d == 1 and not zoc then
                                params[UnitOperationTypes.PARAM_MODIFIERS] = UnitOperationMoveModifiers.ATTACK
                                if can(function() return UnitManager.CanStartOperation(unit, UnitOperationTypes.MOVE_TO, nil, params) end) then kind = "MELEE" end
                            end
                            if kind then
                                local bi = GameInfo.Units[best:GetType()]
                                local owner = best:GetOwner()
                                local ownerName = "Barbarian"
                                if owner ~= 63 then
                                    pcall(function() ownerName = Locale.Lookup(PlayerConfigurations[owner]:GetCivilizationShortDescription()) end)
                                end
                                print("TARGET|" .. tx .. "," .. ty .. "|" .. kind .. "|" .. owner .. "|" .. ownerName:gsub("|", "/") .. "|" .. (bi and bi.UnitType or "UNKNOWN") .. "|" .. (best:GetMaxDamage() - best:GetDamage()) .. "/" .. best:GetMaxDamage() .. "|" .. d .. "|" .. (bi and bi.Combat or 0))
                            end
                        end
                    end
                end
            end
        end
        frontier = nextF
    end
end
print("{SENTINEL}")
"""


def _xy(s: str) -> tuple[int, int]:
    x, y = s.split(",")
    return int(x), int(y)


def _resource(raw: str) -> str | None:
    return None if raw == "none" else raw.split(":", 1)[0]


def parse_unit_action_space(lines: list[str]) -> UnitActionSpace | None:
    space: UnitActionSpace | None = None
    for line in lines:
        if line.startswith("ERR:"):
            return None
        parts = line.split("|")
        try:
            if parts[0] == "UNIT" and len(parts) >= 13:
                x, y = _xy(parts[4])
                hp, max_hp = parts[12].split("/")
                space = UnitActionSpace(
                    unit_id=int(parts[1]),
                    unit_index=int(parts[2]),
                    unit_type=parts[3],
                    x=x,
                    y=y,
                    moves_remaining=float(parts[5]),
                    is_civilian=parts[6] == "1",
                    can_found=parts[7] == "1",
                    can_fortify=parts[8] == "1",
                    can_heal=parts[9] == "1",
                    moved_into_zoc=parts[10] == "1",
                    fortify_turns=_int(parts[11]),
                    hp=_int(hp),
                    max_hp=_int(max_hp),
                )
            elif parts[0] == "REACH" and space is not None and len(parts) >= 12:
                x, y = _xy(parts[1])
                space.reachable.append(
                    ReachableTile(
                        x=x,
                        y=y,
                        terrain=parts[2],
                        feature=None if parts[3] == "none" else parts[3],
                        resource=_resource(parts[4]),
                        is_hills=parts[5] == "1",
                        is_river=parts[6] == "1",
                        owner_id=int(parts[7]),
                        visibility=parts[8],
                        own_stack_conflict=parts[9] == "1",
                        visible_foreign_unit=parts[10] == "1",
                        distance=int(parts[11]),
                    )
                )
            elif parts[0] == "TARGET" and space is not None and len(parts) >= 9:
                x, y = _xy(parts[1])
                hp, max_hp = parts[6].split("/")
                space.targets.append(
                    AttackTarget(
                        x=x,
                        y=y,
                        attack_type=parts[2],
                        owner_id=int(parts[3]),
                        owner_name=parts[4],
                        unit_type=parts[5],
                        hp=_int(hp),
                        max_hp=_int(max_hp),
                        distance=int(parts[7]),
                        combat_strength=int(parts[8]),
                    )
                )
        except (ValueError, IndexError):
            continue
    return space


def build_wonder_types_query() -> str:
    """Static game metadata: building types that are wonders (need placement)."""
    return f"""
for b in GameInfo.Buildings() do
    if b.IsWonder then print("WONDER|" .. b.BuildingType) end
end
print("{SENTINEL}")
"""


def parse_wonder_types(lines: list[str]) -> set[str]:
    return {line.split("|", 1)[1] for line in lines if line.startswith("WONDER|")}


def build_progress_types_query() -> str:
    """GameCore: current research and civic as type names (not localized)."""
    return f"""
local me = Game.GetLocalPlayer()
local t = Players[me]:GetTechs():GetResearchingTech()
local c = Players[me]:GetCulture():GetProgressingCivic()
local tt, ct = "NONE", "NONE"
if t and t >= 0 and GameInfo.Technologies[t] then tt = GameInfo.Technologies[t].TechnologyType end
if c and c >= 0 and GameInfo.Civics[c] then ct = GameInfo.Civics[c].CivicType end
print("PROGRESS|" .. tt .. "|" .. ct)
print("{SENTINEL}")
"""


def parse_progress_types(lines: list[str]) -> ProgressTypes:
    for line in lines:
        if line.startswith("PROGRESS|"):
            parts = line.split("|")
            if len(parts) >= 3:
                return ProgressTypes(
                    research_type=None if parts[1] == "NONE" else parts[1],
                    civic_type=None if parts[2] == "NONE" else parts[2],
                )
    return ProgressTypes(research_type=None, civic_type=None)


def build_eligibility_query(kind: str, type_name: str) -> str:
    """GameCore: fresh engine legality check for a tech or civic target."""
    if kind == "tech":
        return f"""
local me = Game.GetLocalPlayer()
local te = Players[me]:GetTechs()
local row = GameInfo.Technologies["{type_name}"]
if row == nil then print("ELIGIBLE|0|not_found")
elseif te:HasTech(row.Index) then print("ELIGIBLE|0|completed")
elseif te:CanResearch(row.Index) then print("ELIGIBLE|1|engine")
else print("ELIGIBLE|0|engine") end
print("{SENTINEL}")
"""
    if kind == "civic":
        return f"""
local me = Game.GetLocalPlayer()
local cu = Players[me]:GetCulture()
local row = GameInfo.Civics["{type_name}"]
if row == nil then print("ELIGIBLE|0|not_found")
elseif cu:HasCivic(row.Index) then print("ELIGIBLE|0|completed")
else
    local ok, r = pcall(function() return cu:CanProgress(row.Index) end)
    if ok and r ~= nil then
        print("ELIGIBLE|" .. (r and "1" or "0") .. "|engine")
    else
        local met = true
        for p in GameInfo.CivicPrereqs() do
            if p.Civic == "{type_name}" then
                local pe = GameInfo.Civics[p.PrereqCivic]
                if pe and not cu:HasCivic(pe.Index) then met = false end
            end
        end
        print("ELIGIBLE|" .. (met and "1" or "0") .. "|prereqs")
    end
end
print("{SENTINEL}")
"""
    raise ValueError(f"unknown eligibility kind: {kind}")


def parse_eligibility(lines: list[str]) -> tuple[bool, str]:
    for line in lines:
        if line.startswith("ELIGIBLE|"):
            parts = line.split("|")
            return parts[1] == "1", parts[2] if len(parts) > 2 else ""
    return False, "no_response"


def build_unit_state_query(unit_index: int) -> str:
    """GameCore: position, moves, fortification, HP and charges of one unit."""
    return f"""
local me = Game.GetLocalPlayer()
local u = Players[me]:GetUnits():FindID({unit_index})
if u == nil or u:GetX() == -9999 then print("STATE|GONE") else
    local ft, ch = 0, 0
    pcall(function() ft = u:GetFortifyTurns() end)
    pcall(function() ch = u:GetBuildCharges() end)
    print("STATE|" .. u:GetX() .. "|" .. u:GetY() .. "|" .. u:GetMovesRemaining() .. "|" .. ft .. "|" .. (u:GetMaxDamage() - u:GetDamage()) .. "|" .. ch)
end
print("{SENTINEL}")
"""


def parse_unit_state(lines: list[str]) -> UnitState | None:
    for line in lines:
        if line.startswith("STATE|"):
            parts = line.split("|")
            if len(parts) < 7:
                return None
            try:
                return UnitState(
                    x=int(parts[1]),
                    y=int(parts[2]),
                    moves_remaining=float(parts[3]),
                    fortify_turns=_int(parts[4]),
                    hp=_int(parts[5]),
                    build_charges=_int(parts[6]),
                )
            except ValueError:
                return None
    return None


def parse_available_governments(lines: list[str]) -> list[GovernmentOption]:
    """Parse ``build_available_governments_query`` output."""
    out: list[GovernmentOption] = []
    for line in lines:
        if not line.startswith("GOV|"):
            continue
        parts = line.split("|")
        if len(parts) < 5:
            continue
        try:
            index = int(parts[2])
        except ValueError:
            continue
        out.append(
            GovernmentOption(
                government_type=parts[1],
                index=index,
                is_current=parts[3] == "CURRENT",
                name=parts[4],
                slots=[s for s in parts[5].split(",") if s] if len(parts) > 5 else [],
                bonus=parts[6] if len(parts) > 6 else "",
            )
        )
    return out


def build_government_change_considered() -> str:
    """Record that the player keeps the current government for now (InGame)."""
    return f"""
local me = Game.GetLocalPlayer()
Players[me]:GetCulture():SetGovernmentChangeConsidered(true)
print("OK:GOVERNMENT_CHANGE_CONSIDERED")
print("{SENTINEL}")
"""
