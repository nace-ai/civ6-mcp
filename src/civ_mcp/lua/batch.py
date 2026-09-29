"""Run several query scripts in one tuner round trip.

Every builder in :mod:`civ_mcp.lua` ends with ``print("---END---")`` and may
bail early with ``print(...); print("---END---"); return``. A batch strips
those sentinel prints, wraps each script in ``pcall(function() ... end)`` so a
``return`` or an error only ends its own section, prints a section marker
before each, and one sentinel at the very end.
"""

from __future__ import annotations

from civ_mcp.lua._helpers import SENTINEL

SECTION_MARK = "@@SECTION|"
ERROR_MARK = "@@ERR|"
_SENTINEL_PRINT = f'print("{SENTINEL}")'
_SENTINEL_PRINT_SQ = f"print('{SENTINEL}')"


def build_batch(sections: list[tuple[str, str]]) -> str:
    parts: list[str] = []
    for name, lua in sections:
        body = lua.replace(_SENTINEL_PRINT, "").replace(_SENTINEL_PRINT_SQ, "")
        parts.append(
            f'print("{SECTION_MARK}{name}")\n'
            f"do local __ok, __err = pcall(function()\n{body}\nend)\n"
            f'if not __ok then print("{ERROR_MARK}{name}|" .. tostring(__err)) end end\n'
        )
    parts.append(_SENTINEL_PRINT)
    return "\n".join(parts)


def split_batch(lines: list[str]) -> tuple[dict[str, list[str]], dict[str, str]]:
    """Group output lines by section; errors are returned separately."""
    sections: dict[str, list[str]] = {}
    errors: dict[str, str] = {}
    current: str | None = None
    for raw in lines:
        line = raw.strip()
        if line.startswith(SECTION_MARK):
            current = line[len(SECTION_MARK) :]
            sections.setdefault(current, [])
            continue
        if line.startswith(ERROR_MARK):
            name, _, msg = line[len(ERROR_MARK) :].partition("|")
            errors[name] = msg
            sections.setdefault(name, [])
            continue
        if current is not None:
            sections[current].append(raw)
    return sections, errors
