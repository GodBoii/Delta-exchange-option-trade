"""Small read-only probes. Import no collector, event loop or market client."""

import json
import sqlite3
import sys
import time
import tomllib
from datetime import UTC, datetime
from pathlib import Path

from .diagnostics import ACCESS_ERRORS


def collection_status(config, now: datetime | None = None) -> dict:
    """Accept the CLI's validated config without importing its dataclass dependency tree."""
    return _collector_snapshot(
        config.state_dir, [(t.key, config.interval(t)) for t in config.targets], config.timeout_seconds, now
    )


def _collector_snapshot(
    state_dir: Path, targets: list[tuple[str, float]], timeout: float, now: datetime | None = None
) -> dict:
    now = now or datetime.now(UTC)
    database = state_dir / "monitor.sqlite"
    if not database.exists():
        return {"healthy": False, "degraded": True, "coverage_verified": False, "reason": "not_started", "targets": []}
    connection = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.2)
    connection.row_factory = sqlite3.Row
    try:
        results = []
        # One indexed lookup per target, rather than grouping all historical attempts.
        with connection:
            for key, interval in targets:
                latest = connection.execute(
                    "SELECT completed_at,status,error_code FROM polls WHERE target=? ORDER BY id DESC LIMIT 1",
                    (key,),
                ).fetchone()
                success = connection.execute(
                    "SELECT completed_at FROM polls WHERE target=? AND status='ok' ORDER BY id DESC LIMIT 1",
                    (key,),
                ).fetchone()
                max_age = interval * 3 + timeout
                age = (now - datetime.fromisoformat(success[0])).total_seconds() if success else None
                attempt_age = (now - datetime.fromisoformat(latest[0])).total_seconds() if latest else None
                healthy = bool(
                    latest
                    and success
                    and latest["error_code"] not in ACCESS_ERRORS
                    and age is not None
                    and 0 <= age <= max_age
                    and attempt_age is not None
                    and 0 <= attempt_age <= max_age
                )
                results.append(
                    {
                        "target": key,
                        "healthy": healthy,
                        "latest_status": latest["status"] if latest else "unpolled",
                        "error_code": latest["error_code"] if latest else None,
                        "last_success_at": success[0] if success else None,
                        "success_age_seconds": round(age, 3) if age is not None else None,
                    }
                )
        return {
            "healthy": bool(results) and all(target["healthy"] for target in results),
            "degraded": any(target["latest_status"] != "ok" for target in results),
            "coverage_verified": False,
            "targets": results,
        }
    finally:
        connection.close()


def research_status(path: Path, now: float | None = None) -> dict:
    if not path.exists():
        return {"event": "research_not_started", "healthy": False}
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.2)
    try:
        heartbeat = connection.execute("SELECT at FROM heartbeat WHERE id=1").fetchone()
        counts = dict(connection.execute("SELECT status,count(*) FROM jobs GROUP BY status"))
        oldest = connection.execute("SELECT min(due) FROM jobs WHERE status='pending'").fetchone()[0]
        now = time.time() if now is None else now
        return {
            "jobs": counts,
            "last_cycle_at": heartbeat[0] if heartbeat else None,
            "overdue_seconds": max(0, now - oldest) if oldest is not None else 0,
            "healthy": bool(
                heartbeat
                and 0 <= now - heartbeat[0] < 300
                and not counts.get("failed")
                and (oldest is None or now - oldest < 300)
            ),
        }
    finally:
        connection.close()


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2 or args[1] not in {"collector", "research"}:
        print(json.dumps({"healthy": False, "reason": "invalid_probe_arguments"}))
        return 2
    try:
        path = Path(args[0]).resolve()
        with path.open("rb") as source:
            raw = tomllib.load(source)
        state = (path.parent / raw.get("state_dir", "state")).resolve()
        # Probe only the fields it consumes. Full configuration validation belongs to the CLI.
        timeout = raw.get("timeout_seconds", 15)
        targets = []
        for target in raw.get("targets", []):
            interval = target.get("poll_seconds", raw.get("poll_seconds", 15))
            if type(interval) not in {int, float} or not 15 <= interval <= 86400:
                raise ValueError("invalid probe interval")
            replies = target.get("include_replies", True)
            if type(replies) is not bool or target["kind"] not in {"user", "search", "list"}:
                raise ValueError("invalid probe target")
            targets.append((f"{target['kind']}:{target['value']}:{replies}", interval))
        if type(timeout) not in {int, float} or not 5 <= timeout <= 300:
            raise ValueError("invalid probe timeout")
        result = (
            _collector_snapshot(state, targets, timeout)
            if args[1] == "collector"
            else research_status(state / "research" / "market.sqlite")
        )
    except (OSError, ValueError, TypeError, KeyError, AttributeError, sqlite3.Error):
        result = {"healthy": False, "reason": "probe_read_failed"}
    print(json.dumps(result), flush=True)
    return 0 if result["healthy"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
