import argparse
import asyncio
import json
import re
import sqlite3
import sys
import warnings
from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from getpass import GetPassWarning, getpass
from pathlib import Path

from .collector import Collection, XCollector, add_session
from .config import Target, load_config
from .domain import Post, parse_time
from .locking import writer_lock
from .runner import Runner
from .store import Store


def output(event: dict) -> None:
    print(json.dumps(event, ensure_ascii=True), flush=True)


def diagnostic(event: dict) -> None:
    print(json.dumps({"at": datetime.now(UTC).isoformat(), **event}), file=sys.stderr, flush=True)


class ReplayCollector:
    def __init__(self, posts: list[Post]):
        self.posts = posts

    async def fetch(self, target: Target, limit: int) -> Collection:
        return Collection(self.posts, "ok" if self.posts else "empty")


def read_replay(path: Path) -> list[Post]:
    if path.stat().st_size > 10_000_000:
        raise ValueError("replay input exceeds 10 MB")
    posts = []
    with path.open(encoding="utf-8-sig") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                data = json.loads(line)
                if not isinstance(data, dict):
                    raise ValueError("record must be an object")
                posts.append(Post.from_dict(data))
            except (TypeError, ValueError) as error:
                raise ValueError(f"invalid replay record on line {number}") from error
    return posts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Independent read-only X monitor. No X developer API key required.")
    parser.add_argument("--config", type=Path, default=Path("config.example.toml"))
    parser.add_argument("--state-dir", type=Path, help="override local state path, relative to current directory")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate", help="validate configuration without network or state changes")
    commands.add_parser("status", help="show local poll health and pending alerts")
    commands.add_parser("export", help="export public evidence as JSONL, never session cookies")
    stats = commands.add_parser("stats", help="collection reliability and measured alert delay")
    stats.add_argument("--hours", type=int, default=24, choices=range(1, 721), metavar="1-720")
    auth = commands.add_parser("auth", help="save your X session through hidden local prompts")
    auth.add_argument("--name", default="monitor")
    run = commands.add_parser("run", help="poll configured targets and print JSON alerts")
    run.add_argument("--once", action="store_true")
    replay = commands.add_parser("replay", help="test the complete local pipeline without accessing X")
    replay.add_argument("path", type=Path)
    replay.add_argument("--at", required=True, help="receipt time used for fixture alerts, ISO with timezone")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        if args.state_dir:
            config = replace(config, state_dir=args.state_dir.resolve())
        if args.command == "validate":
            output({"valid": True, "targets": len(config.targets), "state_dir": str(config.state_dir)})
            return 0
        database = config.state_dir / "monitor.sqlite"
        if args.command in {"status", "export", "stats"} and not database.exists():
            output({"event": "not_started", "targets": [], "posts": 0, "pending_alerts": 0})
            return 0
        if args.command == "auth":
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", args.name):
                raise ValueError("invalid local account name")
            if not sys.stdin.isatty():
                raise ValueError("auth requires an interactive terminal with hidden input")
            with warnings.catch_warnings():
                warnings.simplefilter("error", GetPassWarning)
                try:
                    auth_token = getpass("X auth_token (hidden): ").strip()
                    ct0 = getpass("X ct0 (hidden): ").strip()
                except GetPassWarning as error:
                    raise ValueError("hidden input is unavailable in this terminal") from error
            if not all(re.fullmatch(r"[A-Za-z0-9_-]{16,512}", value) for value in (auth_token, ct0)):
                raise ValueError("cookies must be 16-512 letters, digits, underscores or hyphens")
            with writer_lock(config.state_dir):
                asyncio.run(add_session(config.state_dir / "accounts.sqlite", args.name, auth_token, ct0))
            output({"event": "session_saved", "live_access_verified": False})
            return 0
        if args.command in {"status", "export", "stats"}:
            with closing(Store(database, read_only=True)) as store:
                if args.command == "export":
                    store.export(output)
                elif args.command == "stats":
                    output(store.statistics(datetime.now(UTC) - timedelta(hours=args.hours)))
                else:
                    status = store.status()
                    now = datetime.now(UTC)
                    for target in status["targets"]:
                        target["stale"] = (
                            now - parse_time(target["completed_at"])
                        ).total_seconds() > config.poll_seconds * 3
                    seen = {item["target"]: item for item in status["targets"]}
                    status["healthy"] = bool(config.targets) and all(
                        target.key in seen and seen[target.key]["status"] == "ok" and not seen[target.key]["stale"]
                        for target in config.targets
                    )
                    status["unpolled_targets"] = [target.name for target in config.targets if target.key not in seen]
                    output(status)
            return 0
        if args.command == "replay":
            # Validate every fixture before any database mutation.
            posts, at = read_replay(args.path), parse_time(args.at)
            target = Target("replay", "search", "fixture")
            config = replace(config, targets=(target,), alert_on_first_poll=True)
            with writer_lock(config.state_dir), closing(Store(database)) as store:
                runner = Runner(config, store, ReplayCollector(posts), output, diagnostic, clock=lambda: at)
                asyncio.run(runner.run(once=True))
            return 0
        accounts = config.state_dir / "accounts.sqlite"
        if not accounts.exists():
            raise ValueError("no X session configured; run the auth command in a local terminal first")
        with (
            writer_lock(config.state_dir),
            closing(Store(database)) as store,
            closing(XCollector(accounts)) as collector,
        ):
            healthy = asyncio.run(Runner(config, store, collector, output, diagnostic).run(once=args.once))
        return 0 if healthy else 2
    except KeyboardInterrupt:
        diagnostic({"event": "stopped"})
        return 130
    except (OSError, ValueError, sqlite3.Error) as error:
        # Only our validation messages are safe to display; OS/DB errors can include secret paths.
        diagnostic(
            {
                "event": "command_failed",
                "error_code": type(error).__name__,
                "detail": str(error) if type(error) is ValueError else "local I/O failure",
            }
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
