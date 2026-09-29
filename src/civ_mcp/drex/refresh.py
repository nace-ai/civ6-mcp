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
}


def refresh_parts(kind: ActionKind) -> frozenset[str]:
    """Parts to re-read after ``kind`` was dispatched (always includes
    blockers and popup state). Raises KeyError for a kind without a rule, so
    a new action kind cannot ship without deciding what it changes."""
    return _TABLE[kind] | _ALWAYS
