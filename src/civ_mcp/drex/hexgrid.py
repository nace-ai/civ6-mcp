"""Civ 6 offset hex grid arithmetic (odd rows shifted right)."""

from __future__ import annotations


def hex_distance(x1: int, y1: int, x2: int, y2: int) -> int:
    """Tile distance between two offset-coordinate hexes."""

    def cube(x: int, y: int) -> tuple[int, int]:
        q = x - (y - (y & 1)) // 2
        return q, y

    q1, r1 = cube(x1, y1)
    q2, r2 = cube(x2, y2)
    dq, dr = q1 - q2, r1 - r2
    return (abs(dq) + abs(dr) + abs(dq + dr)) // 2
