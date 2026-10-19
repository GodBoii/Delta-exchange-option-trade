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
from .health import collection_status, research_status
from .locking import writer_lock
from .reporting import coverage, review
from .research import Research
from .research import run as run_research
from .runner import Runner
from .store import Store


def output(event: dict) -> None:
    print(json.dumps(event, ensure_ascii=True), flush=True)


def diagnostic(event: dict) -> None:
    print(json.dumps({"at": datetime.now(UTC).isoformat(), **event}), file=sys.stderr, flush=True)


class ReplayCollector:
    def __init__(self, posts: list[Post]):
        self.posts = posts

    async def fetch(self, target: Target, limit: int, **options) -> Collection:
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
    status_command = commands.add_parser("status", help="show local poll health and pending alerts")
    status_command.add_argument("--check", action="store_true", help="exit 2 if collection is unhealthy")
    commands.add_parser("export", help="export public evidence as JSONL, never session cookies")
    stats = commands.add_parser("stats", help="collection reliability and measured alert delay")
    stats.add_argument("--hours", type=int, default=24, choices=range(1, 721), metavar="1-720")
    coverage_command = commands.add_parser(
        "coverage", help="local request reliability and unverified timeline freshness"
    )
    coverage_command.add_argument("--hours", type=int, default=24, choices=range(1, 721), metavar="1-720")
    review_command = commands.add_parser(
        "review", help="compare saved posts with current screening without modifying evidence"
    )
    review_command.add_argument("--hours", type=int, default=24, choices=range(1, 721), metavar="1-720")
    review_command.add_argument("--limit", type=int, default=500, choices=range(1, 5001), metavar="1-5000")
    review_command.add_argument("--label", choices=("crypto", "uncertain", "unrelated"))
    verify_command = commands.add_parser(
        "verify", help="compare one original-post page against saved IDs without ingestion"
    )
    verify_command.add_argument("--target", required=True, help="configured user target name")
    auth = commands.add_parser("auth", help="save your X session through hidden local prompts")
    auth.add_argument("--name", default="monitor")
    run = commands.add_parser("run", help="poll configured targets and print JSON alerts")
    run.add_argument("--once", action="store_true")
    research_command = commands.add_parser("research", help="measure price responses in an independent worker")
    research_command.add_argument("--once", action="store_true")
    research_check = commands.add_parser("research-status", help="show market worker health and job counts")
    research_check.add_argument("--check", action="store_true")
    commands.add_parser("research-export", help="export stored candle studies, including failed measurements")
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
        research_dir = config.state_dir / "research"
        research_db = research_dir / "market.sqlite"
        if args.command == "research-status":
            result = research_status(research_db)
            output(result)
            return 2 if args.check and not result["healthy"] else 0
        if args.command == "research-export":
            if not research_db.exists():
                return 0
            with closing(sqlite3.connect(research_db.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
                for row in connection.execute("SELECT result FROM jobs WHERE result != '{}' ORDER BY post_id,horizon"):
                    output(json.loads(row[0]))
            return 0
        if args.command == "research":
            with writer_lock(research_dir), closing(Research(research_db)) as research:
                return 0 if run_research(database, research, output, diagnostic, once=args.once) else 2
        if args.command in {"status", "export", "stats", "coverage", "review"} and not database.exists():
            output({"event": "not_started", "targets": [], "posts": 0, "pending_alerts": 0})
            return 2 if args.command == "status" and args.check else 0
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
        if args.command in {"status", "export", "stats", "coverage", "review"}:
            with closing(Store(database, read_only=True)) as store:
                if args.command == "export":
                    store.export(output)
                elif args.command == "stats":
                    output(store.statistics(datetime.now(UTC) - timedelta(hours=args.hours)))
                elif args.command == "coverage":
                    now = datetime.now(UTC)
                    output(coverage(store, config, now - timedelta(hours=args.hours), now))
                elif args.command == "review":
                    for item in review(
                        store, datetime.now(UTC) - timedelta(hours=args.hours), limit=args.limit, label=args.label
                    ):
                        output(item)
                else:
                    status = store.status([target.key for target in config.targets])
                    now = datetime.now(UTC)
                    for target in status["targets"]:
                        newest = target.get("details", {}).get("newest_published_at")
                        if newest:
                            target["newest_post_age_seconds"] = (now - parse_time(newest)).total_seconds()
                        target["stale"] = (now - parse_time(target["completed_at"])).total_seconds() > target.get(
                            "details", {}
                        ).get("interval_seconds", config.poll_seconds) * 3
                    seen = {item["target"]: item for item in status["targets"]}
                    health = collection_status(config, now)
                    status.update({key: health[key] for key in ("healthy", "degraded", "coverage_verified")})
                    status["unpolled_targets"] = [target.name for target in config.targets if target.key not in seen]
                    output(status)
                    if args.check and not status["healthy"]:
                        return 2
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
        if args.command == "verify":
            target = next((target for target in config.targets if target.name == args.target), None)
            if target is None or target.kind != "user":
                raise ValueError("verify requires a configured user target name")
            accounts = config.state_dir / "accounts.sqlite"
            if not accounts.exists():
                raise ValueError("no X session configured")
            with closing(XCollector(accounts, page_size=config.page_size, max_pages=1)) as collector:

                async def fetch_originals():
                    async with asyncio.timeout(config.timeout_seconds):
                        return await collector.fetch(replace(target, include_replies=False), config.page_size)

                result = asyncio.run(fetch_originals())
            if database.exists():
                with closing(Store(database, read_only=True)) as store:
                    known = store.existing_ids([post.id for post in result.posts])
            else:
                known = frozenset()
            output(
                {
                    "target": target.key,
                    "endpoint": "original_posts",
                    "status": result.status,
                    "error_code": result.error_code,
                    "details": result.details,
                    "posts": [
                        {
                            "id": post.id,
                            "url": post.url,
                            "published_at": post.published_at,
                            "already_stored": post.id in known,
                        }
                        for post in result.posts
                    ],
                    "coverage_verified": False,
                    "evidence_modified": False,
                    "meaning": "Alternate X endpoint sample, not an independent feed or exhaustive coverage proof",
                }
            )
            return 0 if result.status == "ok" else 2
        accounts = config.state_dir / "accounts.sqlite"
        if not accounts.exists():
            raise ValueError("no X session configured; run the auth command in a local terminal first")
        with (
            writer_lock(config.state_dir),
            closing(Store(database)) as store,
            closing(
                XCollector(
                    accounts,
                    page_size=config.page_size,
                    max_pages=config.max_pages,
                    timeout_seconds=config.timeout_seconds - 0.25,
                )
            ) as collector,
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
