"""Lua queries added for the decision-only controller (Phase 2 kinds).

Kept out of ``civ_mcp.lua.__init__`` on purpose: ``GameState`` imports this
module directly, so it does not collide with other work in the package's
re-export list.
"""

from __future__ import annotations

from dataclasses import dataclass

from civ_mcp.lua._helpers import SENTINEL


@dataclass
class CityAttackTarget:
    x: int
    y: int
    unit_type: str
    owner_id: int
    hp: int
    max_hp: int


@dataclass
class PromotableUnit:
    unit_id: int
    unit_index: int
    unit_type: str
    xp: int
    xp_needed: int
    promotion_count: int


def build_promotable_units_query() -> str:
    """GameCore: units that genuinely have a promotion available.

    GameCore ``SetPromotion`` does not consume XP, so ``CanPromote()`` alone
    stays true forever; the XP-threshold formula the legacy end-turn code uses
    (needed = T1 * (n+1) * (n+2) / 2 for n promotions held) is applied too.
    """
    return f"""
local me = Game.GetLocalPlayer()
for i, u in Players[me]:GetUnits():Members() do
  if u:GetX() ~= -9999 then
    local ok, exp = pcall(function() return u:GetExperience() end)
    local ui = GameInfo.Units[u:GetType()]
    local promClass = ui and ui.PromotionClass or ""
    if ok and exp and promClass ~= "" then
      local n = 0
      for p in GameInfo.UnitPromotions() do
        if p.PromotionClass == promClass and exp:HasPromotion(p.Index) then n = n + 1 end
      end
      local t1 = exp:GetExperienceForNextLevel()
      local xp = exp:GetExperiencePoints()
      local needed = t1 * (n + 1) * (n + 2) / 2
      local canAny = false
      for p in GameInfo.UnitPromotions() do
        if p.PromotionClass == promClass and not exp:HasPromotion(p.Index) then
          local okc, c = pcall(function() return exp:CanPromote(p.Index) end)
          if okc and c then canAny = true break end
        end
      end
      local stored = 0
      pcall(function() stored = exp:GetStoredPromotions() end)
      -- the engine's stored-promotion counter is what raises the blocker;
      -- a unit with one pending is promotable whatever the XP formula says
      local due = (xp >= needed) or (stored > 0)
      local tag = (due and canAny) and "PROMOTABLE|" or "SKIP|"
      local tail = due and "" or "|below_threshold"
      print(tag .. u:GetID() .. "|" .. (u:GetID() % 65536) .. "|" .. (ui.UnitType or "UNKNOWN") .. "|" .. xp .. "|" .. needed .. "|" .. n .. tail)
    end
  end
end
print("{SENTINEL}")
"""


def parse_promotable_units(lines: list[str]) -> list[PromotableUnit]:
    out: list[PromotableUnit] = []
    for line in lines:
        if not line.startswith("PROMOTABLE|"):
            continue
        p = line.split("|")
        if len(p) < 7:
            continue
        out.append(
            PromotableUnit(
                int(p[1]),
                int(p[2]),
                p[3],
                int(float(p[4])),
                int(float(p[5])),
                int(p[6]),
            )
        )
    return out


def build_add_belief(belief_type: str) -> str:
    """InGame: add a belief to the player's religion (enhancer, worship, a
    second follower belief...) through the same PlayerOperation
    ``build_found_religion`` uses for its beliefs."""
    return f"""
local me = Game.GetLocalPlayer()
local b = GameInfo.Beliefs["{belief_type}"]
if not b then print("ERR:BELIEF_NOT_FOUND|{belief_type}"); print("{SENTINEL}"); return end
local p = {{}}
p[PlayerOperations.PARAM_BELIEF_TYPE] = b.Hash
UI.RequestPlayerOperation(me, PlayerOperations.ADD_BELIEF, p)
print("OK:BELIEF_ADDED|" .. Locale.Lookup(b.Name))
print("{SENTINEL}")
"""


def build_city_attack_targets_query(city_id: int) -> str:
    """InGame: hostile units the city may range-attack right now, from the
    engine's own target list (the same call ``build_city_attack`` checks)."""
    return f"""
local me = Game.GetLocalPlayer()
local pCity = Players[me]:GetCities():FindID({city_id} % 65536)
if pCity == nil then print("ERR:CITY_NOT_FOUND"); print("{SENTINEL}"); return end
local w = Map.GetGridSize()
local targets = CityManager.GetCommandTargets(pCity, CityCommandTypes.RANGE_ATTACK)
if targets then
  for _, tbl in pairs(targets) do
    if type(tbl) == "table" then
      for _, idx in ipairs(tbl) do
        local x, y = idx % w, math.floor(idx / w)
        local pu = Map.GetUnitsAt(x, y)
        if pu then
          for u in pu:Units() do
            if u:GetOwner() ~= me then
              local info = GameInfo.Units[u:GetType()]
              print("TARGET|" .. x .. "|" .. y .. "|" .. (info and info.UnitType or "UNKNOWN") .. "|" .. u:GetOwner() .. "|" .. (u:GetMaxDamage() - u:GetDamage()) .. "|" .. u:GetMaxDamage())
            end
          end
        end
      end
    end
  end
end
print("{SENTINEL}")
"""


def parse_city_attack_targets(lines: list[str]) -> list[CityAttackTarget]:
    out: list[CityAttackTarget] = []
    for line in lines:
        if not line.startswith("TARGET|"):
            continue
        p = line.split("|")
        if len(p) < 7:
            continue
        out.append(
            CityAttackTarget(
                int(p[1]), int(p[2]), p[3], int(p[4]), int(p[5]), int(p[6])
            )
        )
    return out


def build_end_turn_blocking_types_query() -> str:
    """InGame: dump the engine's ``EndTurnBlockingTypes`` enum (name|value)."""
    return f"""
for k, v in pairs(EndTurnBlockingTypes) do print("BT|" .. tostring(k) .. "|" .. tostring(v)) end
print("{SENTINEL}")
"""


def parse_end_turn_blocking_types(lines: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for line in lines:
        if not line.startswith("BT|"):
            continue
        p = line.split("|")
        if len(p) >= 3:
            try:
                out[p[1]] = int(p[2])
            except ValueError:
                continue
    return out


def build_dismiss_blocker_notifications(blocking_types: list[str]) -> str:
    """InGame housekeeping: dismiss the notifications behind the named
    end-turn blocker types (used when a supported blocker stands but the
    engine offers nothing left to decide, e.g. a stale promotion flag)."""
    names = ", ".join(f'["{t}"] = true' for t in blocking_types)
    return f"""
local me = Game.GetLocalPlayer()
local wanted = {{ {names} }}
local n = 0
local list = NotificationManager.GetList(me)
if list then
  for _, nid in ipairs(list) do
    local e = NotificationManager.Find(me, nid)
    if e and not e:IsDismissed() then
      local bt = e:GetEndTurnBlocking()
      local hit = false
      if bt and bt ~= 0 then
        for k, v in pairs(EndTurnBlockingTypes) do
          if v == bt and wanted[k] then hit = true break end
        end
      end
      if not hit and wanted["ENDTURN_BLOCKING_UNIT_PROMOTION"] then
        local ok, t = pcall(function() return e:GetType() end)
        if ok and t == NotificationTypes.NOTIFICATION_UNIT_PROMOTION_AVAILABLE then hit = true end
      end
      if hit then
        pcall(function() NotificationManager.Dismiss(me, nid) end)
        n = n + 1
      end
    end
  end
end
print("OK:DISMISSED|" .. n)
print("{SENTINEL}")
"""
