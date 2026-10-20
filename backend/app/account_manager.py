"""Subaccount selection, funding and release around the existing trading engine."""

import asyncio
import logging
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import NAMESPACE_URL, uuid5

from psycopg.rows import dict_row

from .account_funding import AccountFunding, usd_wallet
from .account_registry import AccountRegistry
from .capital import CapitalPolicy, policy_budget
from .errors import AppError

if TYPE_CHECKING:
    from .engine import TradingEngine

logger = logging.getLogger(__name__)


class AccountManager:
    def __init__(self, engine: "TradingEngine") -> None:
        self.engine = engine
        self.registry = AccountRegistry(engine.db)
        self.funding = AccountFunding(self.registry, engine)

    async def wallet(self, owner: str) -> tuple[Decimal, Decimal]:
        """Transfers are internal: sum each managed exchange wallet exactly once."""
        parent = str((await self.registry.main(owner))["delta_user_id"])
        accounts = {
            row["account_id"]
            for row in await self.registry.accounts(owner)
            if row["parent_id"] == parent
            and row["present"]
            and row["connected"]
            and (row["participation"] != "disabled" or row["busy"])
        }
        limit = asyncio.Semaphore(4)

        async def read(account: str | None) -> tuple[Decimal, Decimal]:
            async with limit:
                client = await self.engine.client_for_user(owner, exchange_account_id=account)
                try:
                    return usd_wallet(await client.balances())
                finally:
                    await client.close()

        wallets = await asyncio.gather(read(None), *(read(account) for account in sorted(accounts)))
        return sum((item[0] for item in wallets), Decimal(0)), sum((item[1] for item in wallets), Decimal(0))

    async def available(self, owner: str) -> bool:
        settings = await self.registry.settings(owner)
        if settings["mode"] == "main":
            return True
        parent = str((await self.registry.main(owner))["delta_user_id"])
        return (
            any(
                row["present"]
                and row["connected"]
                and row["parent_id"] == parent
                and row["participation"] == "automatic"
                and not row["busy"]
                for row in await self.registry.accounts(owner)
            )
            if settings["mode"] == "automatic"
            else False
        )

    async def choose(self, row: dict[str, Any]) -> dict[str, Any] | None:
        owner, strategy = str(row["user_id"]), str(row["id"])
        existing = await self.registry.assignment(owner, strategy)
        if existing:
            if existing["state"] == "released":
                raise AppError(409, "The run's account assignment has been released", "subaccount_assignment_blocked")
            return existing
        settings = await self.registry.settings(owner)
        if settings["mode"] == "main":
            return None
        if settings["mode"] == "manual":
            raise AppError(
                409, "Assign a subaccount to this run in Profile before entry", "subaccount_assignment_required"
            )
        parent = str((await self.registry.main(owner))["delta_user_id"])
        for account in await self.registry.accounts(owner):
            if (
                account["parent_id"] != parent
                or not account["present"]
                or not account["connected"]
                or account["participation"] != "automatic"
                or account["busy"]
            ):
                continue
            try:
                await self.funding.flat(owner, account["account_id"])
                return await self.registry.reserve(owner, strategy, account["account_id"])
            except AppError as error:
                logger.info(
                    "Subaccount unavailable owner=%s account=%s code=%s", owner, account["account_id"], error.code
                )
        raise AppError(409, "No connected, enabled and flat subaccount is available", "subaccounts_full")

    async def entry_budget(
        self, owner: str, assigned: dict[str, Any], policy: CapitalPolicy, asset: str
    ) -> tuple[tuple[Decimal, Decimal], Decimal]:
        child = await self.registry.row(owner, assigned["account_id"])
        parent = str((await self.registry.main(owner))["delta_user_id"])
        if (
            child["parent_id"] != parent
            or assigned["parent_id"] != parent
            or not child["present"]
            or child["participation"] == "disabled"
        ):
            raise AppError(409, "This subaccount is no longer enabled for entries", "subaccount_not_ready")
        await self.funding.flat(owner, assigned["account_id"])
        wallet = await self.wallet(owner)
        if assigned["budget"] is not None:
            total, budget = assigned["pool_total"], assigned["budget"]
            if policy_budget(total, total, policy, asset) != budget:
                raise AppError(409, "Capital settings changed after this run was funded", "subaccount_entry_review")
            return (wallet[0], total), budget
        budget = policy_budget(*wallet, policy, asset)
        if budget <= 0:
            raise AppError(409, "The managed pool cannot fund this strategy", "automation_balance_unavailable")
        return wallet, budget

    async def fund(self, owner: str, assigned: dict[str, Any], budget: Decimal, total: Decimal) -> None:
        await self.registry.set_budget(owner, assigned["strategy_id"], budget, total)
        available, _ = await self.funding.flat(owner, assigned["account_id"])
        settings = await self.registry.settings(owner)
        parent = str((await self.registry.main(owner))["delta_user_id"])
        if available > budget and settings["auto_fund"]:
            identity = uuid5(NAMESPACE_URL, f"delta-surplus:{owner}:{assigned['strategy_id']}")
            result = await self.funding.transfer(
                owner, identity, assigned["account_id"], parent, available - budget, strategy=assigned["strategy_id"]
            )
            if result["state"] != "confirmed":
                raise AppError(409, "Reconcile the surplus transfer before entry", "transfer_unknown")
            available, _ = await self.funding.flat(owner, assigned["account_id"])
        if available >= budget:
            return
        if not settings["auto_fund"]:
            raise AppError(
                409, "Fund this subaccount or enable automatic funding in Profile", "subaccount_funding_required"
            )
        required = budget - available
        main_available, _ = await self.funding.flat(owner, None)
        if main_available < required:
            # Reuse idle capital only. An assigned account's remaining margin is never a funding source.
            for account in await self.registry.accounts(owner):
                if (
                    account["account_id"] == assigned["account_id"]
                    or account["busy"]
                    or not account["present"]
                    or not account["connected"]
                    or account["parent_id"] != parent
                    or account["participation"] == "disabled"
                ):
                    continue
                try:
                    idle_available, _ = await self.funding.flat(owner, account["account_id"])
                except AppError as error:
                    logger.info("Idle funding source unavailable account=%s code=%s", account["account_id"], error.code)
                    continue
                amount = min(idle_available, required - main_available)
                if amount <= 0:
                    continue
                reclaim_id = uuid5(
                    NAMESPACE_URL, f"delta-reclaim:{owner}:{assigned['strategy_id']}:{account['account_id']}"
                )
                await self.funding.transfer(
                    owner, reclaim_id, account["account_id"], parent, amount, strategy=assigned["strategy_id"]
                )
                main_available, _ = await self.funding.flat(owner, None)
                if main_available >= required:
                    break
        identity = uuid5(NAMESPACE_URL, f"delta-funding:{owner}:{assigned['strategy_id']}")
        result = await self.funding.transfer(
            owner, identity, parent, assigned["account_id"], required, strategy=assigned["strategy_id"]
        )
        if result["state"] != "confirmed":
            raise AppError(409, "The funding transfer must be reconciled before entry", "transfer_unknown")
        available, _ = await self.funding.flat(owner, assigned["account_id"])
        if available < budget:
            raise AppError(
                409, "Delta has not made the transferred capital available yet", "subaccount_funding_required"
            )

    async def release(self, owner: str, strategy: str) -> None:
        assigned = await self.registry.assignment(owner, strategy)
        if not assigned or assigned["state"] == "released":
            return
        # A terminal run alone is insufficient: prove the exchange is flat and no transfer is uncertain.
        await self.funding.flat(owner, assigned["account_id"])
        async with self.registry.pool.connection() as connection:
            pending = await connection.execute(
                """select 1 from trade.account_transfers where parent_id=%s
                   and state in ('prepared','submitting','unknown') limit 1""",
                (assigned["parent_id"],),
            )
            if await pending.fetchone():
                raise AppError(409, "Reconcile the pending transfer before releasing the account", "transfer_pending")
        await self.registry.mark(owner, strategy, "released")

    async def release_finished(self, owner: str | None = None) -> None:
        async with self.registry.pool.connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
            await cursor.execute(
                """select a.owner_id,a.strategy_id from trade.account_assignments a
                   join trade.strategies s on s.id=a.strategy_id
                   where a.state<>'released' and s.status in ('completed','cancelled','skipped')
                   and (%s::text is null or a.owner_id=%s) order by a.created_at limit 100""",
                (owner, owner),
            )
            rows = await cursor.fetchall()
        for row in rows:
            recovery = getattr(self.engine, "recovery", None)
            if recovery is not None and not await recovery.claim(row["strategy_id"], "release"):
                continue
            try:
                await self.release(row["owner_id"], row["strategy_id"])
                if recovery is not None:
                    await recovery.clear(row["strategy_id"], "release")
            except Exception as error:
                code = error.code if isinstance(error, AppError) else type(error).__name__
                if recovery is not None and await recovery.failed(row["strategy_id"], "release", error):
                    self.engine.notify_strategy(row["strategy_id"], "exit_failed",
                                                "Subaccount release paused; resolve the failure and resume recovery")
                logger.warning("Subaccount release pending strategy=%s code=%s", row["strategy_id"], code)
