"""Large research payloads live in Supabase; Convex stores scheduling metadata."""

from typing import Any

from .errors import AppError

REPORT_FIELDS = frozenset({"report_markdown", "member_responses", "tool_calls", "market_json", "account_json"})


class ReportStore:
    def __init__(self, db: Any) -> None:
        self.db = db

    async def read(
        self, ids: list[str], columns: str = "report_markdown,member_responses,tool_calls"
    ) -> dict[str, dict[str, Any]]:
        result = {}
        for start in range(0, len(ids), 100):
            response = await self.db.client.get(
                f"{self.db.settings.supabase_url}/rest/v1/analysis_reports",
                headers=self.db.admin_headers,
                params={"id": f"in.({','.join(ids[start : start + 100])})", "select": "id," + columns},
            )
            for row in self.db._json(response, "Analysis reports unavailable"):
                result[row["id"]] = row
        return result

    async def save(self, run_id: str, fields: dict[str, Any]) -> None:
        payload = {key: value for key, value in fields.items() if key in REPORT_FIELDS}
        if not payload:
            return
        response = await self.db.client.post(
            f"{self.db.settings.supabase_url}/rest/v1/analysis_reports",
            headers={**self.db.admin_headers, "Prefer": "resolution=merge-duplicates"},
            params={"on_conflict": "id"},
            json={"id": run_id, **payload},
        )
        if response.is_error:
            raise AppError(503, "Could not persist the analysis report", "report_storage_failed")

    async def hydrate(self, rows: list[dict[str, Any]], columns: str) -> list[dict[str, Any]]:
        if columns != "*" and not any(field in columns for field in REPORT_FIELDS):
            return rows
        fields = (
            sorted(REPORT_FIELDS.intersection(columns.split(",")))
            if columns != "*"
            else [
                "report_markdown",
                "member_responses",
                "tool_calls",
            ]
        )
        reports = await self.read([row["id"] for row in rows], ",".join(fields))
        return [
            {**row, **{key: value for key, value in reports.get(row["id"], {}).items() if key in REPORT_FIELDS}}
            for row in rows
        ]

    async def snapshots(self, params: dict[str, str]) -> list[dict[str, Any]]:
        chart_only = "chart_images" in params.get("select", "")
        query = {
            "select": "snapshot_id,chart_images:market_json->chartImages"
            if chart_only
            else "snapshot_id,market_json,account_json,created_at"
        }
        if "id" in params:
            query["snapshot_id"] = params["id"]
        else:
            raise AppError(500, "Snapshot reads require explicit IDs", "snapshot_ids_required")
        # Callers first authorize the referenced job. Reports are never exposed directly to browsers.
        response = await self.db.client.get(
            f"{self.db.settings.supabase_url}/rest/v1/analysis_reports",
            headers=self.db.admin_headers,
            params=query,
        )
        return [
            {
                **row,
                "id": row["snapshot_id"],
                "chart_images": row.get("chart_images") or row.get("market_json", {}).get("chartImages", []),
            }
            for row in self.db._json(response, "Market snapshots unavailable")
        ]
