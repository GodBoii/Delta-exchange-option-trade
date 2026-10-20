"""Local CLI, run from the repository parent with python -m reddit_monitor."""

import argparse
import json
import sqlite3
import sys
import time
import tomllib
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .collector import CollectionError, ReplayClient, RSSClient, collect
from .domain import FULLNAME, SUBREDDIT, timestamp
from .storage import Store, writer_lock

ROOT = Path(__file__).resolve().parent


def config(path: Path) -> dict:
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    if data.get("access") not in {"offline", "approved_rss_research"}:
        raise ValueError("unsupported_access_route")
    subs = data.get("subreddits")
    if (
        not isinstance(subs, list)
        or not 1 <= len(subs) <= 8
        or any(not isinstance(s, str) or not SUBREDDIT.fullmatch(s) for s in subs)
    ):
        raise ValueError("invalid_subreddit_roster")
    if len({s.lower() for s in subs}) != len(subs):
        raise ValueError("duplicate_subreddits")
    for field, low, high in (
        ("page_limit", 1, 10),
        ("page_size", 1, 100),
        ("poll_seconds", 60, 86400),
        ("request_spacing_seconds", 2, 120),
        ("retention_hours", 1, 48),
    ):
        if type(data.get(field)) is not int or not low <= data[field] <= high:
            raise ValueError("invalid_" + field)
    state = (path.parent / data["state_dir"]).resolve()
    if not state.is_relative_to(ROOT) or state == ROOT:
        raise ValueError("state_must_be_inside_reddit_monitor")
    data["state_path"] = state
    return data


def emit(value: dict):
    print(json.dumps(value, ensure_ascii=True), flush=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Isolated Reddit research evidence, no notifications or trading")
    parser.add_argument("--config", type=Path, default=ROOT / "config.example.toml")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate")
    health = commands.add_parser("status")
    health.add_argument("--check", action="store_true")
    commands.add_parser("export")
    replay = commands.add_parser("replay")
    replay.add_argument("tape", type=Path)
    replay.add_argument("--subreddit", required=True)
    replay.add_argument("--stream", choices=["posts", "comments"], default="posts")
    replay.add_argument("--at", required=True)
    run = commands.add_parser("run")
    run.add_argument("--once", action="store_true")
    erase = commands.add_parser("erase")
    erase.add_argument("native_id")
    commands.add_parser("purge")
    args = parser.parse_args(argv)
    store = None
    try:
        settings = config(args.config.resolve())
        if args.command == "validate":
            emit({"valid": True, "access": settings["access"], "live_access_verified": False})
            return 0
        client = None
        if args.command == "run":
            if settings["access"] != "approved_rss_research":
                raise CollectionError("research_route_approval_required")
            client = RSSClient(
                settings.get("approval_reference", ""),
                settings.get("user_agent", ""),
                settings["request_spacing_seconds"],
            )
        if args.command == "replay":
            timestamp(args.at)
            if args.subreddit.lower() not in {s.lower() for s in settings["subreddits"]}:
                raise ValueError("subreddit_not_configured")
            if settings["access"] != "offline":
                raise ValueError("replay_requires_offline_state")
            client = ReplayClient(args.tape)
        directory = settings["state_path"]
        if args.command in {"export", "status"} and not (directory / "evidence.sqlite").exists():
            emit({"status": "never_collected", "coverage_verified": False})
            return 2
        reading = args.command in {"export", "status"}
        with nullcontext() if reading else writer_lock(directory):
            store = Store(directory, readonly=reading)
            store.claim_mode(settings["access"])
            now = datetime.now(UTC)
            if args.command == "purge" or (settings["access"] != "offline" and args.command == "run"):
                store.purge_before((now - timedelta(hours=settings["retention_hours"])).isoformat())
            if args.command == "export":
                cutoff = (
                    (now - timedelta(hours=settings["retention_hours"])).isoformat()
                    if settings["access"] != "offline"
                    else ""
                )
                for envelope in store.export(cutoff):
                    emit(envelope)
                return 0
            if args.command == "status":
                rows = store.status()
                expected = {
                    f"{sub.lower()}:{stream}" for sub in settings["subreddits"] for stream in ("posts", "comments")
                }
                missing = sorted(expected - {row["target"] for row in rows})
                operational = (
                    bool(rows)
                    and not missing
                    and settings["access"] != "offline"
                    and all(
                        row["last_success"]
                        and 0 <= (now - timestamp(row["last_success"])).total_seconds() <= settings["poll_seconds"] * 3
                        and row["status"] in {"bounded_read", "window_unverified"}
                        for row in rows
                    )
                )
                emit(
                    {
                        "targets": rows,
                        "missing_targets": missing,
                        "recent_reads": operational,
                        "coverage_verified": False,
                        "live_access_verified": False,
                        "rss_identity_fields": "unavailable",
                    }
                )
                return 2 if args.check and not operational else 0
            if args.command == "erase":
                if not FULLNAME.fullmatch(args.native_id):
                    raise ValueError("invalid_native_id")
                store.erase(args.native_id)
                emit({"erased": args.native_id, "external_exports_require_separate_erasure": True})
                return 0
            if args.command == "purge":
                emit({"purged": True})
                return 0
            if args.command == "replay":
                status = collect(
                    store, client, args.subreddit, args.stream, settings["page_limit"], settings["page_size"], args.at
                )
                emit({"status": status, "mode": "synthetic_replay", "coverage_verified": False})
                return 0 if status == "bounded_read" else 2
            while True:
                statuses = []
                for subreddit in settings["subreddits"]:
                    for stream in ("posts", "comments"):
                        now = datetime.now(UTC)
                        store.purge_before((now - timedelta(hours=settings["retention_hours"])).isoformat())
                        status = collect(
                            store,
                            client,
                            subreddit,
                            stream,
                            settings["page_limit"],
                            settings["page_size"],
                            now.isoformat(),
                        )
                        statuses.append(status)
                        print(
                            json.dumps({"target": subreddit + ":" + stream, "status": status}),
                            file=sys.stderr,
                            flush=True,
                        )
                if args.once:
                    return 0 if all(s in {"window_unverified", "bounded_read"} for s in statuses) else 2
                time.sleep(settings["poll_seconds"])
    except CollectionError as error:
        print(json.dumps({"error": error.code}), file=sys.stderr)
        return 2
    except (ValueError, KeyError, TypeError, OSError, sqlite3.Error, OverflowError):
        # Never print arbitrary local/API exception strings, paths, headers or supplied secrets.
        print(
            json.dumps({"error": "invalid_input_or_storage", "action": "check config, fixture and state permissions"}),
            file=sys.stderr,
        )
        return 2
    except KeyboardInterrupt:
        return 130
    finally:
        if store is not None:
            store.close()


if __name__ == "__main__":
    raise SystemExit(main())
