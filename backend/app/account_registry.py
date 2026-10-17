"""Owned Delta subaccounts and immutable strategy account assignments."""

import json
from decimal import Decimal
from typing import Any

from cryptography.fernet import InvalidToken
from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .credential_store import account_store
from .database import Database
from .errors import AppError

LIVE_STATES = ("executing_entry", "active", "executing_exit", "attention")


class AccountRegistry:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.pool = db.pool

    async def main(self, owner: str) -> dict[str, Any]:
        row = await self.db.local_data.request("accounts:credentials", {"userId": owner})
        if not row or row.get("status") != "connected":
            raise AppError(409, "Connect your main Delta account first", "delta_not_connected")
        return row

    async def settings(self, owner: str) -> dict[str, Any]:
        async with self.pool.connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
            await cursor.execute(
                "select mode,auto_fund,revision from trade.account_management where owner_id=%s", (owner,)
            )
            row = await cursor.fetchone()
        return row or {"mode": "main", "auto_fund": False, "revision": 0}

    async def save_settings(self, owner: str, mode: str, auto_fund: bool, revision: int) -> None:
        main = await self.main(owner)
        async with self.pool.connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
            await cursor.execute("select pg_advisory_xact_lock(hashtextextended(%s,52))", (main["delta_user_id"],))
            await cursor.execute("select revision from trade.account_management where owner_id=%s for update", (owner,))
            row = await cursor.fetchone()
            if (row["revision"] if row else 0) != revision:
                raise AppError(409, "Account settings changed. Reload and try again", "account_settings_changed")
            if mode != "main":
                await cursor.execute(
                    """select 1 from trade.exchange_accounts where owner_id=%s and parent_id=%s
                       and present and credential is not null and participation <> 'disabled' limit 1""",
                    (owner, main["delta_user_id"]),
                )
                if not await cursor.fetchone():
                    raise AppError(409, "Connect and enable a subaccount first", "subaccount_not_ready")
            await cursor.execute(
                """insert into trade.account_management(owner_id,mode,auto_fund) values (%s,%s,%s)
                   on conflict(owner_id) do update set mode=excluded.mode,auto_fund=excluded.auto_fund,
                   revision=trade.account_management.revision+1,updated_at=now()""",
                (owner, mode, auto_fund),
            )

    async def accounts(self, owner: str) -> list[dict[str, Any]]:
        async with self.pool.connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
            await cursor.execute(
                """select a.account_id,a.parent_id,a.account_name,a.margin_mode,a.present,a.participation,
                          a.credential is not null as connected,a.checked_at,
                          x.strategy_id is not null as busy,
                          case when x.owner_id=a.owner_id then x.strategy_id end as strategy_id,
                          case when x.owner_id=a.owner_id then x.budget end as budget,
                          case when x.owner_id=a.owner_id then s.data->>'name' end as strategy_name
                   from trade.exchange_accounts a
                   left join trade.account_assignments x on x.account_id=a.account_id and x.state<>'released'
                   left join trade.strategies s on s.id=x.strategy_id
                   where a.owner_id=%s order by a.account_name,a.account_id""",
                (owner,),
            )
            rows = await cursor.fetchall()
        return rows

    async def row(self, owner: str, account: str) -> dict[str, Any]:
        async with self.pool.connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
            await cursor.execute(
                "select * from trade.exchange_accounts where owner_id=%s and account_id=%s", (owner, account)
            )
            row = await cursor.fetchone()
        if row is None:
            raise AppError(404, "Subaccount not found", "subaccount_not_found")
        return row

    async def sync(self, owner: str, response: dict[str, Any]) -> None:
        main = await self.main(owner)
        parent = str(main["delta_user_id"])
        values = response.get("result")
        if not isinstance(values, list) or any(
            not isinstance(value, dict) or not str(value.get("id") or "").isdigit() for value in values
        ):
            raise AppError(502, "Delta returned an invalid subaccount list", "subaccounts_unknown")
        children = [value for value in values if str(value["id"]) != parent and value.get("is_sub_account") is True]
        if any(str(value["id"]) != parent and value.get("is_sub_account") is not True for value in values):
            raise AppError(502, "Delta account relationships could not be verified", "subaccounts_unknown")
        async with self.pool.connection() as connection:
            await connection.execute("select pg_advisory_xact_lock(hashtextextended(%s,52))", (parent,))
            await connection.execute("update trade.exchange_accounts set present=false where owner_id=%s", (owner,))
            for child in children:
                await connection.execute(
                    """insert into trade.exchange_accounts(owner_id,account_id,parent_id,account_name,margin_mode)
                       values (%s,%s,%s,%s,%s) on conflict(owner_id,account_id) do update
                       set account_name=excluded.account_name,margin_mode=excluded.margin_mode,present=true
                       where trade.exchange_accounts.parent_id=excluded.parent_id""",
                    (
                        owner,
                        str(child["id"]),
                        parent,
                        str(child.get("account_name") or child["id"])[:120],
                        str(child.get("margin_mode") or "unknown")[:40],
                    ),
                )

    async def save_key(self, owner: str, account: str, key: str, secret: str) -> None:
        main = await self.main(owner)
        child = await self.row(owner, account)
        if not child["present"] or child["parent_id"] != str(main["delta_user_id"]):
            raise AppError(409, "Refresh subaccounts from the main account before connecting", "subaccount_not_ready")
        from .delta import DeltaClient

        client = DeltaClient(self.db.settings, key.strip(), secret.strip())
        try:
            profile = (await client.profile()).get("result")
            if not isinstance(profile, dict) or str(profile.get("id")) != account:
                raise AppError(409, "This API key belongs to another Delta account", "subaccount_key_mismatch")
            # Non-mutating probes. A profile response alone does not establish trading API access.
            for response in (await client.balances(), await client.positions(), await client.open_orders()):
                if not isinstance(response.get("result"), list):
                    raise AppError(502, "Delta account access could not be verified", "subaccount_access_unknown")
        finally:
            await client.close()
        credential = account_store(self.db).seal(owner, key.strip(), secret.strip(), {"id": account})
        async with self.pool.connection() as connection:
            await connection.execute(
                """update trade.exchange_accounts set credential=%s,checked_at=now()
                   where owner_id=%s and account_id=%s and parent_id=%s and present""",
                (Jsonb(credential), owner, account, main["delta_user_id"]),
            )

    async def credentials(self, owner: str, account: str) -> dict[str, str]:
        child = await self.row(owner, account)
        record = child["credential"]
        if not record:
            raise AppError(409, "Connect this subaccount's API key", "subaccount_not_connected")
        try:
            value = json.loads(account_store(self.db).cipher.decrypt(record["ciphertext"].encode()))
        except (InvalidToken, ValueError, TypeError, KeyError) as error:
            raise AppError(503, "Subaccount credentials are unavailable", "credentials_unavailable") from error
        if (
            not isinstance(value, dict)
            or value.get("user_id") != owner
            or value.get("delta_user_id") != account
            or value.get("environment") != "production"
            or not value.get("api_key")
            or not value.get("api_secret")
        ):
            raise AppError(503, "Subaccount credential identity mismatch", "credentials_unavailable")
        return {key: str(value[key]) for key in ("api_key", "api_secret", "delta_user_id")}

    async def set_participation(self, owner: str, account: str, participation: str) -> None:
        await self.row(owner, account)
        async with self.pool.connection() as connection:
            await connection.execute("select pg_advisory_xact_lock(hashtextextended(%s,53))", (account,))
            await connection.execute(
                "update trade.exchange_accounts set participation=%s where owner_id=%s and account_id=%s",
                (participation, owner, account),
            )

    async def remove_key(self, owner: str, account: str) -> None:
        await self.row(owner, account)
        async with self.pool.connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
            await cursor.execute("select pg_advisory_xact_lock(hashtextextended(%s,53))", (account,))
            await cursor.execute(
                "select 1 from trade.account_assignments where account_id=%s and state<>'released' limit 1", (account,)
            )
            if await cursor.fetchone():
                raise AppError(409, "Release the subaccount's run before removing its key", "subaccount_busy")
            await cursor.execute(
                """select 1 from trade.account_transfers where (source_id=%s or target_id=%s)
                   and state in ('prepared','submitting','unknown') limit 1""", (account, account),
            )
            if await cursor.fetchone():
                raise AppError(409, "Resolve pending transfers before removing the key", "transfer_pending")
            await cursor.execute(
                """update trade.exchange_accounts set credential=null,participation='disabled',checked_at=null
                   where owner_id=%s and account_id=%s""",
                (owner, account),
            )

    async def assignment(self, owner: str, strategy: str) -> dict[str, Any] | None:
        async with self.pool.connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
            await cursor.execute(
                "select * from trade.account_assignments where strategy_id=%s and owner_id=%s", (strategy, owner)
            )
            return await cursor.fetchone()

    async def reserve(self, owner: str, strategy: str, account: str) -> dict[str, Any]:
        child = await self.row(owner, account)
        parent = str((await self.main(owner))["delta_user_id"])
        try:
            async with self.pool.connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
                await cursor.execute("select pg_advisory_xact_lock(hashtextextended(%s,53))", (account,))
                await cursor.execute(
                    "select * from trade.exchange_accounts where owner_id=%s and account_id=%s for update",
                    (owner, account),
                )
                child = await cursor.fetchone()
                await cursor.execute(
                    "select data,status from trade.strategies where id=%s and owner_id=%s for update", (strategy, owner)
                )
                run = await cursor.fetchone()
                if not run or run["status"] not in {"draft", "scheduled"}:
                    raise AppError(409, "Only an unentered run can be assigned", "subaccount_assignment_blocked")
                await cursor.execute("select * from trade.account_assignments where strategy_id=%s", (strategy,))
                existing = await cursor.fetchone()
                if existing:
                    if existing["account_id"] != account or existing["state"] == "released":
                        raise AppError(
                            409, "This run already has a permanent account assignment", "subaccount_assignment_blocked"
                        )
                    return existing
                await cursor.execute(
                    """select 1 from trade.account_transfers where parent_id=%s
                       and state in ('prepared','submitting','unknown') limit 1""",
                    (parent,),
                )
                if await cursor.fetchone():
                    raise AppError(409, "Resolve the pending transfer before reserving an account", "transfer_pending")
                if (
                    not child["present"]
                    or not child["credential"]
                    or child["participation"] == "disabled"
                    or child["parent_id"] != parent
                    or run["data"].get("entry_execution_at")
                ):
                    raise AppError(409, "The subaccount is unavailable for new entries", "subaccount_not_ready")
                await cursor.execute(
                    """insert into trade.account_assignments(strategy_id,owner_id,account_id,parent_id)
                       values (%s,%s,%s,%s) returning *""",
                    (strategy, owner, account, parent),
                )
                assigned = await cursor.fetchone()
                await cursor.execute(
                    """update trade.strategies set data=data || %s,revision=revision+1 where id=%s""",
                    (Jsonb({"exchange_account_id": account, "exchange_account_name": child["account_name"]}), strategy),
                )
                return assigned
        except UniqueViolation as error:
            raise AppError(409, "The subaccount was reserved by another run", "subaccount_busy") from error

    async def set_budget(self, owner: str, strategy: str, budget: Decimal, total: Decimal) -> None:
        async with self.pool.connection() as connection:
            await connection.execute(
                """update trade.account_assignments set budget=%s,pool_total=%s
                   where owner_id=%s and strategy_id=%s and budget is null""",
                (budget, total, owner, strategy),
            )

    async def mark(self, owner: str, strategy: str, state: str) -> None:
        async with self.pool.connection() as connection:
            await connection.execute(
                """update trade.account_assignments set state=%s,
                   released_at=case when %s='released' then now() else null end where owner_id=%s and strategy_id=%s""",
                (state, state, owner, strategy),
            )
