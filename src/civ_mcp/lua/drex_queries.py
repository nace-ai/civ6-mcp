"""Lua queries added for the decision-only controller (Phase 2 kinds).

Kept out of ``civ_mcp.lua.__init__`` on purpose: ``GameState`` imports this
module directly, so it does not collide with other work in the package's
re-export list.
"""

from __future__ import annotations

from dataclasses import dataclass

from civ_mcp.lua._helpers import SENTINEL


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
      local tag = (xp >= needed and canAny) and "PROMOTABLE|" or "SKIP|"
      local tail = (xp >= needed) and "" or "|below_threshold"
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
