"""One Lua round trip for many queries: build and split."""

from civ_mcp.lua._helpers import SENTINEL
from civ_mcp.lua.batch import ERROR_MARK, SECTION_MARK, build_batch, split_batch


def test_build_strips_inner_sentinels_and_wraps_each_section():
    a = 'print("A1")\nprint("---END---")\n'
    b = (
        'if x then print("B0"); print("---END---"); return end\n'
        'print("B1")\nprint("---END---")'
    )
    lua = build_batch([("a", a), ("b", b)])
    assert lua.count(SENTINEL) == 1 and lua.rstrip().endswith(f'print("{SENTINEL}")')
    assert lua.count(f'print("{SECTION_MARK}a")') == 1
    assert lua.count(f'print("{SECTION_MARK}b")') == 1
    assert lua.count("pcall(function()") == 2
    body_b = lua.split(f"{SECTION_MARK}b")[1].rsplit(SENTINEL, 1)[0]
    assert '"---END---"' not in body_b


def test_split_groups_lines_by_section():
    lines = [f"{SECTION_MARK}a", "A1", "A2", f"{SECTION_MARK}b", "B1"]
    sections, errors = split_batch(lines)
    assert sections == {"a": ["A1", "A2"], "b": ["B1"]} and errors == {}


def test_split_batch_reports_section_error_and_keeps_others():
    lines = [
        f"{SECTION_MARK}a",
        f"{ERROR_MARK}a|attempt to index nil",
        f"{SECTION_MARK}b",
        "B1",
    ]
    sections, errors = split_batch(lines)
    assert sections == {"a": [], "b": ["B1"]}
    assert errors == {"a": "attempt to index nil"}


def test_split_ignores_noise_before_first_section():
    lines = ["BulkHide debug", f"{SECTION_MARK}a", "A1"]
    sections, _ = split_batch(lines)
    assert sections == {"a": ["A1"]}
