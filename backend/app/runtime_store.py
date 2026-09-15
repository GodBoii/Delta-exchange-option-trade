"""Compatibility boundary for existing record shapes; control claims use named Convex transactions."""

import asyncio
import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from .application_data import ConvexApplicationData
from .errors import AppError

TABLES = frozenset(
    {
        "strategies",
        "executions",
        "execution_orders",
        "strategy_capital_slots",
        "strategy_proposals",
        "automation_settings",
        "automation_agent_runs",
        "automation_market_snapshots",
    }
)
OPTIONS = frozenset({"select", "limit", "order", "offset"})


def conditions(params: dict[str, str]) -> list[dict[str, str]]:
    result = []
    for field, expression in params.items():
        if field in OPTIONS:
            continue
        if not field.replace("_", "").isalnum():
            raise AppError(500, "Unsupported record filter", "unsupported_record_query")
        op, _, value = expression.partition(".")
        if op == "not" and value.startswith("is."):
            op, value = "not.is", value[3:]
        if op not in {"eq", "neq", "in", "gt", "gte", "lt", "lte", "is", "not.is"}:
            raise AppError(500, "Unsupported record comparison", "unsupported_record_query")
        parsed: Any = value
        if op == "in":
            if not value.startswith("(") or not value.endswith(")"):
                raise AppError(500, "Invalid record set filter", "unsupported_record_query")
            parsed = value[1:-1].split(",")
        elif op in {"is", "not.is"}:
            if value not in {"null", "true", "false"}:
                raise AppError(500, "Invalid null filter", "unsupported_record_query")
            parsed = json.loads(value)
        result.append({"field": field, "op": op, "valueJson": json.dumps(parsed)})
    return result


class ConvexRuntimeStore:
    def __init__(self, data: ConvexApplicationData) -> None:
        self.data = data

    async def select(self, table: str, params: dict[str, str]) -> list[dict[str, Any]]:
        split = next(
            (
                key
                for key in ("id", "execution_id", "strategy_id", "status", "client_order_id")
                if params.get(key, "").startswith("in.(")
            ),
            None,
        )
        if split is not None:
            values = params[split][4:-1].split(",")
            if len(values) > 100:
                rows = []
                for offset in range(0, len(values), 100):
                    for group in await asyncio.gather(
                        *(
                            self.select(
                                table,
                                {
                                    **{
                                        key: value
                                        for key, value in params.items()
                                        if key not in {"select", "limit", "offset"}
                                    },
                                    split: f"eq.{value}",
                                    "select": "*",
                                },
                            )
                            for value in values[offset : offset + 100]
                        )
                    ):
                        rows.extend(group)
            else:
                groups = await asyncio.gather(
                    *(
                        self.select(
                            table,
                            {
                                **{
                                    key: value
                                    for key, value in params.items()
                                    if key not in {"select", "limit", "offset"}
                                },
                                split: f"eq.{value}",
                                "select": "*",
                            },
                        )
                        for value in values
                    )
                )
                rows = [row for group in groups for row in group]
            return self.project(rows, params)
        filters = conditions(params)
        cursor = None
        rows = []
        while True:
            page = await self.data.request(
                "runtimeRecords:select",
                {"table": table, "conditions": filters, "paginationOpts": {"numItems": 100, "cursor": cursor}},
            )
            rows.extend(json.loads(row) for row in page["page"])
            if page["isDone"]:
                break
            if cursor == page["continueCursor"]:
                raise AppError(503, "Record pagination did not advance", "record_scan_incomplete")
            cursor = page["continueCursor"]
        return self.project(rows, params)

    @staticmethod
    def project(rows: list[dict[str, Any]], params: dict[str, str]) -> list[dict[str, Any]]:
        for ordering in reversed(params.get("order", "").split(",")):
            if not ordering:
                continue
            field, _, direction = ordering.partition(".")
            rows.sort(key=lambda row: (row.get(field) is not None, row.get(field) or ""), reverse=direction == "desc")
        offset = int(params.get("offset", "0"))
        limit = int(params.get("limit", str(len(rows))))
        rows = rows[offset : offset + limit]
        selection = params.get("select", "*")
        if selection != "*":
            projected = []
            for row in rows:
                output = {}
                for column in selection.split(","):
                    if column == "chart_images:market_json->chartImages":
                        output["chart_images"] = (row.get("market_json") or {}).get("chartImages")
                    elif ":" in column or "(" in column:
                        raise AppError(500, "Unsupported record projection", "unsupported_record_query")
                    else:
                        output[column] = row.get(column)
                projected.append(output)
            return projected
        return rows

    async def write(self, table: str, payload: dict[str, Any], conflict: str | None = None) -> list[dict[str, Any]]:
        now = datetime.now(UTC).isoformat()
        row = {"id": str(uuid4()), "created_at": now, "updated_at": now, **payload}
        if table == "automation_settings":
            row = {
                "enabled": False,
                "model_id": "deepseek/deepseek-v4.1-flash",
                "minimum_follow_up_minutes": 5,
                "maximum_agent_runs_per_day": 3,
                **row,
                "id": payload["user_id"],
            }
        elif table == "automation_agent_runs":
            row = {
                "status": "scheduled",
                "outcome": None,
                "parent_agent_run_id": None,
                "model_id": "deepseek/deepseek-v4.1-flash",
                "signals_to_inspect": [],
                **row,
            }
        elif table == "executions":
            row = {"started_at": now, "completed_at": None, "error": None, **row}
        result = await self.data.request(
            "runtimeRecords:write",
            {
                "table": table,
                "rowJson": json.dumps(row, allow_nan=False),
                **({"conflict": conflict} if conflict else {}),
            },
            mutation=True,
        )
        return [json.loads(item) for item in result]

    async def update(
        self, table: str, payload: dict[str, Any], params: dict[str, str], *, remove: bool = False
    ) -> list[dict[str, Any]]:
        lookup = {**params, "select": "id"}
        rows = (
            [{"id": params["id"][3:]}] if params.get("id", "").startswith("eq.") else await self.select(table, lookup)
        )
        output = []
        for offset in range(0, len(rows), 100):
            result = await self.data.request(
                "runtimeRecords:update",
                {
                    "table": table,
                    "ids": [row["id"] for row in rows[offset : offset + 100]],
                    "conditions": conditions(params),
                    "patchJson": json.dumps(payload, allow_nan=False),
                    "remove": remove,
                },
                mutation=True,
            )
            output.extend(json.loads(item) for item in result)
        return output

    async def rpc(self, name: str, payload: dict[str, Any]) -> Any:
        names = {
            "reserve_strategy_capital_slot": "runtimeControl:reserveCapital",
            "release_strategy_capital_slot": "runtimeControl:releaseCapital",
            "claim_automation_agent_run": "runtimeControl:claimAgent",
            "ensure_automation_fixed_runs": "runtimeControl:ensureFixedRuns",
            "cancel_redundant_automation_followups": "runtimeControl:cancelRedundantFollowups",
        }
        if name not in names:
            raise AppError(500, "Unsupported application control operation", "unsupported_record_query")
        return await self.data.request(names[name], payload, mutation=True)
