"""Small read-only probes. Import no collector, event loop or market client."""

import json
import sqlite3
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from .config import Config, load_config
from .diagnostics import ACCESS_ERRORS


def collection_status(config: Config, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    database = config.state_dir / "monitor.sqlite"
    if not database.exists():
        return {"healthy": False, "degraded": True, "reason": "not_started", "targets": []}
    connection = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.2)
    connection.row_factory = sqlite3.Row
    try:
        targets = []
        # One indexed lookup per target, rather than grouping all historical attempts.
        with connection:
            for target in config.targets:
                latest = connection.execute(
                    "SELECT completed_at,status,error_code FROM polls WHERE target=? ORDER BY id DESC LIMIT 1",
                    (target.key,),
                ).fetchone()
                success = connection.execute(
                    "SELECT completed_at FROM polls WHERE target=? AND status='ok' ORDER BY id DESC LIMIT 1",
                    (target.key,),
                ).fetchone()
                max_age = config.interval(target) * 3 + config.timeout_seconds
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
                targets.append(
                    {
                        "target": target.key,
                        "healthy": healthy,
                        "latest_status": latest["status"] if latest else "unpolled",
                        "error_code": latest["error_code"] if latest else None,
                        "last_success_at": success[0] if success else None,
                        "success_age_seconds": round(age, 3) if age is not None else None,
                    }
                )
        return {
            "healthy": bool(targets) and all(target["healthy"] for target in targets),
            "degraded": any(target["latest_status"] != "ok" for target in targets),
            "coverage_verified": False,
            "targets": targets,
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
        config = load_config(Path(args[0]))
        result = (
            collection_status(config)
            if args[1] == "collector"
            else research_status(config.state_dir / "research" / "market.sqlite")
        )
    except (OSError, ValueError, sqlite3.Error):
        result = {"healthy": False, "reason": "probe_read_failed"}
    print(json.dumps(result), flush=True)
    return 0 if result["healthy"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
