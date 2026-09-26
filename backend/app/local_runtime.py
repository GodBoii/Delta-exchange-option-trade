"""Indexed PostgreSQL access for the trading records stored on Ubuntu."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from .errors import AppError
from .local_application_data import DEFAULT_AUTOMATION, LocalApplicationData
from .local_control import LocalControl

TABLES = {
    "strategies": "strategies",
    "executions": "executions",
    "execution_orders": "execution_orders",
    "strategy_capital_slots": "strategy_capital_slots",
    "strategy_proposals": "strategy_proposals",
    "automation_agent_runs": "analysis_jobs",
}
OPTIONS = frozenset({"select", "limit", "order", "offset"})
TYPED_FIELDS = {
    "id": "id",
    "user_id": "owner_id",
    "status": "status",
    "created_at": "created_at",
    "entry_at": "entry_at",
    "exit_at": "exit_at",
    "scheduled_for": "scheduled_for",
    "activation_time": "activation_time",
    "started_at": "started_at",
}
RELATIONS = {
    "executions": "strategy_id",
    "execution_orders": "execution_id",
    "strategy_capital_slots": "strategy_id",
    "strategy_proposals": "strategy_id",
    "automation_agent_runs": "strategy_proposal_id",
}
UNIQUE_FIELDS = {
    "execution_orders": "client_order_id",
    "strategy_capital_slots": "slot_number",
    "automation_agent_runs": "run_key",
}


def _field(table: str, field: str) -> sql.Composable:
    if not field.replace("_", "").isalnum():
        raise AppError(500, "Unsupported record field", "unsupported_record_query")
    if field in TYPED_FIELDS:
        return sql.Identifier(TYPED_FIELDS[field])
    if field == RELATIONS.get(table):
        return sql.Identifier("relation_id")
    if field == UNIQUE_FIELDS.get(table):
        return sql.Identifier("unique_key")
    return sql.SQL("(data ->> {})").format(sql.Literal(field))


def _filters(table: str, params: dict[str, str]) -> tuple[sql.Composable, list[Any]]:
    clauses: list[sql.Composable] = []
    values: list[Any] = []
    for field, expression in params.items():
        if field in OPTIONS:
            continue
        target = _field(table, field)
        operator, _, value = expression.partition(".")
        if operator == "not" and value.startswith("is."):
            operator, value = "not.is", value[3:]
        if operator == "is" and value == "null":
            clauses.append(sql.SQL("{} is null").format(target))
        elif operator == "not.is" and value == "null":
            clauses.append(sql.SQL("{} is not null").format(target))
        elif operator in {"eq", "neq", "gt", "gte", "lt", "lte"}:
            comparison = {"eq": "=", "neq": "<>", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}[operator]
            clauses.append(sql.SQL("{} {} %s").format(target, sql.SQL(comparison)))
            values.append(value)
        elif operator == "in" and value.startswith("(") and value.endswith(")"):
            clauses.append(sql.SQL("{} = any(%s)").format(target))
            values.append(value[1:-1].split(","))
        else:
            raise AppError(500, "Unsupported record filter", "unsupported_record_query")
    if not clauses:
        return sql.SQL("true"), values
    return sql.SQL(" and ").join(clauses), values


def _ordering(table: str, params: dict[str, str]) -> sql.Composable:
    terms = []
    for value in params.get("order", "").split(","):
        if not value:
            continue
        field, _, direction = value.partition(".")
        if direction not in {"asc", "desc", ""}:
            raise AppError(500, "Unsupported sort order", "unsupported_record_query")
        terms.append(sql.SQL("{} {}").format(_field(table, field), sql.SQL(direction or "asc")))
    if not terms:
        terms = [sql.SQL("created_at asc"), sql.SQL("id asc")]
    return sql.SQL(", ").join(terms)


def _project(rows: list[dict[str, Any]], columns: str) -> list[dict[str, Any]]:
    if columns == "*":
        return rows
    names = columns.split(",")
    if any(not name.replace("_", "").isalnum() for name in names):
        raise AppError(500, "Unsupported record projection", "unsupported_record_query")
    return [{name: row.get(name) for name in names} for row in rows]


def _sort_and_project(rows: list[dict[str, Any]], params: dict[str, str]) -> list[dict[str, Any]]:
    for ordering in reversed(params.get("order", "").split(",")):
        if not ordering:
            continue
        field, _, direction = ordering.partition(".")
        rows.sort(key=lambda row: (row.get(field) is not None, row.get(field) or ""), reverse=direction == "desc")
    offset = int(params.get("offset", "0"))
    limit = int(params.get("limit", str(len(rows))))
    return _project(rows[offset : offset + limit], params.get("select", "*"))


class AutomationSettings:
    """Per-user automation preferences stored on ``trade.users`` plus the shared analysis row."""

    fields = ("enabled", "model_id", "minimum_follow_up_minutes", "maximum_agent_runs_per_day")

    def __init__(self, data: LocalApplicationData) -> None:
        self.data = data

    async def select(self, params: dict[str, str], *, raw: bool = False) -> list[dict[str, Any]]:
        user_filter = params.get("user_id", "")
        if user_filter.startswith("eq."):
            one = await self.data.request("settings:automationForUser", {"userId": user_filter[3:]})
            rows = [one] if one is not None else []
        else:
            enabled_filter = params.get("enabled", "")
            enabled: bool | None = None
            if enabled_filter in {"eq.true", "neq.false"}:
                enabled = True
            elif enabled_filter in {"eq.false", "neq.true"}:
                enabled = False
            rows = []
            cursor = None
            while True:
                page = await self.data.request(
                    "settings:automationPage",
                    {
                        "paginationOpts": {"numItems": 100, "cursor": cursor},
                        **({"enabled": enabled} if enabled is not None else {}),
                    },
                )
                rows.extend(page["page"])
                if page["isDone"]:
                    break
                cursor = page["continueCursor"]
            if user_filter != "neq.global":
                global_settings = await self.data.request("settings:automationForUser", {"userId": "global"})
                if global_settings:
                    rows.append(global_settings)
        for field, expression in params.items():
            if field in OPTIONS:
                continue
            op, _, value = expression.partition(".")
            if op not in {"eq", "neq"}:
                raise AppError(500, "Unsupported settings filter", "unsupported_record_query")
            rows = [row for row in rows if (str(row.get(field)).lower() == value.lower()) == (op == "eq")]
        return rows if raw else _sort_and_project(rows, params)

    async def write(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        current = await self.select({"user_id": f"eq.{payload['user_id']}"})
        value = {**DEFAULT_AUTOMATION, **(current[0] if current else {}), **payload}
        row = await self.data.request(
            "settings:saveAutomation",
            {"userId": payload["user_id"], "value": {key: value[key] for key in self.fields}},
            mutation=True,
        )
        return [row]


def _timestamp(value: Any, fallback: datetime | None = None) -> datetime | None:
    if value is None:
        return fallback
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise AppError(422, "Record timestamp lacks a timezone", "record_timestamp_invalid")
    return parsed


class LocalRuntimeStore:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self.pool = pool
        self.data = LocalApplicationData(pool)
        self.data.control = LocalControl(self)
        self.automation_settings = AutomationSettings(self.data)

    async def select(self, table: str, params: dict[str, str], *, raw: bool = False) -> list[dict[str, Any]]:
        if table == "automation_settings":
            return await self.automation_settings.select(params, raw=raw)
        if table not in TABLES:
            raise AppError(500, "Unknown local record table", "unsupported_record_query")
        where, values = _filters(table, params)
        query = sql.SQL("select data from {} where {} order by {}").format(
            sql.Identifier("trade", TABLES[table]), where, _ordering(table, params)
        )
        limit = params.get("limit")
        offset = params.get("offset")
        if limit is not None:
            query += sql.SQL(" limit %s")
            values.append(max(0, int(limit)))
        if offset is not None:
            query += sql.SQL(" offset %s")
            values.append(max(0, int(offset)))
        async with self.pool.connection() as connection, connection.cursor() as cursor:
            await cursor.execute(query, values)
            rows = [row[0] for row in await cursor.fetchall()]
        return rows if raw else _project(rows, params.get("select", "*"))

    async def _owner(self, connection: Any, table: str, row: dict[str, Any]) -> str:
        owner = str(row.get("user_id") or "")
        if table == "executions":
            parent = await connection.execute(
                "select owner_id from trade.strategies where id=%s", (row["strategy_id"],)
            )
            owner_row = await parent.fetchone()
            owner = owner_row[0] if owner_row else ""
        elif table == "execution_orders":
            parent = await connection.execute(
                "select owner_id from trade.executions where id=%s", (row["execution_id"],)
            )
            owner_row = await parent.fetchone()
            owner = owner_row[0] if owner_row else ""
        if not owner:
            raise AppError(409, "Record owner or parent missing", "record_owner_missing")
        return owner

    @staticmethod
    def _unique(table: str, row: dict[str, Any]) -> str | None:
        if table == "strategy_proposals" and row.get("shared_decision_id"):
            return f"shared:{row['shared_decision_id']}"
        field = UNIQUE_FIELDS.get(table)
        return str(row[field]) if field and row.get(field) is not None else None

    async def _save(self, connection: Any, table: str, row: dict[str, Any], *, existing: bool) -> dict[str, Any]:
        owner = await self._owner(connection, table, row)
        relation = next(
            (str(row[key]) for key in ("execution_id", "strategy_id", "strategy_proposal_id") if row.get(key)), None
        )
        status = str(row.get("status") or row.get("state") or "")
        created = _timestamp(row.get("created_at") or row.get("started_at"), datetime.now(UTC))
        values = (
            owner,
            status,
            relation,
            self._unique(table, row),
            created,
            _timestamp(row.get("entry_at")),
            _timestamp(row.get("exit_at")),
            _timestamp(row.get("scheduled_for")),
            _timestamp(row.get("activation_time")),
            _timestamp(row.get("started_at")),
            Jsonb(row),
            row["id"],
        )
        target = sql.Identifier("trade", TABLES[table])
        if existing:
            current = await connection.execute(
                sql.SQL("select owner_id,relation_id from {} where id=%s for update").format(target), (row["id"],)
            )
            stored = await current.fetchone()
            if not stored or stored[0] != owner:
                raise AppError(409, "Record owner cannot change", "record_owner_changed")
            if table in {"executions", "execution_orders"} and stored[1] != relation:
                raise AppError(409, "Execution parent cannot change", "record_parent_changed")
            await connection.execute(
                sql.SQL("""update {} set owner_id=%s,status=%s,relation_id=%s,unique_key=%s,
                   created_at=%s,entry_at=%s,exit_at=%s,scheduled_for=%s,activation_time=%s,
                   started_at=%s,data=%s where id=%s""").format(target),
                values,
            )
        else:
            await connection.execute(
                sql.SQL("""insert into {} (owner_id,status,relation_id,unique_key,created_at,
                   entry_at,exit_at,scheduled_for,activation_time,started_at,data,id)
                   values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""").format(target),
                values,
            )
        if table == "strategies" and status in {"completed", "cancelled"}:
            slots = await connection.execute(
                "select data from trade.strategy_capital_slots where relation_id=%s for update", (row["id"],)
            )
            for item in await slots.fetchall():
                await self._save(connection, "strategy_capital_slots", {
                    **item[0], "status": "available", "strategy_id": None, "proposal_id": None,
                    "reserved_at": None, "released_at": datetime.now(UTC).isoformat(),
                }, existing=True)
        return row

    async def write(self, table: str, payload: dict[str, Any], conflict: str | None = None) -> list[dict[str, Any]]:
        if table == "automation_settings":
            return await self.automation_settings.write(payload)
        if table not in TABLES:
            raise AppError(500, "Unknown local record table", "unsupported_record_query")
        now = datetime.now(UTC).isoformat()
        row = {"id": str(uuid4()), "created_at": now, "updated_at": now, **payload}
        if table == "automation_agent_runs":
            row = {
                "status": "scheduled",
                "outcome": None,
                "parent_agent_run_id": None,
                "model_id": "xiaomi/mimo-v2.6-pro",
                "signals_to_inspect": [],
                **row,
            }
        if table == "executions":
            row = {"started_at": now, "completed_at": None, "error": None, **row}
        async with self.pool.connection() as connection:
            existing = await connection.execute(
                sql.SQL("select data from {} where id=%s for update").format(sql.Identifier("trade", TABLES[table])),
                (row["id"],),
            )
            current = await existing.fetchone()
            if not current and conflict:
                key = str(row.get(conflict) or "")
                if key:
                    owner = await self._owner(connection, table, row)
                    owner_clause = sql.SQL("") if table == "execution_orders" else sql.SQL(" and owner_id=%s")
                    lookup_values = (key,) if table == "execution_orders" else (key, owner)
                    existing = await connection.execute(
                        sql.SQL("select data from {} where unique_key=%s{} for update").format(
                            sql.Identifier("trade", TABLES[table]), owner_clause
                        ), lookup_values,
                    )
                    current = await existing.fetchone()
            if current:
                if not conflict:
                    raise AppError(409, "Record already exists", "record_exists")
                row = {**current[0], **row, "id": current[0]["id"], "created_at": current[0].get("created_at", now)}
            await self._save(connection, table, row, existing=bool(current))
        return [row]

    async def update(
        self, table: str, payload: dict[str, Any], params: dict[str, str], *, remove: bool = False
    ) -> list[dict[str, Any]]:
        if table not in TABLES or "id" in payload or "user_id" in payload:
            raise AppError(500, "Invalid local record update", "unsupported_record_query")
        where, values = _filters(table, params)
        target = sql.Identifier("trade", TABLES[table])
        output = []
        async with self.pool.connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
            await cursor.execute(
                sql.SQL("select id,status,data from {} where {} for update").format(target, where), values
            )
            rows = await cursor.fetchall()
            for record in rows:
                row = record["data"]
                if remove:
                    if table == "strategies" and record["status"] in {"active", "executing_entry", "executing_exit"}:
                        raise AppError(409, "Cannot delete live execution", "live_execution_delete")
                    if table == "strategies":
                        risk = row.get("risk_state") or {}
                        if (row.get("entry_execution_at") and not row.get("exit_execution_at")
                                and risk.get("exposureStatus") != "flat"):
                            raise AppError(409, "Unresolved exposure cannot be deleted", "live_exposure_delete")
                        await self._delete_strategy_children(connection, row["id"])
                    await cursor.execute(sql.SQL("delete from {} where id=%s").format(target), (record["id"],))
                    output.append(row)
                    continue
                updated = {**row, **payload, "updated_at": datetime.now(UTC).isoformat()}
                await self._save(connection, table, updated, existing=True)
                output.append(updated)
        return output

    async def _delete_strategy_children(self, connection: Any, strategy_id: str) -> None:
        executions = await connection.execute(
            "select id from trade.executions where relation_id=%s limit 1001", (strategy_id,)
        )
        ids = [item[0] for item in await executions.fetchall()]
        if len(ids) > 1000:
            raise AppError(409, "Execution deletion exceeds transaction capacity", "record_delete_too_large")
        for execution_id in ids:
            orders = await connection.execute(
                "select id from trade.execution_orders where relation_id=%s limit 1001", (execution_id,)
            )
            order_ids = [item[0] for item in await orders.fetchall()]
            if len(order_ids) > 1000:
                raise AppError(409, "Order deletion exceeds transaction capacity", "record_delete_too_large")
            await connection.execute("delete from trade.execution_orders where id=any(%s)", (order_ids,))
        await connection.execute("delete from trade.executions where id=any(%s)", (ids,))
        proposals = await connection.execute(
            "select data from trade.strategy_proposals where relation_id=%s for update", (strategy_id,)
        )
        for item in await proposals.fetchall():
            await self._save(connection, "strategy_proposals", {**item[0], "strategy_id": None}, existing=True)
        slots = await connection.execute(
            "select data from trade.strategy_capital_slots where relation_id=%s for update", (strategy_id,)
        )
        for item in await slots.fetchall():
            await self._save(connection, "strategy_capital_slots", {
                **item[0], "status": "available", "strategy_id": None, "proposal_id": None,
                "reserved_at": None, "released_at": datetime.now(UTC).isoformat(),
            }, existing=True)

    async def rpc(self, name: str, payload: dict[str, Any]) -> Any:
        operations = {
            "reserve_strategy_capital_slot": self._reserve_capital,
            "release_strategy_capital_slot": self._release_capital,
            "claim_automation_agent_run": self._claim_agent,
            "ensure_automation_fixed_runs": self._ensure_fixed_runs,
            "cancel_redundant_automation_followups": self._cancel_followups,
        }
        operation = operations.get(name)
        if operation is None:
            raise AppError(500, "Unsupported local control operation", "unsupported_record_query")
        return await operation(payload)

    async def _reserve_capital(self, args: dict[str, Any]) -> dict[str, Any]:
        user_id = str(args["p_user_id"])
        strategy_id = str(args["p_strategy_id"])
        maximum = int(args["p_maximum_slots"])
        budget = Decimal(str(args["p_budget"]))
        balance = Decimal(str(args["p_total_balance"]))
        if maximum < 1 or maximum > 100 or budget <= 0 or balance <= 0:
            raise AppError(422, "Invalid capital reservation", "capital_reservation_invalid")
        async with self.pool.connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
            await cursor.execute("select owner_id from trade.strategies where id=%s", (strategy_id,))
            strategy = await cursor.fetchone()
            if not strategy or strategy["owner_id"] != user_id:
                raise AppError(404, "Strategy unavailable", "strategy_not_found")
            await cursor.execute("select connection from trade.users where user_id=%s", (user_id,))
            user = await cursor.fetchone()
            connection_record = user["connection"] if user else None
            if not connection_record or connection_record.get("status") != "connected":
                raise AppError(409, "Delta account required", "delta_not_connected")
            account_id = str(connection_record["delta_user_id"])
            await cursor.execute("select pg_advisory_xact_lock(hashtextextended(%s, 41))", (account_id,))
            await cursor.execute("select user_id from trade.users where connection->>'delta_user_id'=%s", (account_id,))
            aliases = [row["user_id"] for row in await cursor.fetchall()]
            await cursor.execute(
                """select data from trade.strategy_capital_slots
                   where owner_id = any(%s) and status in ('reserved','active') for update""",
                (aliases,),
            )
            active = [row["data"] for row in await cursor.fetchall()]
            owned = [row for row in active if row["user_id"] == user_id]
            existing = next((row for row in owned if row.get("strategy_id") == strategy_id), None)
            if existing:
                return {"slot": existing["slot_number"], "created": False, "occupiedBefore": len(owned) - 1}
            reserved = sum((Decimal(str(row.get("reserved_budget") or "0")) for row in active), Decimal("0"))
            if reserved + budget > balance or len(owned) >= maximum:
                raise AppError(409, "Account capital is already reserved", "capital_slots_full")
            await cursor.execute(
                "select data from trade.strategy_capital_slots where owner_id=%s for update", (user_id,)
            )
            slots = {int(row["data"]["slot_number"]): row["data"] for row in await cursor.fetchall()}
            for number in range(1, maximum + 1):
                current = slots.get(number)
                if current and current["status"] != "available":
                    continue
                now = datetime.now(UTC).isoformat()
                row = {
                    "id": current["id"] if current else str(uuid4()),
                    "user_id": user_id,
                    "slot_number": number,
                    "strategy_id": strategy_id,
                    "proposal_id": None,
                    "status": "reserved",
                    "reserved_at": now,
                    "released_at": None,
                    "reserved_budget": str(budget),
                    "created_at": current.get("created_at", now) if current else now,
                }
                await self._save(connection, "strategy_capital_slots", row, existing=bool(current))
                return {"slot": number, "created": True, "occupiedBefore": len(owned)}
        raise AppError(409, "No eligible capital slot", "capital_slots_full")

    async def _release_capital(self, args: dict[str, Any]) -> bool:
        async with self.pool.connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
            await cursor.execute(
                """select data from trade.strategy_capital_slots
                   where owner_id=%s and relation_id=%s for update""",
                (args["p_user_id"], args["p_strategy_id"]),
            )
            for record in await cursor.fetchall():
                await self._save(
                    connection,
                    "strategy_capital_slots",
                    {
                        **record["data"],
                        "status": "available",
                        "strategy_id": None,
                        "proposal_id": None,
                        "reserved_at": None,
                        "released_at": datetime.now(UTC).isoformat(),
                    },
                    existing=True,
                )
        return True

    async def _claim_agent(self, args: dict[str, Any]) -> list[dict[str, Any]]:
        user_id = str(args["p_user_id"])
        async with self.pool.connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
            await cursor.execute("select pg_advisory_xact_lock(hashtextextended(%s, 42))", (user_id,))
            await cursor.execute(
                "select 1 from trade.analysis_jobs where owner_id=%s and status='running' limit 1", (user_id,)
            )
            if await cursor.fetchone():
                return []
            await cursor.execute(
                "select data from trade.analysis_jobs where id=%s and owner_id=%s and status='scheduled' for update",
                (args["p_run_id"], user_id),
            )
            record = await cursor.fetchone()
            if not record:
                return []
            row = {**record["data"], "status": "running", "started_at": datetime.now(UTC).isoformat(), "error": None}
            await self._save(connection, "automation_agent_runs", row, existing=True)
            return [row]

    async def _ensure_fixed_runs(self, args: dict[str, Any]) -> int:
        runs = args["p_runs"]
        if len(runs) > 1000:
            raise AppError(422, "Fixed-run batch too large", "fixed_run_batch_invalid")
        count = 0
        async with self.pool.connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
            for item in runs:
                await cursor.execute(
                    """select data from trade.analysis_jobs
                       where owner_id=%s and unique_key=%s for update""",
                    (item["user_id"], item["run_key"]),
                )
                current = await cursor.fetchone()
                if current and (
                    current["data"]["status"] != "cancelled" or _timestamp(item["scheduled_for"]) <= datetime.now(UTC)
                ):
                    continue
                now = datetime.now(UTC).isoformat()
                row = {
                    **(current["data"] if current else {}),
                    **item,
                    "id": current["data"]["id"] if current else str(uuid4()),
                    "created_at": current["data"].get("created_at", now) if current else now,
                    "updated_at": now,
                    "status": "scheduled",
                    "completed_at": None,
                    "error": None,
                    "outcome": None,
                    "parent_agent_run_id": None,
                    "signals_to_inspect": [],
                }
                await self._save(connection, "automation_agent_runs", row, existing=bool(current))
                count += 1
        return count

    async def _cancel_followups(self, args: dict[str, Any]) -> int:
        count = 0
        fixed = ["asia_session", "london_session", "new_york_session", "pre_expiry", "midnight_review"]
        async with self.pool.connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
            # Indexed per-owner check; cost grows with redundant follow-ups, not with every scheduled run.
            await cursor.execute(
                """select f.data from trade.analysis_jobs f
                   where f.status='scheduled' and f.data->>'trigger'='agent_follow_up'
                     and exists (
                       select 1 from trade.analysis_jobs x
                       where x.status='scheduled' and x.owner_id=f.owner_id
                         and x.data->>'trigger' = any(%s) and x.scheduled_for <= f.scheduled_for)
                   for update of f""",
                (fixed,),
            )
            for record in await cursor.fetchall():
                await self._save(
                    connection,
                    "automation_agent_runs",
                    {
                        **record["data"],
                        "status": "cancelled",
                        "completed_at": datetime.now(UTC).isoformat(),
                        "error": "A fixed session review is already scheduled first",
                    },
                    existing=True,
                )
                count += 1
        return count
