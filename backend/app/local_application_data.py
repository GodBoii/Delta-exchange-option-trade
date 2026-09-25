"""Local account, strategy-library and automation settings operations."""

import base64
import json
import re
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from .errors import AppError

DEFAULT_AUTOMATION = {
    "enabled": False,
    "model_id": "xiaomi/mimo-v2.6-pro",
    "minimum_follow_up_minutes": 5,
    "maximum_agent_runs_per_day": 3,
}
DEFAULT_CAPITAL = {"allocation_mode": "half_balance", "capital_amount": None}
VERSION_SUFFIX = re.compile(r"(?:\s*[-–—]?\s*[([]?v(?:ersion)?\s*\d+(?:\.\d+)*[)\]]?)+$", re.IGNORECASE)


def _library_row(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(record["id"]),
        "user_id": record["user_id"],
        "name": record["name"],
        "definitionJson": json.dumps(record["definition_json"], separators=(",", ":")),
        "source_run_id": record["source_run_id"],
        "version": record["version"],
        "enabled_for_ai": record["enabled_for_ai"],
        "deleted": record["deleted"],
        "created_at": record["created_at"].isoformat(),
        "updated_at": record["updated_at"].isoformat(),
    }


def _cursor(name: str, identifier: str) -> str:
    return base64.urlsafe_b64encode(json.dumps([name, identifier], separators=(",", ":")).encode()).decode()


def _uncursor(value: str | None) -> tuple[str, str]:
    if not value:
        return "", ""
    try:
        decoded = json.loads(base64.urlsafe_b64decode(value))
        if isinstance(decoded, list) and len(decoded) == 2 and all(isinstance(item, str) for item in decoded):
            return decoded[0], decoded[1]
    except (ValueError, TypeError):
        pass
    raise AppError(422, "Invalid library cursor", "library_cursor_invalid")


class LocalApplicationData:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self.pool = pool
        self.control: Any = None

    async def ensure_user(self, user_id: str) -> None:
        now = datetime.now(UTC).isoformat()
        record = {
            "userId": user_id,
            "capital": DEFAULT_CAPITAL,
            "automation": DEFAULT_AUTOMATION,
            "connection": None,
            "createdAt": now,
            "updatedAt": now,
        }
        async with self.pool.connection() as connection:
            await connection.execute(
                """insert into trade.users (user_id,connection,automation,capital,record)
                   values (%s,null,%s,%s,%s) on conflict do nothing""",
                (user_id, Jsonb(DEFAULT_AUTOMATION), Jsonb(DEFAULT_CAPITAL), Jsonb(record)),
            )

    async def request(self, path: str, args: dict[str, Any], *, mutation: bool = False) -> Any:
        dispatch = {
            "accounts:overview": self._account_overview,
            "accounts:credentials": self._credentials,
            "accounts:saveConnection": self._save_connection,
            "accounts:revoke": self._revoke,
            "accounts:executionGroupsForUsers": self._execution_groups,
            "accounts:updateOutboundIp": self._update_outbound_ip,
            "library:getCapital": self._get_capital,
            "library:setCapital": self._set_capital,
            "library:serverGet": self._library_get,
            "library:serverList": self._library_list,
            "settings:automationForUser": self._automation_for_user,
            "settings:automationPage": self._automation_page,
            "settings:saveAutomation": self._save_automation,
        }
        operation = dispatch.get(path)
        if operation is None:
            if self.control is not None:
                return await self.control.request(path, args)
            raise AppError(500, f"Unknown local application operation: {path}", "local_operation_unknown")
        return await operation(args)

    async def _user(self, user_id: str) -> dict[str, Any] | None:
        async with self.pool.connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
            await cursor.execute(
                "select user_id,connection,automation,capital,record from trade.users where user_id=%s", (user_id,)
            )
            return await cursor.fetchone()

    async def _account_overview(self, args: dict[str, Any]) -> dict[str, Any]:
        user = await self._user(args["userId"])
        row = user["connection"] if user else None
        if not row:
            return {"connection": None}
        return {
            "connection": {
                key: row.get(key)
                for key in (
                    "id",
                    "delta_user_id",
                    "account_name",
                    "email_masked",
                    "environment",
                    "status",
                )
            }
        }

    async def _credentials(self, args: dict[str, Any]) -> dict[str, Any] | None:
        user = await self._user(args["userId"])
        return user["connection"] if user else None

    async def _save_connection(self, args: dict[str, Any]) -> str:
        value = args["value"]
        user_id = value["user_id"]
        await self.ensure_user(user_id)
        async with self.pool.connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
            await cursor.execute("select connection,record from trade.users where user_id=%s for update", (user_id,))
            current = await cursor.fetchone()
            if (current["connection"] or {}).get("fingerprint") != args["expectedFingerprint"]:
                raise AppError(409, "Connection changed; retry", "connection_changed")
            record = {**current["record"], "connection": value, "updatedAt": datetime.now(UTC).isoformat()}
            await cursor.execute(
                "update trade.users set connection=%s,record=%s where user_id=%s",
                (Jsonb(value), Jsonb(record), user_id),
            )
        return str(value["id"])

    async def _revoke(self, args: dict[str, Any]) -> None:
        async with self.pool.connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
            await cursor.execute(
                "select connection,record from trade.users where user_id=%s for update", (args["userId"],)
            )
            current = await cursor.fetchone()
            if not current or not current["connection"] or current["connection"]["status"] == "revoked":
                return
            value = {
                **current["connection"],
                "status": "revoked",
                "ciphertext": None,
                "fingerprint": "revoked:" + current["connection"]["fingerprint"],
                "updated_at": datetime.now(UTC).isoformat(),
            }
            record = {**current["record"], "connection": value, "updatedAt": datetime.now(UTC).isoformat()}
            await cursor.execute(
                "update trade.users set connection=%s,record=%s where user_id=%s",
                (Jsonb(value), Jsonb(record), args["userId"]),
            )

    async def _execution_groups(self, args: dict[str, Any]) -> list[dict[str, str]]:
        user_ids = args["userIds"]
        if len(user_ids) > 100:
            raise AppError(422, "Account lookup batch too large", "account_batch_invalid")
        async with self.pool.connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
            await cursor.execute("select user_id,connection from trade.users where user_id=any(%s)", (user_ids,))
            users = {row["user_id"]: row["connection"] for row in await cursor.fetchall()}
        return [
            {"userId": user_id, "accountId": (users.get(user_id) or {}).get("delta_user_id") or user_id}
            for user_id in user_ids
        ]

    async def _update_outbound_ip(self, args: dict[str, Any]) -> None:
        async with self.pool.connection() as connection:
            result = await connection.execute(
                "update trade.system_settings set outbound_ip=%s,ip_checked_at=now() where key='main'",
                (args["ip"],),
            )
            if not result.rowcount:
                raise AppError(503, "System settings missing", "system_settings_missing")

    async def _get_capital(self, args: dict[str, Any]) -> dict[str, Any]:
        user = await self._user(args["userId"])
        capital = user["capital"] if user else DEFAULT_CAPITAL
        return {"user_id": args["userId"], **capital}

    async def _set_capital(self, args: dict[str, Any]) -> None:
        value = args["value"]
        user_id = value["user_id"]
        await self.ensure_user(user_id)
        async with self.pool.connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
            await cursor.execute("select record from trade.users where user_id=%s for update", (user_id,))
            current = await cursor.fetchone()
            record = {**current["record"], "capital": value, "updatedAt": datetime.now(UTC).isoformat()}
            await cursor.execute(
                "update trade.users set capital=%s,record=%s where user_id=%s",
                (Jsonb({key: value[key] for key in ("allocation_mode", "capital_amount")}), Jsonb(record), user_id),
            )

    async def _library_get(self, args: dict[str, Any]) -> dict[str, Any] | None:
        async with self.pool.connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
            await cursor.execute("select * from trade.saved_strategies where id=%s and not deleted", (args["id"],))
            row = await cursor.fetchone()
        if not row or (row["user_id"] is not None and row["user_id"] != args["userId"]):
            return None
        return _library_row(row)

    async def _library_list(self, args: dict[str, Any]) -> dict[str, Any]:
        opts = args["paginationOpts"]
        limit = max(1, min(int(opts["numItems"]), 100))
        after_name, after_id = _uncursor(opts.get("cursor"))
        owner = None if args["defaults"] else args["userId"]
        async with self.pool.connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
            await cursor.execute(
                """select * from trade.saved_strategies
                   where user_id is not distinct from %s and not deleted
                     and (name,id::text) > (%s,%s)
                   order by name,id limit %s""",
                (owner, after_name, after_id, limit + 1),
            )
            rows = await cursor.fetchall()
        page = rows[:limit]
        return {
            "page": [_library_row(row) for row in page],
            "isDone": len(rows) <= limit,
            "continueCursor": _cursor(page[-1]["name"], str(page[-1]["id"])) if len(rows) > limit else "",
        }

    async def _automation_for_user(self, args: dict[str, Any]) -> dict[str, Any] | None:
        if args["userId"] == "global":
            async with self.pool.connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
                await cursor.execute("select analysis from trade.system_settings where key='main'")
                row = await cursor.fetchone()
                return {"user_id": "global", **row["analysis"]} if row else None
        user = await self._user(args["userId"])
        return {"user_id": args["userId"], **user["automation"]} if user else None

    async def _automation_page(self, args: dict[str, Any]) -> dict[str, Any]:
        opts = args["paginationOpts"]
        limit = max(1, min(int(opts["numItems"]), 100))
        cursor = opts.get("cursor") or ""
        enabled = args.get("enabled")
        async with self.pool.connection() as connection, connection.cursor(row_factory=dict_row) as query:
            await query.execute(
                """select user_id,automation from trade.users
                   where user_id>%s and (%s::boolean is null or (automation->>'enabled')::boolean=%s)
                   order by user_id limit %s""",
                (cursor, enabled, enabled, limit + 1),
            )
            rows = await query.fetchall()
        page = rows[:limit]
        return {
            "page": [{"user_id": row["user_id"], **row["automation"]} for row in page],
            "isDone": len(rows) <= limit,
            "continueCursor": page[-1]["user_id"] if len(rows) > limit else "",
        }

    async def _save_automation(self, args: dict[str, Any]) -> dict[str, Any]:
        user_id = args["userId"]
        value = args["value"]
        if value["minimum_follow_up_minutes"] < 5 or value["maximum_agent_runs_per_day"] < 0:
            raise AppError(422, "Invalid automation limits", "automation_limits_invalid")
        if user_id == "global":
            async with self.pool.connection() as connection:
                result = await connection.execute(
                    "update trade.system_settings set analysis=%s where key='main'", (Jsonb(value),)
                )
                if not result.rowcount:
                    raise AppError(503, "System settings missing", "system_settings_missing")
        else:
            await self.ensure_user(user_id)
            async with self.pool.connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
                await cursor.execute("select record from trade.users where user_id=%s for update", (user_id,))
                current = await cursor.fetchone()
                record = {**current["record"], "automation": value, "updatedAt": datetime.now(UTC).isoformat()}
                await cursor.execute(
                    "update trade.users set automation=%s,record=%s where user_id=%s",
                    (Jsonb(value), Jsonb(record), user_id),
                )
        return {"user_id": user_id, **value}

    async def saved_strategies(self, user_id: str, strategy_id: str | None = None) -> list[dict[str, Any]]:
        if strategy_id is not None:
            row = await self._library_get({"userId": user_id, "id": strategy_id})
            return [self._legacy_library_row(row)] if row else []
        records = []
        for defaults in (False, True):
            cursor = None
            while True:
                page = await self._library_list(
                    {
                        "userId": user_id,
                        "defaults": defaults,
                        "paginationOpts": {"numItems": 100, "cursor": cursor},
                    }
                )
                records.extend(self._legacy_library_row(row) for row in page["page"])
                if page["isDone"]:
                    break
                cursor = page["continueCursor"]
        return records

    @staticmethod
    def _legacy_library_row(row: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in row.items() if key not in {"definitionJson", "deleted"}} | {
            "definition_json": json.loads(row["definitionJson"])
        }

    async def library_save(self, user_id: str, value: dict[str, Any]) -> dict[str, Any]:
        try:
            identifier = str(UUID(value["id"]))
            definition = json.loads(value["definitionJson"])
        except (ValueError, TypeError, KeyError) as error:
            raise AppError(422, "Invalid strategy definition", "strategy_definition_invalid") from error
        name = value["name"]
        if (
            not isinstance(name, str)
            or not 2 <= len(name) <= 80
            or VERSION_SUFFIX.sub("", name.strip()).strip() != name
            or not isinstance(definition, dict)
            or definition.get("name") != name
            or definition.get("enabledForAi") is not value["enabled"]
            or not isinstance(definition.get("legs"), list)
            or not 1 <= len(definition["legs"]) <= 12
            or len(value["definitionJson"]) > 262144
        ):
            raise AppError(422, "Invalid strategy definition", "strategy_definition_invalid")
        async with self.pool.connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
            await cursor.execute("select owner_user_id from trade.system_settings where key='main'")
            owner = await cursor.fetchone()
            is_owner = bool(owner and owner["owner_user_id"] == user_id)
            await cursor.execute("select * from trade.saved_strategies where id=%s for update", (identifier,))
            existing = await cursor.fetchone()
            now = datetime.now(UTC)
            if existing:
                if existing["deleted"] or (
                    existing["user_id"] != user_id and not (is_owner and existing["user_id"] is None)
                ):
                    raise AppError(404, "Strategy unavailable", "strategy_unavailable")
                if (
                    existing["name"] == name
                    and existing["definition_json"] == definition
                    and existing["enabled_for_ai"] == value["enabled"]
                ):
                    return _library_row(existing)
                if value["expectedVersion"] != existing["version"]:
                    raise AppError(409, "Strategy changed in another session", "strategy_version_conflict")
                await cursor.execute(
                    """update trade.saved_strategies
                       set name=%s,definition_json=%s,enabled_for_ai=%s,version=version+1,updated_at=%s
                       where id=%s returning *""",
                    (name, Jsonb(definition), value["enabled"], now, identifier),
                )
            else:
                if value["expectedVersion"] is not None:
                    raise AppError(409, "Strategy no longer exists", "strategy_version_conflict")
                await cursor.execute(
                    """insert into trade.saved_strategies
                       (id,user_id,name,definition_json,enabled_for_ai,version,source_run_id,created_at,updated_at)
                       values (%s,%s,%s,%s,%s,1,null,%s,%s) returning *""",
                    (identifier, None if is_owner else user_id, name, Jsonb(definition), value["enabled"], now, now),
                )
            saved = await cursor.fetchone()
        return _library_row(saved)

    async def library_remove(self, user_id: str, identifier: str, expected_version: int) -> None:
        async with self.pool.connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
            await cursor.execute("select owner_user_id from trade.system_settings where key='main'")
            owner = await cursor.fetchone()
            is_owner = bool(owner and owner["owner_user_id"] == user_id)
            await cursor.execute("select * from trade.saved_strategies where id=%s for update", (identifier,))
            existing = await cursor.fetchone()
            if not existing or (existing["user_id"] != user_id and not (is_owner and existing["user_id"] is None)):
                raise AppError(404, "Strategy unavailable", "strategy_unavailable")
            if existing["deleted"]:
                return
            if existing["version"] != expected_version:
                raise AppError(409, "Strategy changed in another session", "strategy_version_conflict")
            await cursor.execute(
                """update trade.saved_strategies set deleted=true,enabled_for_ai=false,updated_at=now()
                   where id=%s""",
                (identifier,),
            )
