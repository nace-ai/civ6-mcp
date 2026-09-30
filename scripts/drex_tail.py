"""Live terminal view of the decisions Drex makes.

Follows the newest run log in logs/drex/ (or a given path) and prints one
line per decision as it lands, plus turn banners, errors and stops. Sized
for a narrow side terminal (about 60 columns).

Usage:
    uv run python scripts/drex_tail.py            # follow the newest run log
    uv run python scripts/drex_tail.py PATH.jsonl # follow a specific log
    uv run python scripts/drex_tail.py --all      # replay the whole log first
"""

from __future__ import annotations

import glob
import json
import os
import shutil
import sys
import time
from datetime import datetime

LOG_DIR = os.path.join(os.path.dirname(__file__), "..", "logs", "drex")

RESET = "\033[0m"
DIM = "\033[2m"
BOLD = "\033[1m"
WHITE = "\033[97m"
GREY = "\033[90m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
MAGENTA = "\033[35m"
BLUE = "\033[34m"
ORANGE = "\033[38;5;208m"

# icon, colour, short name shown in the category column
CATEGORY = {
    "unit": ("⚔", CYAN, "unit"),
    "production": ("⚒", BLUE, "build"),
    "research": ("⚗", BLUE, "tech"),
    "civic": ("✎", BLUE, "civic"),
    "policy": ("☰", BLUE, "policy"),
    "government": ("♔", BLUE, "gov"),
    "purchase": ("$", YELLOW, "buy"),
    "diplomacy": ("☎", MAGENTA, "talk"),
    "foreign_policy": ("✉", MAGENTA, "diplo"),
    "deal": ("⇄", MAGENTA, "deal"),
    "envoy": ("♟", MAGENTA, "envoy"),
    "city_attack": ("➶", ORANGE, "city"),
    "promotion": ("★", CYAN, "promo"),
    "great_person": ("☆", YELLOW, "great"),
    "pantheon": ("☼", YELLOW, "faith"),
    "religion": ("☼", YELLOW, "faith"),
    "belief": ("☼", YELLOW, "faith"),
    "governor": ("♕", BLUE, "gov"),
}

REJECT_REASONS = {
    "game_error:purchase_refused": "refused by game",
    "game_error:stacking_conflict": "tile taken by own unit",
    "already_dispatched_this_turn": "already acted",
    "failed earlier this turn": "failed before",
}


def newest_log() -> str | None:
    files = glob.glob(os.path.join(LOG_DIR, "drex-*.jsonl"))
    return max(files, key=os.path.getmtime) if files else None


def width() -> int:
    return max(48, shutil.get_terminal_size((72, 40)).columns)


def stamp(ts: float | None) -> str:
    return (
        datetime.fromtimestamp(ts).astimezone().strftime("%H:%M:%S")
        if ts
        else "        "
    )


def pretty_type(raw: str | None) -> str:
    if not raw:
        return ""
    for prefix in ("UNIT_", "BUILDING_", "DISTRICT_", "TECH_", "CIVIC_"):
        raw = raw.removeprefix(prefix)
    return raw.replace("_", " ").title()


def subject_name(r: dict) -> str:
    """A human name for what the decision is about: Warrior, Rome, Empire."""
    for c in r.get("candidates", []):
        p = c.get("params") or {}
        unit = p.get("unit")
        if isinstance(unit, dict) and unit.get("unit_type"):
            return pretty_type(unit["unit_type"])
        if p.get("city_name"):
            return str(p["city_name"])
        facts = c.get("facts") or {}
        if facts.get("city"):
            return str(facts["city"])
    subj = ((r.get("request") or {}).get("state") or {}).get("subject") or {}
    if subj.get("civilization"):
        return str(subj["civilization"])
    entity = r.get("entity", "")
    if entity == "empire":
        return "Empire"
    if entity.startswith("city:"):
        return "City"
    return entity.split(":")[0].title() if entity else ""


def clip(s: str, n: int) -> str:
    return s if len(s) <= n else s[: max(1, n - 1)] + "…"


def fmt_decision(r: dict) -> str:
    d = r.get("decision") or {}
    chosen = d.get("candidate_id")
    labels = {c["id"]: c.get("label", c["id"]) for c in r.get("candidates", [])}
    label = labels.get(chosen, chosen or "no choice")
    cat = r.get("category", "?")
    icon, color, short = CATEGORY.get(cat, ("•", WHITE, cat[:6]))
    forced = d.get("selector") != "drex"

    out = r.get("outcome") or {}
    status = out.get("status")
    if status == "confirmed":
        mark = f"{GREEN}✓{RESET}"
        tail = ""
    elif status == "pending":
        mark = f"{YELLOW}…{RESET}"
        tail = ""
    elif status:
        reason = out.get("reason") or ""
        mark = f"{RED}✗{RESET}"
        tail = f" {RED}{REJECT_REASONS.get(reason, reason.replace('_', ' '))}{RESET}"
    else:
        mark, tail = f"{GREY}·{RESET}", ""

    conf = d.get("confidence")
    if forced:
        conf_s = f"{GREY}auto{RESET}"
    elif isinstance(conf, (int, float)):
        conf_s = f"{GREY}{conf:>3.0%}{RESET}"
    else:
        conf_s = "    "

    who = clip(subject_name(r), 11)
    # time(8) sp icon(1) sp cat(5) sp who(11) sp label(fill) sp conf(4) sp mark(1)
    label_w = max(10, width() - (8 + 1 + 1 + 1 + 5 + 1 + 11 + 1 + 1 + 4 + 1 + 1))
    label_s = clip(label, label_w)
    lab = f"{DIM}{label_s}{RESET}" if forced else f"{BOLD}{label_s}{RESET}"
    line = (
        f"{GREY}{stamp(r.get('ts'))}{RESET} {color}{icon} {short:<5}{RESET} "
        f"{WHITE}{who:<11}{RESET} {lab}{' ' * (label_w - len(label_s))} {conf_s} {mark}"
    )
    if tail:
        line += "\n" + " " * 28 + tail.strip()
    return line


def banner(text: str, color: str) -> str:
    w = width()
    line = f"━━ {text} "
    return f"\n{color}{BOLD}{line}{'━' * max(0, w - len(line))}{RESET}"


def fmt(r: dict) -> str | None:
    t = r.get("type")
    if t == "decision":
        return fmt_decision(r)
    if t == "turn":
        status = r.get("status")
        if status == "advanced":
            return banner(f"Turn {r.get('turn_after')}", GREEN)
        report = (r.get("report") or "").splitlines()
        first = clip(report[0], width() - 12) if report else ""
        extra = ""
        if r.get("diplomacy_pending"):
            extra = (
                f"\n{MAGENTA}   leader waiting: players {r['diplomacy_pending']}{RESET}"
            )
        return f"{YELLOW}{stamp(r.get('ts'))} turn {r.get('turn_before')} not ended: {status}{RESET}\n{GREY}   {first}{RESET}{extra}"
    if t == "speed":
        return (
            f"{GREY}   turn {r.get('turn')} done: {r.get('decisions')} decisions in "
            f"{r.get('seconds')}s (Drex {r.get('drex_seconds')}s){RESET}"
        )
    if t == "header":
        g = r.get("game", {})
        return (
            banner(
                f"Drex playing {str(g.get('civ', '')).title()} from turn {r.get('start_turn')}",
                WHITE,
            )
            + f"\n{GREY}   run {r.get('run_id')}{RESET}"
        )
    if t == "no_candidates":
        return f"{stamp(r.get('ts'))} {YELLOW}{r.get('category')}: nothing left to choose{RESET}"
    if t in (
        "end_turn_blocked_repeat",
        "supported_blocker_stuck",
        "unsupported_blocker",
    ):
        return (
            f"{stamp(r.get('ts'))} {YELLOW}turn blocked by {r.get('blockers')}{RESET}"
        )
    if t in ("game_io_error", "runner_error", "inputs_error"):
        return f"{stamp(r.get('ts'))} {RED}{t.replace('_', ' ')}: {clip(str(r.get('error')), width() - 30)}{RESET}"
    if t in ("game_relaunch", "waiting_for_turn", "housekeeping", "checkpoint_failed"):
        extra = {k: v for k, v in r.items() if k not in ("type", "run_id", "seq", "ts")}
        return f"{stamp(r.get('ts'))} {YELLOW}{t.replace('_', ' ')}{RESET} {GREY}{clip(json.dumps(extra), width() - 30)}{RESET}"
    if t == "stop":
        return banner(f"STOPPED: {r.get('reason')}", RED)
    return None


def follow(path: str, replay: bool) -> None:
    print(f"{GREY}following {os.path.basename(path)}{RESET}")
    with open(path, encoding="utf-8") as fh:
        if not replay:
            fh.seek(0, os.SEEK_END)
        while True:
            line = fh.readline()
            if not line:
                newer = newest_log()
                if newer and os.path.abspath(newer) != os.path.abspath(path):
                    print(banner("new run started", WHITE))
                    return follow(newer, replay=True)
                time.sleep(0.3)
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            out = fmt(r)
            if out:
                print(out, flush=True)


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    replay = "--all" in sys.argv
    path = args[0] if args else newest_log()
    if not path:
        print("no run log found in logs/drex/")
        sys.exit(1)
    try:
        follow(path, replay)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
