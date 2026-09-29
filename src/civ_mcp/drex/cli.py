"""``civ-drex``: run, preview, replay and verify the decision-only controller.

Exit codes: 0 success, 2 configuration/API error, 3 run stopped early,
4 replayed answer rejected.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from civ_mcp.drex.candidates import DecisionCategory
from civ_mcp.drex.client import (
    DEFAULT_ENV_FILE,
    DrexClient,
    DrexConfig,
    DrexConfigError,
    DrexError,
    parse_choice_response,
)
from civ_mcp.drex.decision import DecisionError, resolve_choice
from civ_mcp.drex.decision_log import DecisionLog
from civ_mcp.drex.executor import dispatch_call
from civ_mcp.drex.observation import (
    CoreObservation,
    DecisionInputs,
    DecisionMemory,
    DecisionSpec,
)
from civ_mcp.drex.points import build_decision_point
from civ_mcp.drex.runner import DEFAULT_OBJECTIVE, RunConfig, Runner
from civ_mcp.drex.selectors import DrexSelector, RandomSelector, build_request
from civ_mcp.drex.serialize import from_jsonable
from civ_mcp.drex.spectate import LiveSpectator

SUCCESS_STOPS = {"turn_budget_reached", "game_over", "dry_run_complete"}


def _print(data: Any) -> None:
    print(json.dumps(data, indent=2, default=str))


def _err(message: str) -> None:
    print(message, file=sys.stderr)


def _progress_line(speed: dict[str, Any]) -> None:
    _err(
        f"T{speed['turn']}: {speed['decisions']} decisions in {speed['seconds']}s, "
        f"{speed['roundtrips']} round trips, Drex {speed['drex_seconds']}s, "
        f"end turn {speed['end_turn_seconds']}s"
    )


def _load_config(args: argparse.Namespace) -> DrexConfig:
    cfg = DrexConfig.from_env(os.environ, env_file=Path(args.env_file))
    if getattr(args, "allow_non_drex_model", False):
        cfg = DrexConfig(**{**cfg.__dict__, "require_model_prefix": None})
    return cfg


def _config_summary(cfg: DrexConfig) -> dict[str, Any]:
    return {
        "base_url": cfg.base_url,
        "model": cfg.model,
        "timeout_s": cfg.timeout_s,
        "max_retries": cfg.max_retries,
        "max_options": cfg.max_options,
        "require_model_prefix": cfg.require_model_prefix,
    }


async def _verify_api(args: argparse.Namespace) -> int:
    try:
        cfg = _load_config(args)
    except DrexConfigError as e:
        _err(str(e))
        return 2
    client = DrexClient(cfg)
    report: dict[str, Any] = {"config": _config_summary(cfg)}
    try:
        report["models"] = await client.list_models()
        if args.probe:
            ans = await client.choose(
                state={"purpose": "civ-drex contract check"},
                instructions="Which option is listed first?",
                options={"first": None, "second": None},
            )
            report["probe"] = {
                "model": ans.model,
                "choice": ans.choice,
                "probabilities": dict(ans.probabilities),
                "confidence": ans.confidence,
                "usage": ans.usage,
                "request_id": ans.request_id,
                "latency_ms": round(ans.latency_ms or 0.0, 1),
            }
    except DrexError as e:
        report["error"] = f"{type(e).__name__}: {e}"
        _print(report)
        return 2
    finally:
        await client.aclose()
    _print(report)
    return 0


def _run_meta(run_id: str, args: argparse.Namespace) -> dict[str, Any]:
    from civ_mcp.version import GIT_DESCRIBE, GIT_SHA, VERSION

    return {
        "run_id": run_id,
        "civ_mcp_version": VERSION,
        "git": GIT_DESCRIBE or GIT_SHA,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "tuner": f"{args.host}:{args.port}",
    }


async def _run_live(args: argparse.Namespace, *, dry_run: bool) -> int:
    from civ_mcp.connection import GameConnection
    from civ_mcp.game_state import GameState

    run_id = f"drex-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:6]}"
    meta = _run_meta(run_id, args)
    secrets: list[str] = []
    client = None
    selector = None
    runner: Runner | None = None
    max_options = args.max_options
    log_path = Path(args.log_dir) / f"{run_id}.jsonl"
    log = DecisionLog(log_path, run_id=run_id, secrets=secrets)

    def _on_wait(info: dict[str, Any]) -> None:
        if runner is not None:
            runner.selection_waiting(info)
        if info.get("warn"):
            _err(f"waiting for Drex: {info['error']} (retry in {info['sleep_s']:.0f}s)")

    use_drex = (not dry_run and args.selector == "drex") or (dry_run and args.call_drex)
    if use_drex:
        try:
            cfg = _load_config(args)
        except DrexConfigError as e:
            _err(str(e))
            return 2
        secrets.append(cfg.api_key)
        client = DrexClient(cfg)

        async def _refresh_client() -> DrexClient:
            # Non-retryable Drex errors: re-read the env file so a rotated key
            # or changed base URL is picked up without restarting the run.
            nonlocal client, cfg
            new_cfg = _load_config(args)
            if client is not None:
                await client.aclose()
            cfg = new_cfg
            client = DrexClient(cfg)
            log.add_secret(cfg.api_key)
            return client

        selector = DrexSelector(
            client,
            backoff_s=cfg.backoff_s,
            max_backoff_s=cfg.max_backoff_s,
            warn_after=cfg.max_retries,
            on_wait=lambda info: _on_wait(info),
            refresh_client=_refresh_client,
        )
        meta["drex"] = _config_summary(cfg)
        max_options = min(max_options, cfg.max_options)
    elif not dry_run:
        selector = RandomSelector(args.seed)
        meta["baseline"] = {"selector": "random-baseline", "seed": args.seed}
    env_key = os.environ.get("DREX_API_KEY")
    if env_key:
        log.add_secret(env_key)
    for secret in secrets:
        log.add_secret(secret)

    conn = GameConnection(args.host, args.port)
    try:
        await conn.connect()
        gs = GameState(conn)
        config = RunConfig(
            turns=args.turns if not dry_run else 1,
            objective=args.objective,
            max_options=max_options,
            dry_run=dry_run,
            game_dead_after_s=float(os.environ.get("GAME_DEAD_AFTER_S", "120")),
        )

        async def _relaunch() -> str:
            # The game process died or its tuner never answers: relaunch and
            # load the newest per-turn autosave (needs a windowed game).
            from civ_mcp.autosave import get_autosave_for_turn
            from civ_mcp.game_launcher import restart_and_load

            turn = gs._high_water_turn
            latest = get_autosave_for_turn(turn) if turn else None
            return await restart_and_load(latest)

        spectator = None if dry_run or args.no_spectator else LiveSpectator(conn)
        runner = Runner(
            gs,
            selector,
            log,
            config,
            run_meta=meta,
            spectator=spectator,
            on_turn=_progress_line,
            relaunch=None if dry_run else _relaunch,
        )
        result = await runner.run()
        if dry_run and args.save_fixture and runner.preview:
            Path(args.save_fixture).write_text(json.dumps(runner.preview, indent=2))
    except ConnectionError as e:
        _err(f"game connection failed: {e}")
        return 2
    except Exception as e:
        _err(f"run failed: {type(e).__name__}: {e} (log: {log_path})")
        return 3
    finally:
        if client is not None:
            await client.aclose()
        await conn.disconnect()
    _print({"log": str(log_path), **result.__dict__})
    return 0 if result.stop_reason in SUCCESS_STOPS else 3


def _replay(args: argparse.Namespace) -> int:
    data = json.loads(Path(args.fixture).read_text())
    spec = DecisionSpec(
        DecisionCategory(data["spec"]["category"]), data["spec"]["entity"]
    )
    core = from_jsonable(CoreObservation, data["core"])
    inputs = from_jsonable(DecisionInputs, data["inputs"])
    point, excluded = build_decision_point(
        spec,
        core,
        inputs,
        DecisionMemory(),
        objective=data.get("objective", DEFAULT_OBJECTIVE),
        decision_id=data.get("decision_id", "replay#0001"),
        max_options=args.max_options,
    )
    if point is None:
        _print({"point": None, "exclusions": [e.__dict__ for e in excluded]})
        return 4
    out: dict[str, Any] = {
        "point": point.to_record(),
        "request": build_request(point),
        "decision": None,
        "dispatch": None,
    }
    try:
        if args.answer:
            answer = parse_choice_response(Path(args.answer).read_bytes())
            decision = resolve_choice(point, answer, selector="replay")
        elif args.selector == "random-baseline":
            decision = asyncio.run(RandomSelector(args.seed).choose(point)).decision
        else:
            _print(out)
            return 0
    except (DecisionError, DrexError) as e:
        out["rejected"] = f"{type(e).__name__}: {e}"
        _print(out)
        return 4
    out["decision"] = decision.to_record()
    out["dispatch"] = dispatch_call(point.get(decision.candidate_id)).to_record()
    _print(out)
    return 0


_APP_OPTIONS = Path(
    "~/Library/Application Support/Sid Meier's Civilization VI/Firaxis Games/"
    "Sid Meier's Civilization VI/AppOptions.txt"
).expanduser()


def _fullscreen_warning(path: Path = _APP_OPTIONS) -> str | None:
    """Exclusive fullscreen (FullScreen 1) blocks the OCR-driven relaunch after
    a crash; borderless (2) or windowed (0) is required for it."""
    try:
        for line in path.read_text().splitlines():
            if line.strip().startswith("FullScreen "):
                if line.split()[1] == "1":
                    return (
                        "FullScreen 1 in AppOptions.txt: automatic game relaunch "
                        "after a crash needs windowed mode (FullScreen 2)"
                    )
                return None
    except OSError:
        return None
    return None


async def _probe(args: argparse.Namespace) -> int:
    """Read-only live checks of the queries the controller relies on."""
    from civ_mcp.connection import GameConnection
    from civ_mcp.game_state import GameState

    warning = _fullscreen_warning()
    if warning:
        _err(f"warning: {warning}")
    conn = GameConnection(args.host, args.port)
    try:
        await conn.connect()
        gs = GameState(conn)
        civ, seed = await gs.get_game_identity()
        overview = await gs.get_game_overview()
        progress = await gs.get_progress_types()
        units = await gs.get_units()
        spaces = []
        for u in units:
            if u.moves_remaining <= 0:
                continue
            s = await gs.get_unit_action_space(u.unit_index)
            spaces.append(
                {
                    "unit_id": u.unit_id,
                    "unit_index": u.unit_index,
                    "type": u.unit_type,
                    "at": [u.x, u.y],
                    "action_space": None
                    if s is None
                    else {
                        "identity_matches": s.unit_id == u.unit_id,
                        "reachable": len(s.reachable),
                        "targets": [t.__dict__ for t in s.targets],
                        "can_found": s.can_found,
                        "can_fortify": s.can_fortify,
                        "can_heal": s.can_heal,
                    },
                }
            )
        report = {
            "game": {"civ": civ, "seed": seed, "turn": overview.turn},
            "progress": progress.__dict__,
            "blockers": await gs.get_end_turn_blockers(),
            "wonder_types": len(await gs.get_wonder_types()),
            "units_with_moves": spaces,
        }
    except ConnectionError as e:
        _err(f"game connection failed: {e}")
        return 2
    except Exception as e:
        _err(f"probe failed: {type(e).__name__}: {e}")
        return 3
    finally:
        await conn.disconnect()
    _print(report)
    return 0


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="civ-drex",
        description="Decision-only Civilization VI controller: Drex chooses among code-enumerated candidates.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    def env(sp):
        sp.add_argument(
            "--env-file",
            default=str(DEFAULT_ENV_FILE),
            help="KEY=VALUE file with DREX_* settings",
        )
        sp.add_argument(
            "--allow-non-drex-model",
            action="store_true",
            help="accept responses whose model is not drex-* (wire-compatibility tests only)",
        )

    def game(sp):
        sp.add_argument("--host", default="127.0.0.1")
        sp.add_argument("--port", type=int, default=4318)

    v = sub.add_parser("verify-api", help="check Drex credentials and contract")
    env(v)
    v.add_argument(
        "--probe", action="store_true", help="send one 2-option Choice request"
    )

    for name, help_text in (
        ("play", "play turns in the loaded save"),
        ("dry-run", "show the next decision without mutating the game"),
    ):
        sp = sub.add_parser(name, help=help_text)
        env(sp)
        game(sp)
        sp.add_argument("--objective", default=DEFAULT_OBJECTIVE)
        sp.add_argument("--log-dir", default="logs/drex")
        sp.add_argument("--max-options", type=int, default=255)
        if name == "play":
            sp.add_argument("--turns", type=int, default=20)
            sp.add_argument(
                "--selector", choices=["drex", "random-baseline"], default="drex"
            )
            sp.add_argument("--seed", type=int, default=0, help="random-baseline seed")
            sp.add_argument(
                "--no-spectator",
                action="store_true",
                help="do not auto-dismiss popups or follow the action with the camera",
            )
        else:
            sp.add_argument(
                "--call-drex",
                action="store_true",
                help="ask Drex to choose (no dispatch)",
            )
            sp.add_argument(
                "--save-fixture", help="write the observation for offline replay"
            )

    r = sub.add_parser("replay", help="offline: rebuild a decision from a fixture")
    r.add_argument("--fixture", required=True)
    r.add_argument("--answer", help="recorded /v1/systemone response JSON")
    r.add_argument("--selector", choices=["random-baseline"])
    r.add_argument("--seed", type=int, default=0)
    r.add_argument("--max-options", type=int, default=255)

    pr = sub.add_parser("probe", help="read-only live check of controller queries")
    game(pr)
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "verify-api":
        return asyncio.run(_verify_api(args))
    if args.command == "play":
        return asyncio.run(_run_live(args, dry_run=False))
    if args.command == "dry-run":
        return asyncio.run(_run_live(args, dry_run=True))
    if args.command == "replay":
        return _replay(args)
    if args.command == "probe":
        return asyncio.run(_probe(args))
    return 2


if __name__ == "__main__":
    sys.exit(main())
