"""Export every decision sent to Drex from run logs into one JSONL file.

Each output row holds what Drex received (``request``: state, options,
instructions), the candidates the controller offered, Drex's answer
(``drex_response``: chosen id, scores, confidence, model, latency) and what
the game did with it (``dispatch`` / ``outcome``).

Usage:
    uv run python scripts/export_drex_requests.py                 # all logs
    uv run python scripts/export_drex_requests.py --since 20260930T094247Z
    uv run python scripts/export_drex_requests.py -o out.jsonl LOG1.jsonl LOG2.jsonl
"""

from __future__ import annotations

import argparse
import glob
import json
import os
from collections import Counter

LOG_DIR = os.path.join(os.path.dirname(__file__), "..", "logs", "drex")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "logs", nargs="*", help="run logs (default: every logs/drex/drex-*.jsonl)"
    )
    ap.add_argument(
        "--since", help="only logs whose run stamp is >= this (e.g. 20260930T094247Z)"
    )
    ap.add_argument(
        "-o", "--out", default=os.path.join(LOG_DIR, "exports", "drex_requests.jsonl")
    )
    args = ap.parse_args()

    files = args.logs or sorted(glob.glob(os.path.join(LOG_DIR, "drex-*.jsonl")))
    if args.since:
        files = [f for f in files if os.path.basename(f)[5:21] >= args.since]

    rows = []
    for f in files:
        run = os.path.basename(f)
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if r.get("type") != "decision":
                    continue
                d = r.get("decision") or {}
                if d.get("selector") != "drex":
                    continue  # forced single-candidate picks never reached Drex
                rows.append(
                    {
                        "ts": r.get("ts"),
                        "run": run,
                        "turn": r.get("turn"),
                        "decision_id": r.get("decision_id"),
                        "category": r.get("category"),
                        "entity": r.get("entity"),
                        "question": r.get("question"),
                        "request": r.get("request"),
                        "candidates": r.get("candidates"),
                        "exclusions": r.get("exclusions"),
                        "drex_response": d,
                        "attempts": r.get("attempts"),
                        "dispatch": r.get("dispatch"),
                        "outcome": r.get("outcome"),
                    }
                )
    rows.sort(key=lambda x: x["ts"] or 0)

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")

    turns = [r["turn"] for r in rows if r["turn"] is not None]
    print(
        f"{len(rows)} requests -> {args.out} ({os.path.getsize(args.out) // 1024} KB)"
    )
    if turns:
        print(f"turns {min(turns)}-{max(turns)} across {len(files)} logs")
    print("by category:", dict(Counter(r["category"] for r in rows).most_common()))


if __name__ == "__main__":
    main()
