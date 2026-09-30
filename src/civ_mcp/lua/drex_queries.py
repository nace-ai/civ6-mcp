"""Lua queries added for the decision-only controller (Phase 2 kinds).

Kept out of ``civ_mcp.lua.__init__`` on purpose: ``GameState`` imports this
module directly, so it does not collide with other work in the package's
re-export list.
"""

from __future__ import annotations

from dataclasses import dataclass

from civ_mcp.lua._helpers import SENTINEL
from civ_mcp.lua.batch import build_batch, split_batch


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


@dataclass
class Placement:
    """One candidate tile for a district or wonder, ranked by the advisor."""

    x: int
    y: int
    score: int  # district: total adjacency; wonder: displacement score
    note: str  # terrain and yields, for the option description


def build_placement_batch(
    city_id: int, districts: list[str], wonders: list[str]
) -> str:
    """InGame: every district and wonder advisor for one city in one round trip.
    Sections are ``district:<type>`` / ``wonder:<name>``; each advisor's own
    ERR: bail stays inside its section."""
    from civ_mcp import lua as lq

    sections = [
        (f"district:{d}", lq.build_district_advisor_query(city_id, d))
        for d in districts
    ] + [(f"wonder:{w}", lq.build_wonder_advisor_query(city_id, w)) for w in wonders]
    return build_batch(sections)


def _pretty_enum(value: str) -> str:
    for prefix in ("TERRAIN_", "FEATURE_", "RESOURCE_", "IMPROVEMENT_"):
        if value.startswith(prefix):
            value = value[len(prefix) :]
    return value.replace("_", " ").title()


def _wonder_note(w) -> str:
    parts = [_pretty_enum(w.terrain)]
    if w.feature not in ("none", "", "FEATURE_NONE"):
        parts.append(_pretty_enum(w.feature))
    note = " ".join(parts)
    if w.has_river:
        note += "; river"
    if w.is_coastal:
        note += "; coast"
    lost = [
        _pretty_enum(v)
        for v in (w.resource, w.improvement)
        if v not in ("none", "", "RESOURCE_NONE", "IMPROVEMENT_NONE")
    ]
    if lost:
        note += "; displaces " + " + ".join(lost)
    return note


def parse_placement_batch(
    lines: list[str],
) -> tuple[dict[str, list[Placement]], dict[str, str]]:
    """(placements by item, best first; errors by item)."""
    from civ_mcp import lua as lq

    sections, errors_by_section = split_batch(lines)
    placements: dict[str, list[Placement]] = {}
    errors: dict[str, str] = {}
    for section, body in sections.items():
        kind, _, item = section.partition(":")
        if section in errors_by_section:
            errors[item] = errors_by_section[section]
            continue
        err = next((ln for ln in body if ln.startswith("ERR:")), None)
        if err is not None:
            errors[item] = err[len("ERR:") :]
            continue
        if kind == "district":
            found = [
                Placement(
                    d.x,
                    d.y,
                    d.total_adjacency,
                    d.terrain_desc
                    + (
                        "; " + ", ".join(f"{k}:{v}" for k, v in d.adjacency.items())
                        if d.adjacency
                        else ""
                    ),
                )
                for d in lq.parse_district_advisor_response(body)
            ]
        else:
            # displacement_score is lower = better; negate so one scale (higher
            # = better) serves both kinds, shortlist and the option text.
            found = [
                Placement(
                    w.x,
                    w.y,
                    -w.displacement_score,
                    _wonder_note(w),
                )
                for w in lq.parse_wonder_advisor_response(body)
            ]
        found.sort(key=lambda pl: (-pl.score, pl.x, pl.y))
        placements[item] = found
    return placements, errors


# --------------------------------------------------------------- Phase 4 prompts

CAPTURE_ACTIONS = ("keep", "raze", "liberate_founder", "liberate_previous", "reject")
_CAPTURE_DIRECTIVES = {
    "keep": "KEEP",
    "raze": "RAZE",
    "liberate_founder": "LIBERATE_FOUNDER",
    "liberate_previous": "LIBERATE_PREVIOUS_OWNER",
    "reject": "REJECT",
}

_LUA_CIV_NAME = """
local function civName(pid)
  if pid == nil or pid < 0 then return "" end
  local cfg = PlayerConfigurations[pid]
  if cfg == nil then return "" end
  local ok, s = pcall(function() return Locale.Lookup(cfg:GetCivilizationShortDescription()) end)
  return (ok and s) or ""
end
"""


@dataclass
class CapturedCity:
    """The city the engine wants a keep/raze/liberate decision for."""

    city_id: int
    name: str
    x: int
    y: int
    population: int
    districts: int
    source: str  # "captured" (conquest) or "rebelled" (loyalty flip)
    original_owner: str  # civ short name, "" if unknown
    previous_owner: str
    options: list[str]  # subset of CAPTURE_ACTIONS the engine allows right now


def build_captured_city_query() -> str:
    """InGame: the pending captured/rebelled city and the directives the
    engine accepts for it (the same ``CanStartCommand`` checks RazeCity.lua
    makes before showing each button)."""
    checks = "\n".join(
        f"do local p = {{}} p[UnitOperationTypes.PARAM_FLAGS] = CityDestroyDirectives.{d}\n"
        f"  local ok, can = pcall(function() return CityManager.CanStartCommand(city, CityCommandTypes.DESTROY, p) end)\n"
        f'  if ok and can then print("OPTION|{a}") end end'
        for a, d in _CAPTURE_DIRECTIVES.items()
    )
    return f"""
local me = Game.GetLocalPlayer()
local player = Players[me]
local city = player:GetCities():GetNextRebelledCity()
local source = "rebelled"
if city == nil then city = player:GetCities():GetNextCapturedCity() source = "captured" end
if city == nil then print("NO_PENDING_CITY"); print("{SENTINEL}"); return end
{_LUA_CIV_NAME}
local orig, prev, nd = -1, -1, 0
pcall(function() orig = city:GetOriginalOwner() end)
pcall(function() prev = city:GetOwnerBeforeOccupation() end)
pcall(function() nd = city:GetDistricts():GetNumZonedDistrictsRequiringPopulation() end)
print("CAPTURED|" .. (city:GetID() + me * 65536) .. "|" .. Locale.Lookup(city:GetName()) .. "|" .. city:GetX() .. "|" .. city:GetY() .. "|" .. city:GetPopulation() .. "|" .. nd .. "|" .. source .. "|" .. civName(orig) .. "|" .. civName(prev))
{checks}
print("{SENTINEL}")
"""


def parse_captured_city(lines: list[str]) -> CapturedCity | None:
    city: CapturedCity | None = None
    offered: set[str] = set()
    for line in lines:
        if line.startswith("CAPTURED|"):
            p = line.split("|")
            if len(p) < 10:
                continue
            city = CapturedCity(
                int(p[1]),
                p[2],
                int(p[3]),
                int(p[4]),
                int(p[5]),
                int(p[6]),
                p[7],
                p[8],
                p[9],
                [],
            )
        elif line.startswith("OPTION|"):
            offered.add(line.split("|", 1)[1].strip())
    if city is None:
        return None
    city.options = [a for a in CAPTURE_ACTIONS if a in offered]
    return city


ESCAPE_DISTRICTS = (  # the four routes EspionageEscape.lua offers, fastest first
    "DISTRICT_AERODROME",
    "DISTRICT_HARBOR",
    "DISTRICT_COMMERCIAL_HUB",
    "DISTRICT_CITY_CENTER",
)


@dataclass
class EscapeChoice:
    spy_unit_id: int
    spy_name: str
    city_name: str
    x: int
    y: int
    routes: list[str]  # available escape districts, fastest first


def _lua_escaping_spy() -> str:
    """Shared prefix: find the escaping spy and its city, but only after one
    of the two escape blockers' notifications is found standing —
    ``GetNextEscapingSpyID`` crashes the game when nothing is escaping."""
    return f"""
local me = Game.GetLocalPlayer()
local prompt = false
local list = NotificationManager.GetList(me)
if list then
  for _, nid in ipairs(list) do
    local e = NotificationManager.Find(me, nid)
    if e and not e:IsDismissed() then
      local bt = e:GetEndTurnBlocking()
      if bt == EndTurnBlockingTypes.ENDTURN_BLOCKING_SPY_CHOOSE_ESCAPE_ROUTE or bt == EndTurnBlockingTypes.ENDTURN_BLOCKING_SPY_CHOOSE_DRAGNET_PRIORITY then prompt = true end
    end
  end
end
if not prompt then print("NO_ESCAPING_SPY"); print("{SENTINEL}"); return end
local ok_esc, spyID = pcall(function() return Players[me]:GetDiplomacy():GetNextEscapingSpyID() end)
if not ok_esc or spyID == nil or spyID < 0 then print("NO_ESCAPING_SPY"); print("{SENTINEL}"); return end
local spy = Players[me]:GetUnits():FindID(spyID)
if spy == nil then print("ERR:SPY_NOT_FOUND"); print("{SENTINEL}"); return end
local city = Cities.GetPlotPurchaseCity(spy:GetX(), spy:GetY())
if city == nil then print("ERR:NO_CITY"); print("{SENTINEL}"); return end
local function hasRoute(d)
  if d == "DISTRICT_CITY_CENTER" then return true end
  local row = GameInfo.Districts[d]
  if row == nil then return false end
  local ok, has = pcall(function() return city:GetDistricts():HasDistrict(row.Index, true, true) end)
  return ok and has
end
"""


def build_spy_escape_options_query() -> str:
    """InGame: the caught spy and the escape districts available in its city."""
    routes = "\n".join(
        f'if hasRoute("{d}") then print("ROUTE|{d}") end' for d in ESCAPE_DISTRICTS
    )
    return f"""
{_lua_escaping_spy()}
print("ESCAPE|" .. (spy:GetID() + me * 65536) .. "|" .. Locale.Lookup(spy:GetName()) .. "|" .. Locale.Lookup(city:GetName()) .. "|" .. spy:GetX() .. "|" .. spy:GetY())
{routes}
print("{SENTINEL}")
"""


def parse_spy_escape_options(lines: list[str]) -> EscapeChoice | None:
    choice: EscapeChoice | None = None
    found: set[str] = set()
    for line in lines:
        if line.startswith("ESCAPE|"):
            p = line.split("|")
            if len(p) >= 6:
                choice = EscapeChoice(int(p[1]), p[2], p[3], int(p[4]), int(p[5]), [])
        elif line.startswith("ROUTE|"):
            found.add(line.split("|", 1)[1].strip())
    if choice is None:
        return None
    choice.routes = [d for d in ESCAPE_DISTRICTS if d in found]
    return choice


def build_choose_spy_escape(district_type: str) -> str:
    """InGame: set the caught spy's escape route (``SET_ESCAPE_ROUTE``, as the
    popup does) after re-checking the district is available."""
    if district_type not in ESCAPE_DISTRICTS:
        return f'print("ERR:NOT_AN_ESCAPE_ROUTE|{district_type}"); print("{SENTINEL}")'
    return f"""
{_lua_escaping_spy()}
if not hasRoute("{district_type}") then print("ERR:ROUTE_NOT_AVAILABLE|{district_type}"); print("{SENTINEL}"); return end
local params = {{}}
params[PlayerOperations.PARAM_DISTRICT_TYPE] = GameInfo.Districts["{district_type}"].Index
UI.RequestPlayerOperation(me, PlayerOperations.SET_ESCAPE_ROUTE, params)
pcall(function() local popup = ContextPtr:LookUpControl("/InGame/EspionageEscape") if popup then popup:SetHide(true) end end)
print("OK:ESCAPE_ROUTE|" .. Locale.Lookup(spy:GetName()) .. "|{district_type}")
print("{SENTINEL}")
"""


ARTIFACT_KINDS = {  # kObject.Type in ChooseArtifact.lua; 1, 2 and 5 offer no choice
    0: "unknown origin",
    1: "tribal village",
    2: "barbarian camp",
    3: "battle site",
    4: "shipwreck",
    5: "heroic relic",
}


@dataclass
class ArtifactChoice:
    unit_id: int
    unit_name: str
    x: int
    y: int
    kind: str
    era: str
    players: list[tuple[int, str, str]]  # (player_id, civ name, "acting" | "target")


def _lua_artifact() -> str:
    return f"""
local me = Game.GetLocalPlayer()
local arch = Players[me]:GetUnits():GetNextExtractingArchaeologist()
if arch == nil then print("NO_ARTIFACT"); print("{SENTINEL}"); return end
local idx = arch:GetArchaeology():GetArtifactIndex()
local obj = Game.GetArtifactByIndex(idx)
if obj == nil then print("ERR:NO_ARTIFACT_OBJECT"); print("{SENTINEL}"); return end
{_LUA_CIV_NAME}
local t = obj.Type or 0
local acting = obj.ActingPlayerID
local target = obj.TargetPlayerID
local hasChoice = (t == 0 or t == 3 or t == 4) and target ~= nil and target >= 0 and target ~= acting
"""


def build_artifact_choice_query() -> str:
    """InGame: the artifact awaiting a player choice, as ChooseArtifact.lua
    reads it, and the one or two civilizations it can be credited to."""
    return f"""
{_lua_artifact()}
local era = ""
pcall(function() era = Locale.Lookup(GameInfo.Eras[obj.ActingPlayerEra].Name) end)
print("ARTIFACT|" .. (arch:GetID() + me * 65536) .. "|" .. Locale.Lookup(arch:GetName()) .. "|" .. arch:GetX() .. "|" .. arch:GetY() .. "|" .. tostring(t) .. "|" .. era)
print("PLAYER|" .. tostring(acting) .. "|" .. civName(acting) .. "|acting")
if hasChoice then print("PLAYER|" .. tostring(target) .. "|" .. civName(target) .. "|target") end
print("{SENTINEL}")
"""


def parse_artifact_choice(lines: list[str]) -> ArtifactChoice | None:
    choice: ArtifactChoice | None = None
    players: list[tuple[int, str, str]] = []
    for line in lines:
        if line.startswith("ARTIFACT|"):
            p = line.split("|")
            if len(p) >= 7:
                try:
                    kind = ARTIFACT_KINDS.get(int(p[5]), "unknown origin")
                except ValueError:
                    kind = "unknown origin"
                choice = ArtifactChoice(
                    int(p[1]), p[2], int(p[3]), int(p[4]), kind, p[6], []
                )
        elif line.startswith("PLAYER|"):
            p = line.split("|")
            if len(p) >= 4:
                players.append((int(p[1]), p[2], p[3].strip()))
    if choice is None:
        return None
    choice.players = players
    return choice


def build_choose_artifact_player(player_id: int) -> str:
    """InGame: credit the artifact to ``player_id`` (``CHOOSE_ARTIFACT_PLAYER``
    with ``PARAM_PLAYER_ONE``, as the popup's buttons do) after re-checking
    that player is one the engine offers."""
    return f"""
{_lua_artifact()}
local pid = {int(player_id)}
if pid ~= acting and not (hasChoice and pid == target) then print("ERR:PLAYER_NOT_OFFERED|" .. pid); print("{SENTINEL}"); return end
local params = {{}}
params[PlayerOperations.PARAM_PLAYER_ONE] = pid
UI.RequestPlayerOperation(me, PlayerOperations.CHOOSE_ARTIFACT_PLAYER, params)
pcall(function() local popup = ContextPtr:LookUpControl("/InGame/ChooseArtifact") if popup then popup:SetHide(true) end end)
print("OK:ARTIFACT_CHOSEN|" .. civName(pid))
print("{SENTINEL}")
"""
