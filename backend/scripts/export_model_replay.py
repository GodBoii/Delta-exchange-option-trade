"""Export historical analysis evidence with read-only transactions, without account secrets."""

from __future__ import annotations

import argparse
import base64
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
from psycopg.rows import dict_row


def export_evidence(output: Path, hours: int) -> dict:
    end = datetime.now(UTC)
    start = end - timedelta(hours=hours)
    url = os.environ["AI_DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(url, row_factory=dict_row) as connection:
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        reports = connection.execute(
            """select id::text, snapshot_id::text, report_markdown, member_responses,
                      tool_calls, market_json, account_json, created_at
               from ai.analysis_reports where created_at >= %s and created_at < %s
               order by created_at, id""",
            (start, end),
        ).fetchall()
        sessions = connection.execute(
            """select session_id, agent_id, runs, created_at, updated_at
               from ai.automation_agent_sessions where updated_at >= %s""",
            (int(start.timestamp()),),
        ).fetchall()
        charts = connection.execute(
            """select run_id::text, chart_id, content from ai.chart_images
               where run_id = any(%s::uuid[]) order by run_id, chart_id""",
            ([row["id"] for row in reports],),
        ).fetchall()
    evidence = {
        "window": {"start": start.isoformat(), "end": end.isoformat(), "hours": hours},
        "reports": reports,
        "sessions": sessions,
        "charts": [
            {
                "run_id": row["run_id"],
                "chart_id": row["chart_id"],
                "base64": base64.b64encode(bytes(row["content"])).decode("ascii"),
            }
            for row in charts
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, default=str), encoding="utf-8")
    return {"window": evidence["window"], "reports": len(reports), "sessions": len(sessions), "charts": len(charts)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--hours", type=int, default=72)
    args = parser.parse_args()
    if not 1 <= args.hours <= 168:
        parser.error("hours must be between 1 and 168")
    print(json.dumps(export_evidence(args.output, args.hours)))


if __name__ == "__main__":
    main()
