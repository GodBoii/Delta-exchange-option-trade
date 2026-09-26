"""Large research payloads live in ``ai.analysis_reports``; runtime rows keep scheduling metadata."""

from typing import Any

from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from .errors import AppError

REPORT_FIELDS = frozenset({"report_markdown", "member_responses", "tool_calls", "market_json", "account_json"})
JSON_FIELDS = frozenset({"member_responses", "tool_calls", "market_json", "account_json"})
DEFAULT_FIELDS = ("report_markdown", "member_responses", "tool_calls")
BATCH = 500


class ReportStore:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self.pool = pool

    async def read(
        self, ids: list[str], columns: str = "report_markdown,member_responses,tool_calls"
    ) -> dict[str, dict[str, Any]]:
        fields = [field for field in columns.split(",") if field]
        if any(field not in REPORT_FIELDS for field in fields):
            raise AppError(500, "Unsupported report column", "unsupported_record_query")
        result: dict[str, dict[str, Any]] = {}
        if not ids or not fields:
            return result
        query = sql.SQL("select id::text as id, {} from ai.analysis_reports where id = any(%s::uuid[])").format(
            sql.SQL(", ").join(sql.Identifier(field) for field in fields)
        )
        async with self.pool.connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
            for start in range(0, len(ids), BATCH):
                await cursor.execute(query, (ids[start : start + BATCH],))
                for row in await cursor.fetchall():
                    result[row["id"]] = row
        return result

    async def save(self, run_id: str, fields: dict[str, Any]) -> None:
        payload = {key: value for key, value in fields.items() if key in REPORT_FIELDS}
        if not payload:
            return
        names = sorted(payload)
        values = [Jsonb(payload[name]) if name in JSON_FIELDS else payload[name] for name in names]
        query = sql.SQL(
            """insert into ai.analysis_reports (id, {columns}) values (%s, {placeholders})
               on conflict (id) do update set {updates}, updated_at = now()"""
        ).format(
            columns=sql.SQL(", ").join(sql.Identifier(name) for name in names),
            placeholders=sql.SQL(", ").join(sql.Placeholder() for _ in names),
            updates=sql.SQL(", ").join(
                sql.SQL("{0} = excluded.{0}").format(sql.Identifier(name)) for name in names
            ),
        )
        async with self.pool.connection() as connection:
            await connection.execute(query, (run_id, *values))

    async def hydrate(self, rows: list[dict[str, Any]], columns: str) -> list[dict[str, Any]]:
        if columns != "*" and not any(field in columns for field in REPORT_FIELDS):
            return rows
        fields = sorted(REPORT_FIELDS.intersection(columns.split(","))) if columns != "*" else list(DEFAULT_FIELDS)
        reports = await self.read([row["id"] for row in rows], ",".join(fields))
        return [
            {**row, **{key: value for key, value in reports.get(row["id"], {}).items() if key in REPORT_FIELDS}}
            for row in rows
        ]

    async def snapshots(self, params: dict[str, str]) -> list[dict[str, Any]]:
        """Read market snapshots by snapshot ID. Callers first authorize the referencing job."""
        expression = params.get("id", "")
        if expression.startswith("eq."):
            snapshot_ids = [expression[3:]]
        elif expression.startswith("in.(") and expression.endswith(")"):
            snapshot_ids = [item for item in expression[4:-1].split(",") if item]
        else:
            raise AppError(500, "Snapshot reads require explicit IDs", "snapshot_ids_required")
        chart_only = "chart_images" in params.get("select", "")
        columns = (
            "snapshot_id::text as snapshot_id, id::text as run_id, market_json->'chartImages' as chart_images"
            if chart_only
            else (
                "snapshot_id::text as snapshot_id, id::text as run_id, market_json, account_json, "
                """to_char(created_at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"') as created_at"""
            )
        )
        async with self.pool.connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
            await cursor.execute(
                sql.SQL("select {} from ai.analysis_reports where snapshot_id = any(%s::uuid[])").format(
                    sql.SQL(columns)
                ),
                (snapshot_ids,),
            )
            rows = await cursor.fetchall()
        return [
            {
                **row,
                "id": row["snapshot_id"],
                "chart_images": row.get("chart_images") or (row.get("market_json") or {}).get("chartImages", []),
            }
            for row in rows
        ]
