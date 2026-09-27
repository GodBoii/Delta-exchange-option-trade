"""Owner reporting copies of software runs and capital observations.

The trading writer calls :meth:`OwnerLedger.capture` inside the transaction that
changes a run, so the copy commits or rolls back with the source row. Deleting a
run captures a final copy marked ``deleted_by_user_at`` before the source rows go.
Nothing on the trading path reads these tables.
"""

import base64
import json
import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from .errors import AppError
from .run_accounting import ACCOUNTING_STATES, activity_time, classify_run, run_detail_payload
from .settlement import optional_decimal

logger = logging.getLogger(__name__)
# Raise when the capture logic changes in a way that needs every row rebuilt.
LEDGER_VERSION = 1
CAPTURE_LOCK_SPACE = 47
LIVE_WALLET_INTERVAL = "15 minutes"
DeletedFilter = Literal["include", "exclude", "only"]
LIST_COLUMNS = """run_id,strategy_name,asset,status,accounting_state,exclusion_reason,created_at,
    entry_at,exit_at,entry_executed_at,exit_executed_at,activity_at,realized_pnl,gross_pnl,exchange_fees,
    capital_budget,wallet_total_at_entry,wallet_available_at_entry,deleted_by_user_at,last_captured_at"""


def _time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else None
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else None


def _text(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    return value


def _row(record: dict[str, Any]) -> dict[str, Any]:
    return {key: _text(value) for key, value in record.items()}


def encode_cursor(activity_at: datetime, run_id: str) -> str:
    raw = json.dumps([activity_at.astimezone(UTC).isoformat(), run_id], separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode()


def decode_cursor(value: str | None) -> tuple[datetime, str] | None:
    if not value:
        return None
    try:
        decoded = json.loads(base64.urlsafe_b64decode(value.encode()))
        moment = _time(decoded[0]) if isinstance(decoded, list) and len(decoded) == 2 else None
        if moment is not None and isinstance(decoded[1], str):
            return moment, decoded[1]
    except (ValueError, TypeError):
        pass
    raise AppError(422, "Invalid page cursor", "page_cursor_invalid")


async def capture_quietly(ledger: "OwnerLedger", connection: Any, run_id: str) -> None:
    """Refresh the copy inside a savepoint; a reporting failure never blocks a trading write."""
    try:
        async with connection.transaction():
            await ledger.capture(connection, run_id)
    except Exception:
        logger.exception("Owner ledger capture failed run_id=%s", run_id)


async def record_policy(connection: Any, user_id: str, mode: str, amount: Any) -> None:
    """Record a capital-policy change in the caller's transaction, isolated by a savepoint."""
    now = datetime.now(UTC)
    try:
        async with connection.transaction():
            await connection.execute(
                "insert into owner_reporting.user_profiles (user_id) values (%s) on conflict do nothing", (user_id,)
            )
            await connection.execute(
                """insert into owner_reporting.capital_observations
                   (user_id,kind,source,observation_key,observed_at,allocation_mode,capital_amount)
                   values (%s,'policy','capital_policy',%s,%s,%s,%s) on conflict do nothing""",
                (user_id, f"policy:{now.isoformat()}", now, mode, optional_decimal(amount)),
            )
    except Exception:
        logger.exception("Capital policy observation failed user_id=%s", user_id)


class OwnerLedger:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self.pool = pool

    async def capture(self, connection: Any, run_id: str, *, deleted: bool = False) -> bool:
        """Rebuild the reporting copy of one run from its current source rows."""
        # Serialise captures of one run so a later transaction never writes an older view.
        await connection.execute(
            "select pg_advisory_xact_lock(hashtextextended(%s, %s))", (run_id, CAPTURE_LOCK_SPACE)
        )
        found = await (
            await connection.execute("select owner_id,data from trade.strategies where id=%s", (run_id,))
        ).fetchone()
        if not found:
            return False
        owner, row = found
        executions = [
            item[0]
            for item in await (
                await connection.execute(
                    """select data from trade.executions where relation_id=%s
                       order by started_at nulls last, created_at, id""",
                    (run_id,),
                )
            ).fetchall()
        ]
        kinds = {str(item["id"]): str(item.get("kind") or "entry") for item in executions}
        orders: list[dict[str, Any]] = []
        if kinds:
            records = await (
                await connection.execute(
                    """select data from trade.execution_orders where relation_id = any(%s)
                       order by created_at, id""",
                    (list(kinds),),
                )
            ).fetchall()
            orders = [{**item[0], "kind": kinds.get(str(item[0].get("execution_id")), "entry")} for item in records]
        accounting = classify_run(str(row.get("status") or ""), row.get("result_json") or {}, orders)
        detail = run_detail_payload(row, executions, orders, accounting.settlement, include_raw=False)
        detail.update(
            {
                "asset": row.get("asset"),
                "sharedDecisionId": row.get("shared_decision_id"),
                "accountingState": accounting.state,
                "exclusionReason": accounting.reason,
            }
        )
        policy = row.get("capital_policy_json") or {}
        created = _time(row.get("created_at")) or datetime.now(UTC)
        entry_executed = _time(row.get("entry_execution_at"))
        budget = optional_decimal(row.get("capital_budget"))
        wallet_total = optional_decimal(policy.get("totalBalanceAtEntry"))
        wallet_available = optional_decimal(policy.get("availableBalanceAtEntry"))
        await connection.execute(
            "insert into owner_reporting.user_profiles (user_id) values (%s) on conflict do nothing", (owner,)
        )
        await connection.execute(
            """insert into owner_reporting.trade_ledger (
                   run_id,owner_user_id,strategy_name,asset,status,accounting_state,exclusion_reason,
                   created_at,entry_at,exit_at,entry_executed_at,exit_executed_at,activity_at,
                   realized_pnl,gross_pnl,exchange_fees,entry_premium,exit_premium,capital_budget,
                   wallet_total_at_entry,wallet_available_at_entry,order_count,detail,deleted_by_user_at)
               values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                       case when %s then now() end)
               on conflict (run_id) do update set
                   strategy_name=excluded.strategy_name, asset=excluded.asset, status=excluded.status,
                   accounting_state=excluded.accounting_state, exclusion_reason=excluded.exclusion_reason,
                   created_at=excluded.created_at, entry_at=excluded.entry_at, exit_at=excluded.exit_at,
                   entry_executed_at=excluded.entry_executed_at, exit_executed_at=excluded.exit_executed_at,
                   activity_at=excluded.activity_at, realized_pnl=excluded.realized_pnl,
                   gross_pnl=excluded.gross_pnl, exchange_fees=excluded.exchange_fees,
                   entry_premium=excluded.entry_premium, exit_premium=excluded.exit_premium,
                   capital_budget=excluded.capital_budget,
                   wallet_total_at_entry=excluded.wallet_total_at_entry,
                   wallet_available_at_entry=excluded.wallet_available_at_entry,
                   order_count=excluded.order_count, detail=excluded.detail, last_captured_at=now(),
                   deleted_by_user_at=coalesce(owner_reporting.trade_ledger.deleted_by_user_at,
                                               excluded.deleted_by_user_at)""",
            (
                run_id,
                owner,
                str(row.get("name") or "Unnamed run"),
                row.get("asset"),
                str(row.get("status") or ""),
                accounting.state,
                accounting.reason,
                created,
                _time(row.get("entry_at")),
                _time(row.get("exit_at")),
                entry_executed,
                _time(row.get("exit_execution_at")),
                _time(activity_time(row)) or created,
                accounting.realized_pnl,
                accounting.gross_pnl,
                accounting.exchange_fees,
                accounting.entry_premium,
                accounting.exit_premium,
                budget,
                wallet_total,
                wallet_available,
                len(orders),
                Jsonb(detail),
                deleted,
            ),
        )
        if entry_executed is not None:
            await self._entry_observations(connection, owner, run_id, entry_executed, budget, policy)
        return True

    @staticmethod
    async def _entry_observations(
        connection: Any,
        owner: str,
        run_id: str,
        observed_at: datetime,
        budget: Decimal | None,
        policy: dict[str, Any],
    ) -> None:
        """Capital values the software recorded when the run entered. Missing values stay missing."""
        key = f"run:{run_id}"
        total = optional_decimal(policy.get("totalBalanceAtEntry"))
        values = []
        if budget is not None:
            values.append(("run_allocation", None, None, None, None, budget))
        if total is not None:
            values.append(("wallet", total, optional_decimal(policy.get("availableBalanceAtEntry")), None, None, None))
        if policy.get("allocationMode"):
            values.append(
                ("policy", None, None, str(policy["allocationMode"]), optional_decimal(policy.get("capitalAmount")),
                 None)
            )
        for kind, total_balance, available, mode, amount, allocated in values:
            await connection.execute(
                """insert into owner_reporting.capital_observations
                   (user_id,kind,source,observation_key,observed_at,run_id,total_balance,available_balance,
                    allocation_mode,capital_amount,allocated_budget)
                   values (%s,%s,'strategy_entry',%s,%s,%s,%s,%s,%s,%s,%s) on conflict do nothing""",
                (owner, kind, key, observed_at, run_id, total_balance, available, mode, amount, allocated),
            )

    async def backfill(self, *, batch_size: int = 200) -> dict[str, Any]:
        """Idempotently copy every existing run, then verify counts against the source tables."""
        started = datetime.now(UTC)
        seen = captured = 0
        after = ""
        while True:
            async with self.pool.connection() as connection:
                ids = [
                    item[0]
                    for item in await (
                        await connection.execute(
                            "select id from trade.strategies where id > %s order by id limit %s", (after, batch_size)
                        )
                    ).fetchall()
                ]
            if not ids:
                break
            for run_id in ids:
                async with self.pool.connection() as connection:
                    captured += int(await self.capture(connection, run_id))
            seen += len(ids)
            after = ids[-1]
        report = await self.verify()
        verified = report["missing"] == 0 and report["mismatched"] == 0
        async with self.pool.connection() as connection:
            await connection.execute(
                """insert into owner_reporting.backfill_runs
                   (ledger_version,started_at,completed_at,strategies_seen,captured,missing,mismatched,verified)
                   values (%s,%s,now(),%s,%s,%s,%s,%s)""",
                (LEDGER_VERSION, started, seen, captured, report["missing"], report["mismatched"], verified),
            )
        logger.info(
            "Owner ledger backfill seen=%d captured=%d missing=%d mismatched=%d verified=%s",
            seen, captured, report["missing"], report["mismatched"], verified,
        )
        return {**report, "seen": seen, "captured": captured, "verified": verified}

    async def verify(self) -> dict[str, Any]:
        """Every source run has a live copy with the same owner, status and order count."""
        async with self.pool.connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
            await cursor.execute(
                """select
                     (select count(*) from trade.strategies s where not exists
                        (select 1 from owner_reporting.trade_ledger l where l.run_id = s.id)) as missing,
                     (select count(*) from trade.strategies s
                        join owner_reporting.trade_ledger l on l.run_id = s.id
                       where l.owner_user_id <> s.owner_id or l.status <> s.status
                          or l.deleted_by_user_at is not null
                          or l.order_count <> (select count(*) from trade.execution_orders o
                                                 join trade.executions e on o.relation_id = e.id
                                                where e.relation_id = s.id)) as mismatched,
                     (select count(*) from trade.strategies) as source_runs,
                     (select count(*) from owner_reporting.trade_ledger) as ledger_runs"""
            )
            totals = await cursor.fetchone()
            await cursor.execute(
                """select coalesce(s.owner_id, l.owner_user_id) as user_id,
                          count(s.id) as source_runs, count(l.run_id) as ledger_runs
                     from trade.strategies s
                     full join owner_reporting.trade_ledger l on l.run_id = s.id
                    group by 1 order by 1"""
            )
            users = await cursor.fetchall()
        return {**totals, "users": users}

    async def history_status(self) -> dict[str, Any]:
        async with self.pool.connection() as connection:
            found = await (
                await connection.execute(
                    """select completed_at from owner_reporting.backfill_runs
                       where ledger_version=%s and verified order by completed_at desc limit 1""",
                    (LEDGER_VERSION,),
                )
            ).fetchone()
        return {"complete": found is not None, "verifiedAt": _text(found[0]) if found else None}

    async def ensure_backfilled(self) -> None:
        if not (await self.history_status())["complete"]:
            await self.backfill()

    async def summary(
        self, user_id: str, *, deleted: DeletedFilter, since: datetime | None
    ) -> dict[str, Any]:
        """Totals over ``settled`` runs plus a count of every other accounting state."""
        state_counts = ",".join(
            f"count(*) filter (where accounting_state='{state}') as {state}" for state in ACCOUNTING_STATES
        )
        async with self.pool.connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
            await cursor.execute(
                f"""select count(*) as total, {state_counts},
                      coalesce(sum(realized_pnl) filter (where accounting_state='settled'), 0) as net_realized,
                      coalesce(sum(realized_pnl) filter (where accounting_state='settled' and realized_pnl > 0), 0)
                        as gains,
                      coalesce(sum(realized_pnl) filter (where accounting_state='settled' and realized_pnl < 0), 0)
                        as losses,
                      coalesce(sum(exchange_fees) filter (where accounting_state='settled'), 0) as fees,
                      count(*) filter (where accounting_state='settled' and realized_pnl > 0) as wins,
                      count(*) filter (where accounting_state='settled' and realized_pnl < 0) as losing,
                      count(*) filter (where accounting_state='settled' and realized_pnl = 0) as break_even,
                      count(*) filter (where deleted_by_user_at is not null) as deleted,
                      max(last_captured_at) as captured_at
                    from owner_reporting.trade_ledger
                   where owner_user_id=%s
                     and (%s = 'include' or (%s = 'exclude') = (deleted_by_user_at is null))
                     and (%s::timestamptz is null or activity_at >= %s)""",
                (user_id, deleted, deleted, since, since),
            )
            totals = await cursor.fetchone()
        settled = int(totals["settled"])
        return {
            "netRealizedPnl": _text(totals["net_realized"]),
            "grossGains": _text(totals["gains"]),
            "grossLosses": _text(totals["losses"]),
            "exchangeFees": _text(totals["fees"]),
            "wins": totals["wins"],
            "losses": totals["losing"],
            "breakEven": totals["break_even"],
            "winRate": round(totals["wins"] / settled, 4) if settled else None,
            "settledRuns": settled,
            "totalRuns": totals["total"],
            "excludedRuns": int(totals["total"]) - settled,
            "states": {state: totals[state] for state in ACCOUNTING_STATES},
            "deletedByUserRuns": totals["deleted"],
            "lastCapturedAt": _text(totals["captured_at"]),
        }

    async def trades(
        self,
        user_id: str,
        *,
        deleted: DeletedFilter,
        since: datetime | None,
        state: str | None,
        cursor: str | None,
        limit: int,
    ) -> dict[str, Any]:
        after = decode_cursor(cursor)
        after_time, after_id = after if after else (None, None)
        async with self.pool.connection() as connection, connection.cursor(row_factory=dict_row) as query:
            await query.execute(
                f"""select {LIST_COLUMNS} from owner_reporting.trade_ledger
                   where owner_user_id=%s
                     and (%s = 'include' or (%s = 'exclude') = (deleted_by_user_at is null))
                     and (%s::timestamptz is null or activity_at >= %s)
                     and (%s::text is null or accounting_state = %s)
                     and (%s::timestamptz is null or (activity_at, run_id) < (%s, %s))
                   order by activity_at desc, run_id desc limit %s""",
                (user_id, deleted, deleted, since, since, state, state, after_time, after_time, after_id, limit + 1),
            )
            rows = await query.fetchall()
        page = rows[:limit]
        return {
            "items": [_row(item) for item in page],
            "nextCursor": encode_cursor(page[-1]["activity_at"], page[-1]["run_id"]) if len(rows) > limit else None,
        }

    async def trade(self, user_id: str, run_id: str) -> dict[str, Any] | None:
        async with self.pool.connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
            await cursor.execute(
                f"""select {LIST_COLUMNS},detail,first_captured_at from owner_reporting.trade_ledger
                   where owner_user_id=%s and run_id=%s""",
                (user_id, run_id),
            )
            found = await cursor.fetchone()
        return _row(found) if found else None

    async def record_wallet(self, user_id: str, total: Decimal, available: Decimal, observed_at: datetime) -> None:
        """Keep at most one live wallet observation per user every 15 minutes."""
        async with self.pool.connection() as connection:
            await connection.execute(
                "insert into owner_reporting.user_profiles (user_id) values (%s) on conflict do nothing", (user_id,)
            )
            await connection.execute(
                f"""insert into owner_reporting.capital_observations
                   (user_id,kind,source,observation_key,observed_at,total_balance,available_balance)
                   select %s,'wallet','live_wallet',%s,%s,%s,%s
                   where not exists (
                     select 1 from owner_reporting.capital_observations
                      where user_id=%s and kind='wallet' and source='live_wallet'
                        and observed_at > %s - interval '{LIVE_WALLET_INTERVAL}')
                   on conflict do nothing""",
                (user_id, f"live:{observed_at.isoformat()}", observed_at, total, available, user_id, observed_at),
            )

    async def capital_history(self, user_id: str, limit: int = 30) -> list[dict[str, Any]]:
        async with self.pool.connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
            await cursor.execute(
                """select kind,source,observed_at,run_id,total_balance,available_balance,allocation_mode,
                          capital_amount,allocated_budget
                     from owner_reporting.capital_observations
                    where user_id=%s order by observed_at desc, id desc limit %s""",
                (user_id, limit),
            )
            return [_row(item) for item in await cursor.fetchall()]

    async def latest_wallets(self, user_ids: list[str]) -> dict[str, dict[str, Any]]:
        async with self.pool.connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
            await cursor.execute(
                """select distinct on (user_id) user_id,source,observed_at,total_balance,available_balance
                     from owner_reporting.capital_observations
                    where user_id = any(%s) and kind='wallet'
                    order by user_id, observed_at desc, id desc""",
                (user_ids,),
            )
            return {item["user_id"]: _row(item) for item in await cursor.fetchall()}

    async def rollups(self, user_ids: list[str]) -> dict[str, dict[str, Any]]:
        """All-time owner-scope figures for a page of users, including runs they deleted."""
        async with self.pool.connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
            await cursor.execute(
                """select owner_user_id as user_id, count(*) as total_runs,
                          count(*) filter (where accounting_state='settled') as settled_runs,
                          coalesce(sum(realized_pnl) filter (where accounting_state='settled'), 0) as net_realized,
                          count(*) filter (where accounting_state='attention' and deleted_by_user_at is null)
                            as attention_runs,
                          count(*) filter (where accounting_state='open') as open_runs,
                          max(activity_at) as last_activity_at
                     from owner_reporting.trade_ledger
                    where owner_user_id = any(%s) group by owner_user_id""",
                (user_ids,),
            )
            return {item["user_id"]: _row(item) for item in await cursor.fetchall()}

    async def accounts_with_attention(self) -> int:
        async with self.pool.connection() as connection:
            found = await (
                await connection.execute(
                    """select count(distinct owner_user_id) from owner_reporting.trade_ledger
                        where accounting_state='attention' and deleted_by_user_at is null"""
                )
            ).fetchone()
        return int(found[0])

    async def sync_profiles(self, profiles: list[dict[str, Any]], accounts: dict[str, dict[str, Any]]) -> None:
        """Refresh the allowed profile fields for a page of registered users."""
        async with self.pool.connection() as connection:
            for profile in profiles:
                account = accounts.get(profile["id"]) or {}
                await connection.execute(
                    """insert into owner_reporting.user_profiles
                       (user_id,display_name,email,phone_number,avatar_url,user_type,registered_at,
                        connection_status,delta_account_name,profile_synced_at)
                       values (%s,%s,%s,%s,%s,%s,%s,%s,%s,now())
                       on conflict (user_id) do update set
                         display_name=excluded.display_name, email=excluded.email,
                         phone_number=excluded.phone_number, avatar_url=excluded.avatar_url,
                         user_type=excluded.user_type, registered_at=excluded.registered_at,
                         connection_status=excluded.connection_status,
                         delta_account_name=excluded.delta_account_name, profile_synced_at=now()""",
                    (
                        profile["id"],
                        profile.get("display_name"),
                        profile.get("email"),
                        profile.get("phone_number"),
                        profile.get("avatar_url"),
                        profile.get("user_type") if profile.get("user_type") in {"owner", "user"} else None,
                        _time(profile.get("created_at")),
                        account.get("connectionStatus"),
                        account.get("accountName"),
                    ),
                )
