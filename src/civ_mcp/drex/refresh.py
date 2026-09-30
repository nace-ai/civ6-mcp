"""Which parts of the observation an action can change.

After a dispatched action the runner refreshes only these parts (plus the
end-turn blockers and popup state, which any action may change) instead of
re-reading the whole game. Once per turn, before end turn, a full observation
runs as a safety net.
"""

from __future__ import annotations

from civ_mcp.drex.candidates import ActionKind
from civ_mcp.game_state import CORE_PARTS

FULL = CORE_PARTS
_ALWAYS = frozenset({"blockers", "popup"})
_TABLE: dict[ActionKind, frozenset[str]] = {
    ActionKind.MOVE_UNIT: frozenset({"units"}),
    ActionKind.FORTIFY_UNIT: frozenset({"units"}),
    ActionKind.HEAL_UNIT: frozenset({"units"}),
    ActionKind.SKIP_UNIT: frozenset({"units"}),
    ActionKind.IMPROVE_TILE: frozenset({"units"}),
    ActionKind.FOUND_CITY: frozenset({"units", "cities", "overview"}),
    ActionKind.ATTACK: frozenset({"units", "cities"}),
    ActionKind.SET_PRODUCTION: frozenset({"cities"}),
    ActionKind.SET_RESEARCH: frozenset({"progress"}),
    ActionKind.SET_CIVIC: frozenset({"progress"}),
    ActionKind.SET_POLICY: frozenset({"overview"}),
    ActionKind.CHANGE_GOVERNMENT: frozenset({"overview"}),
    ActionKind.KEEP_GOVERNMENT: frozenset(),
    ActionKind.SEND_ENVOY: frozenset({"overview"}),
    ActionKind.CHOOSE_PANTHEON: frozenset({"overview"}),
    ActionKind.DIPLOMACY_RESPOND: frozenset({"sessions", "deals", "overview"}),
    ActionKind.DEAL_RESPOND: frozenset({"sessions", "deals", "overview"}),
    ActionKind.PROMOTE_UNIT: frozenset({"units"}),
    ActionKind.APPOINT_GOVERNOR: frozenset(),
    ActionKind.ASSIGN_GOVERNOR: frozenset({"cities"}),
    ActionKind.PROMOTE_GOVERNOR: frozenset(),
    ActionKind.CHOOSE_DEDICATION: frozenset({"overview"}),
    ActionKind.RECRUIT_GREAT_PERSON: frozenset({"units", "overview"}),
    ActionKind.PATRONIZE_GREAT_PERSON: frozenset({"units", "overview"}),
    ActionKind.WAIT_GREAT_PERSON: frozenset(),
    ActionKind.CHOOSE_RELIGION: frozenset(),
    ActionKind.CHOOSE_FOLLOWER_BELIEF: frozenset(),
    ActionKind.FOUND_RELIGION: frozenset({"overview", "units", "cities"}),
    ActionKind.ADD_BELIEF: frozenset({"overview"}),
    ActionKind.CITY_ATTACK: frozenset({"units"}),
    ActionKind.HOLD_FIRE: frozenset(),
    ActionKind.MAKE_TRADE_ROUTE: frozenset({"units", "overview"}),
    ActionKind.RESOLVE_CAPTURED_CITY: frozenset({"cities", "units", "overview"}),
    ActionKind.CHOOSE_ESCAPE_ROUTE: frozenset({"units"}),
    ActionKind.CHOOSE_ARTIFACT_PLAYER: frozenset({"units", "overview"}),
    ActionKind.UPGRADE_UNIT: frozenset({"units", "overview"}),
}


def refresh_parts(kind: ActionKind) -> frozenset[str]:
    """Parts to re-read after ``kind`` was dispatched (always includes
    blockers and popup state). Raises KeyError for a kind without a rule, so
    a new action kind cannot ship without deciding what it changes."""
    return _TABLE[kind] | _ALWAYS
