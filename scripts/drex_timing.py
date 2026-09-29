#!/usr/bin/env python3
"""Summarise timing from a civ-drex decision log.

uv run python scripts/drex_timing.py logs/drex/<run>.jsonl
"""

import json
import statistics
import sys


def main(path: str) -> None:
    decisions: list[dict] = []
    speed: list[dict] = []
    with open(path) as fh:
        for line in fh:
            r = json.loads(line)
            if r["type"] == "decision":
                decisions.append(
                    {**r["timing_ms"], "kind": (r.get("dispatch") or {}).get("method")}
                )
            elif r["type"] == "speed":
                speed.append(r)
    if decisions:
        for k in ("api", "execute", "observe", "roundtrips"):
            vals = sorted(d.get(k, 0) or 0 for d in decisions)
            p90 = vals[int(0.9 * (len(vals) - 1))]
            print(
                f"{k:>10}: median {statistics.median(vals):8.1f}  "
                f"p90 {p90:8.1f}  n={len(vals)}"
            )
        over = [d for d in decisions if (d.get("roundtrips") or 0) > 3]
        if over:
            by_kind: dict[str, int] = {}
            for d in over:
                by_kind[str(d["kind"])] = by_kind.get(str(d["kind"]), 0) + 1
            print(f"over 3 round trips: {by_kind}")
    for s in speed:
        print(
            f"T{s['turn']:>4}: {s['decisions']:3d} dec {s['seconds']:6.1f}s "
            f"{s['roundtrips']:4d} rt drex {s['drex_seconds']:5.1f}s "
            f"end_turn {s['end_turn_seconds']:5.1f}s"
        )


if __name__ == "__main__":
    main(sys.argv[1])
