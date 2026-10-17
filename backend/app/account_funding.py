"""Journal transfers before sending them; never replay an uncertain mutation."""

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any
from uuid import UUID

from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row

from .account_registry import AccountRegistry
from .errors import AppError

if TYPE_CHECKING:
    from .engine import TradingEngine


def money(value: Any) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise AppError(502, "Delta returned an invalid balance", "account_balance_unknown") from error
    if not result.is_finite():
        raise AppError(502, "Delta returned an invalid balance", "account_balance_unknown")
    return result


def usd_wallet(response: dict[str, Any]) -> tuple[Decimal, Decimal]:
    rows = response.get("result")
    if not isinstance(rows, list):
        raise AppError(502, "Delta wallet is unavailable", "account_balance_unknown")
    wallets = [row for row in rows if isinstance(row, dict) and row.get("asset_symbol") == "USD"]
    if len(wallets) != 1:
        raise AppError(502, "Delta USD wallet is unavailable", "account_balance_unknown")
    available, total = money(wallets[0].get("available_balance")), money(wallets[0].get("balance"))
    return max(Decimal(0), available), total


def transfer_time(value: Any) -> datetime | None:
    try:
        if str(value).isdigit():
            raw = int(value)
            return datetime.fromtimestamp(raw / (1_000_000 if raw > 10**14 else 1_000 if raw > 10**11 else 1), UTC)
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone(UTC) if parsed.utcoffset() is not None else None
    except (ValueError, OverflowError, OSError):
        return None


class AccountFunding:
    def __init__(self, registry: AccountRegistry, engine: "TradingEngine") -> None:
        self.registry = registry
        self.engine = engine

    async def records(self, owner: str) -> list[dict[str, Any]]:
        async with self.registry.pool.connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
            await cursor.execute(
                """select id,source_id,target_id,asset_symbol,amount,state,error,created_at,updated_at
                   from trade.account_transfers where owner_id=%s order by created_at desc limit 50""",
                (owner,),
            )
            rows = await cursor.fetchall()
        return [{**row, "amount": str(row["amount"])} for row in rows]

    async def flat(self, owner: str, account: str | None) -> tuple[Decimal, Decimal]:
        client = await self.engine.client_for_user(owner, exchange_account_id=account)
        try:
            exposure = await self.engine.account_exposure(client)
            if any(exposure.positions.values()) or exposure.open_orders:
                raise AppError(
                    409, "Transfers out require an account with no positions or open orders", "subaccount_not_flat"
                )
            journal = client.order_journal
            async with self.registry.pool.connection() as connection:
                pending = await connection.execute(
                    "select 1 from trade.order_intents where account_id=%s and unresolved limit 1",
                    (journal.account_id,),
                )
                if await pending.fetchone():
                    raise AppError(409, "Resolve uncertain orders before moving funds", "subaccount_not_flat")
            return usd_wallet(await client.balances())
        finally:
            await client.close()

    async def history(self, owner: str, after: str | None = None) -> dict[str, Any]:
        await self.registry.main(owner)
        client = await self.engine.client_for_user(owner)
        try:
            response = await client.request(
                "GET",
                "/v2/wallets/sub_accounts_transfer_history",
                query={"page_size": 100, **({"after": after} if after else {})},
                authenticated=True,
            )
            if not isinstance(response.get("result"), list):
                raise AppError(502, "Delta transfer history is unavailable", "transfer_history_unknown")
            return {"result": response["result"], "meta": response.get("meta") or {}}
        finally:
            await client.close()

    async def transfer(
        self, owner: str, identity: UUID, source: str, target: str, amount: Decimal, *, strategy: str | None = None
    ) -> dict[str, Any]:
        if not amount.is_finite() or amount <= 0 or amount.as_tuple().exponent < -10 or amount >= Decimal("1e18"):
            raise AppError(422, "Enter a positive USD amount with at most 10 decimal places", "transfer_amount_invalid")
        parent = str((await self.registry.main(owner))["delta_user_id"])
        valid = {
            parent,
            *(
                row["account_id"]
                for row in await self.registry.accounts(owner)
                if row["present"] and row["parent_id"] == parent
            ),
        }
        if source == target or source not in valid or target not in valid:
            raise AppError(
                422, "Transfers must stay within your current Delta account family", "transfer_accounts_invalid"
            )
        # Return the original outcome for a repeated request, including an uncertain one.
        async with self.registry.pool.connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
            await cursor.execute("select * from trade.account_transfers where id=%s", (identity,))
            existing = await cursor.fetchone()
        if existing:
            if (existing["owner_id"], existing["source_id"], existing["target_id"], existing["amount"]) != (
                owner,
                source,
                target,
                amount,
            ):
                raise AppError(409, "Transfer request ID has already been used", "transfer_identity_conflict")
            return {"id": str(identity), "state": existing["state"]}
        available, _ = await self.flat(owner, None if source == parent else source)
        if available < amount:
            raise AppError(409, "The source account has insufficient available USD", "transfer_balance_insufficient")
        try:
            async with self.registry.pool.connection() as connection:
                await connection.execute("select pg_advisory_xact_lock(hashtextextended(%s,41))", (parent,))
                await connection.execute("select pg_advisory_xact_lock(hashtextextended(%s,52))", (parent,))
                for account in sorted({source, target} - {parent}):
                    await connection.execute("select pg_advisory_xact_lock(hashtextextended(%s,53))", (account,))
                if source == parent:
                    busy = await connection.execute(
                        """select 1 from trade.strategy_capital_slots slots
                           join trade.users u on u.user_id=slots.owner_id
                           left join trade.account_assignments a on a.strategy_id=slots.relation_id
                           where u.connection->>'delta_user_id'=%s and slots.status in ('reserved','active')
                             and a.strategy_id is null limit 1""",
                        (parent,),
                    )
                    if await busy.fetchone():
                        raise AppError(409, "Main-account capital is reserved for a run", "subaccount_busy")
                # Serialize against active assignments before any money leaves a subaccount.
                if source != parent:
                    busy = await connection.execute(
                        """select strategy_id,state from trade.account_assignments
                           where account_id=%s and state<>'released'""",
                        (source,),
                    )
                    assigned = await busy.fetchone()
                    trimming_reserved = (
                        assigned and assigned[0] == strategy and assigned[1] == "reserved" and target == parent
                    )
                    if assigned and not trimming_reserved:
                        raise AppError(409, "Release the assigned run before moving funds out", "subaccount_busy")
                await connection.execute(
                    """insert into trade.account_transfers
                       (id,owner_id,parent_id,source_id,target_id,asset_symbol,amount,state,strategy_id)
                       values (%s,%s,%s,%s,%s,'USD',%s,'prepared',%s)""",
                    (identity, owner, parent, source, target, amount, strategy),
                )
        except UniqueViolation as error:
            raise AppError(
                409, "Another transfer is pending. Reconcile it before transferring again", "transfer_pending"
            ) from error
        client = await self.engine.client_for_user(owner)
        try:
            await self._state(owner, identity, "submitting")
            await client.request(
                "POST",
                "/v2/wallets/sub_account_balance_transfer",
                body={
                    "transferrer_user_id": source,
                    "transferee_user_id": target,
                    "asset_symbol": "USD",
                    "amount": format(amount, "f"),
                },
                authenticated=True,
            )
        except Exception as error:
            definite = isinstance(error, AppError) and 400 <= error.status < 500 and error.status not in {408, 429}
            await self._state(
                owner,
                identity,
                "failed" if definite else "unknown",
                "Delta rejected the transfer"
                if definite
                else "Outcome unknown. Check Delta transfer history before continuing",
            )
            raise AppError(
                409,
                "Transfer failed" if definite else "Transfer outcome is unknown. Do not repeat it",
                "transfer_failed" if definite else "transfer_unknown",
            ) from error
        finally:
            await client.close()
        await self._state(owner, identity, "confirmed")
        return {"id": str(identity), "state": "confirmed"}

    async def _state(self, owner: str, identity: UUID, state: str, error: str | None = None) -> None:
        async with self.registry.pool.connection() as connection:
            await connection.execute(
                "update trade.account_transfers set state=%s,error=%s,updated_at=now() where id=%s and owner_id=%s",
                (state, error, identity, owner),
            )

    async def reconcile(self, owner: str, identity: UUID) -> dict[str, Any]:
        async with self.registry.pool.connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
            await cursor.execute("select * from trade.account_transfers where id=%s and owner_id=%s", (identity, owner))
            intent = await cursor.fetchone()
        if not intent:
            raise AppError(404, "Transfer not found", "transfer_not_found")
        if intent["state"] in {"confirmed", "failed"}:
            return {"id": str(identity), "state": intent["state"]}
        # Deliberately never infer failure from a missing history row or replay the POST.
        after = None
        matches = []
        seen: set[str] = set()
        for _ in range(20):
            page = await self.history(owner, after)
            for row in page["result"]:
                when = transfer_time(row.get("created_at"))
                if (
                    str(row.get("transferrer_user_id")) == intent["source_id"]
                    and str(row.get("transferee_user_id")) == intent["target_id"]
                    and row.get("asset_symbol") == "USD"
                    and money(row.get("amount")) == intent["amount"]
                    and when is not None
                    and intent["created_at"] <= when <= datetime.now(UTC)
                ):
                    matches.append(row)
            after = page["meta"].get("after")
            if not after:
                break
            if after in seen:
                raise AppError(502, "Delta repeated a transfer cursor", "transfer_history_unknown")
            seen.add(after)
        else:
            raise AppError(409, "History is incomplete. Verify this transfer on Delta", "transfer_unknown")
        if len(matches) != 1:
            raise AppError(
                409,
                "Transfer could not be uniquely matched. Verify it on Delta; automatic funding remains blocked",
                "transfer_unknown",
            )
        await self._state(owner, identity, "confirmed")
        return {"id": str(identity), "state": "confirmed"}
