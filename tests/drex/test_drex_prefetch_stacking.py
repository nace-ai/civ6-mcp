"""A prefetched action space must not offer tiles our own units just took.

The next unit's inputs are read while the current unit is still deciding, so
a tile the current unit then moves onto still looks free. 20 of 539 unit
decisions on 2026-09-30 were STACKING_CONFLICT rejections from exactly this.
"""

from __future__ import annotations

import dataclasses

import drex_fixtures as fx

from civ_mcp import lua as lq
from civ_mcp.drex.observation import DecisionInputs
from civ_mcp.drex.runner import _mark_tiles_taken_since


class _Core:
    def __init__(self, units):
        self.units = units

    def unit(self, unit_id):
        return next((u for u in self.units if u.unit_id == unit_id), None)


def _unit(uid, x, y, combat=25):
    return dataclasses.replace(
        fx.warrior(), unit_id=uid, unit_index=uid, x=x, y=y, combat_strength=combat
    )


def _tile(x, y, conflict=False):
    return lq.ReachableTile(
        x=x,
        y=y,
        terrain="TERRAIN_GRASS",
        feature=None,
        resource=None,
        is_hills=False,
        is_river=False,
        owner_id=-1,
        visibility="visible",
        own_stack_conflict=conflict,
        visible_foreign_unit=False,
        distance=1,
    )


def _inputs(uid, x, y, tiles):
    space = lq.UnitActionSpace(
        unit_id=uid,
        unit_index=uid,
        unit_type="UNIT_ARCHER",
        x=x,
        y=y,
        moves_remaining=2,
        is_civilian=False,
        can_found=False,
        can_fortify=True,
        can_heal=False,
        moved_into_zoc=False,
        fortify_turns=0,
        hp=100,
        max_hp=100,
        reachable=tiles,
    )
    return DecisionInputs(unit=_unit(uid, x, y), action_space=space)


def _conflicts(inputs):
    return sorted(
        (t.x, t.y) for t in inputs.action_space.reachable if t.own_stack_conflict
    )


def test_tile_another_combat_unit_moved_onto_is_marked_taken():
    before = _Core([_unit(1, 59, 24), _unit(2, 59, 23)])
    now = _Core([_unit(1, 59, 22), _unit(2, 59, 23)])  # archer 1 moved onto (59,22)
    inputs = _inputs(2, 59, 23, [_tile(59, 22), _tile(59, 24), _tile(60, 23)])

    patched = _mark_tiles_taken_since(inputs, before, now, unit_id=2)

    assert _conflicts(patched) == [(59, 22)]
    assert _conflicts(inputs) == []  # the original is left alone


def test_a_civilian_moving_in_does_not_block_a_combat_unit():
    before = _Core([_unit(1, 59, 24, combat=0), _unit(2, 59, 23)])
    now = _Core(
        [_unit(1, 59, 22, combat=0), _unit(2, 59, 23)]
    )  # builder moved onto (59,22)
    inputs = _inputs(2, 59, 23, [_tile(59, 22)])
    assert _conflicts(_mark_tiles_taken_since(inputs, before, now, unit_id=2)) == []


def test_units_that_did_not_move_change_nothing():
    before = _Core([_unit(1, 59, 22), _unit(2, 59, 23)])
    now = _Core([_unit(1, 59, 22), _unit(2, 59, 23)])
    inputs = _inputs(
        2, 59, 23, [_tile(59, 22)]
    )  # enumeration already handles the standing unit
    assert _mark_tiles_taken_since(inputs, before, now, unit_id=2) is inputs
