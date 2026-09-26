"""PostgreSQL-backed exchange journal: identities commit before any order leaves the backend."""

import json
import re
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from .errors import AppError
from .order_journal import OrderJournal

CLIENT_ID = re.compile(r"^[a-zA-Z0-9_-]{1,32}$")
POSITIVE_INTEGER = re.compile(r"^[1-9][0-9]*$")


def _intent(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "accountId": row["account_id"],
        "clientOrderId": row["client_order_id"],
        "payload": row["payload"],
        "context": row["context"],
        "materialized": row["materialized"],
        "outcome": row["outcome"],
        "unresolved": row["unresolved"],
        "updatedAt": int(row["updated_at"].timestamp() * 1000),
        "_creationTime": int(row["created_at"].timestamp() * 1000),
    }


def _page(rows: list[dict[str, Any]], limit: int, converter: Any, cursor_key: str) -> dict[str, Any]:
    selected = rows[:limit]
    return {
        "page": [converter(row) for row in selected],
        "isDone": len(rows) <= limit,
        "continueCursor": selected[-1][cursor_key] if selected and len(rows) > limit else "",
    }


class LocalOrderJournal(OrderJournal):
    def __init__(self, pool: AsyncConnectionPool, account_id: str) -> None:
        self.pool = pool
        self.account_id = account_id

    async def call(self, path: str, args: dict[str, Any], *, query: bool = False) -> Any:
        try:
            return await self._dispatch(path, args)
        except (psycopg.OperationalError, TimeoutError) as error:
            # A lost connection leaves the operation's outcome unknown; callers treat it that way.
            raise AppError(
                503, "Trading journal unavailable; order outcome must be checked", "journal_unavailable"
            ) from error

    async def _dispatch(self, path: str, args: dict[str, Any]) -> Any:
        if path == "orderIntents:begin":
            return await self._begin(args)
        if path == "orderIntents:resolve":
            return await self._resolve(args)
        if path == "orderIntents:materialized":
            return await self._materialized(args)
        if path == "orderIntents:forStrategy":
            return await self._for_strategy(args)
        if path == "orderIntents:unresolved":
            return await self._unresolved(args)
        if path == "orderIntents:claimProducts":
            return await self._claim_products(args)
        if path == "orderIntents:releaseProducts":
            return await self._release_products(args)
        if path == "exchangeFills:ingest":
            return await self._ingest_fills(args)
        if path == "exchangeFills:forProduct":
            return await self._fills_for_product(args)
        raise AppError(500, "Unknown local journal operation", "journal_operation_unknown")

    async def _begin(self, args: dict[str, Any]) -> dict[str, Any]:
        client_id = str(args.get("clientOrderId") or "")
        payload = str(args.get("payload") or "")
        context = args.get("context")
        if not self.account_id or not CLIENT_ID.fullmatch(client_id) or len(payload) > 16384:
            raise AppError(422, "Invalid order identity or payload", "order_identity_invalid")
        try:
            json.loads(payload)
        except ValueError as error:
            raise AppError(422, "Invalid order payload", "order_payload_invalid") from error
        async with self.pool.connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
            await cursor.execute(
                """insert into trade.order_intents (account_id,client_order_id,payload,context)
                       values (%s,%s,%s,%s) on conflict do nothing returning client_order_id""",
                (self.account_id, client_id, payload, Jsonb(context) if context is not None else None),
            )
            inserted = await cursor.fetchone()
            if inserted:
                return {"dispatch": True, "outcome": {"kind": "unknown"}}
            await cursor.execute(
                """select payload,context,outcome from trade.order_intents
                       where account_id=%s and client_order_id=%s""",
                (self.account_id, client_id),
            )
            existing = await cursor.fetchone()
        if not existing or existing["payload"] != payload or existing["context"] != context:
            raise AppError(409, "Order identity changed", "order_identity_conflict")
        return {"dispatch": False, "outcome": existing["outcome"]}

    async def _resolve(self, args: dict[str, Any]) -> None:
        outcome = args["outcome"]
        if outcome.get("kind") == "unknown":
            return
        if outcome.get("kind") not in {"accepted", "rejected"}:
            raise AppError(422, "Invalid order outcome", "order_outcome_invalid")
        async with self.pool.connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
            await cursor.execute(
                """select outcome from trade.order_intents
                       where account_id=%s and client_order_id=%s for update""",
                (self.account_id, args["clientOrderId"]),
            )
            existing = await cursor.fetchone()
            if not existing:
                raise AppError(404, "Order intent missing", "order_intent_missing")
            if existing["outcome"]["kind"] != "unknown":
                if existing["outcome"] != outcome:
                    raise AppError(409, "Conflicting order outcome", "order_outcome_conflict")
                return
            await cursor.execute(
                """update trade.order_intents set outcome=%s, unresolved=false, updated_at=now()
                       where account_id=%s and client_order_id=%s""",
                (Jsonb(outcome), self.account_id, args["clientOrderId"]),
            )

    async def _materialized(self, args: dict[str, Any]) -> None:
        async with self.pool.connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
            await cursor.execute(
                """update trade.order_intents set materialized=true
                       where account_id=%s and client_order_id=%s and outcome->>'kind'<>'unknown'
                       returning client_order_id""",
                (self.account_id, args["clientOrderId"]),
            )
            if not await cursor.fetchone():
                raise AppError(409, "Order outcome must be known first", "order_outcome_unknown")

    async def _for_strategy(self, args: dict[str, Any]) -> dict[str, Any]:
        opts = args["paginationOpts"]
        limit = max(1, min(int(opts["numItems"]), 100))
        cursor = opts.get("cursor") or ""
        async with self.pool.connection() as connection, connection.cursor(row_factory=dict_row) as query:
            await query.execute(
                """select * from trade.order_intents
                       where account_id=%s and strategy_id=%s and client_order_id>%s
                       order by client_order_id limit %s""",
                (self.account_id, args["strategyId"], cursor, limit + 1),
            )
            rows = await query.fetchall()
        return _page(rows, limit, _intent, "client_order_id")

    async def _unresolved(self, args: dict[str, Any]) -> dict[str, Any]:
        opts = args["paginationOpts"]
        limit = max(1, min(int(opts["numItems"]), 100))
        cursor = opts.get("cursor") or ""
        async with self.pool.connection() as connection, connection.cursor(row_factory=dict_row) as query:
            await query.execute(
                """select * from trade.order_intents
                       where account_id=%s and unresolved and client_order_id>%s
                       order by client_order_id limit %s""",
                (self.account_id, cursor, limit + 1),
            )
            rows = await query.fetchall()
        return _page(rows, limit, _intent, "client_order_id")

    async def _claim_products(self, args: dict[str, Any]) -> None:
        product_ids = args["productIds"]
        strategy_id = args["strategyId"]
        if (
            not strategy_id
            or not product_ids
            or len(product_ids) > 12
            or len(set(product_ids)) != len(product_ids)
            or any(not POSITIVE_INTEGER.fullmatch(str(product_id)) for product_id in product_ids)
        ):
            raise AppError(422, "Invalid product claim", "product_claim_invalid")
        async with self.pool.connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
            for product_id in product_ids:
                await cursor.execute(
                    """insert into trade.product_claims (account_id,product_id,strategy_id)
                           values (%s,%s,%s) on conflict do nothing""",
                    (self.account_id, product_id, strategy_id),
                )
                await cursor.execute(
                    """select strategy_id from trade.product_claims
                           where account_id=%s and product_id=%s""",
                    (self.account_id, product_id),
                )
                owner = await cursor.fetchone()
                if not owner or owner["strategy_id"] != strategy_id:
                    raise AppError(409, "Contract already owned by another strategy", "product_claim_conflict")

    async def _release_products(self, args: dict[str, Any]) -> None:
        async with self.pool.connection() as connection:
            await connection.execute(
                "delete from trade.product_claims where account_id=%s and strategy_id=%s",
                (self.account_id, args["strategyId"]),
            )

    async def _ingest_fills(self, args: dict[str, Any]) -> None:
        fills = args["fills"]
        if len(fills) > 100:
            raise AppError(422, "Fill batch too large", "fill_batch_invalid")
        async with self.pool.connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
            for fill in fills:
                if (
                    not fill.get("fillId")
                    or not fill.get("orderId")
                    or not POSITIVE_INTEGER.fullmatch(str(fill.get("productId", "")))
                ):
                    raise AppError(422, "Invalid fill identity", "fill_invalid")
                quantity = Decimal(str(fill["quantity"]))
                price = Decimal(str(fill["price"]))
                commission = Decimal(str(fill["commission"])) if fill["commission"] is not None else None
                occurred_at = datetime.fromisoformat(str(fill["occurredAt"]).replace("Z", "+00:00"))
                if quantity <= 0 or price < 0 or occurred_at.tzinfo is None or fill["side"] not in {"buy", "sell"}:
                    raise AppError(422, "Invalid fill facts", "fill_invalid")
                await cursor.execute(
                    """select * from trade.exchange_fills
                           where account_id=%s and fill_id=%s for update""",
                    (self.account_id, fill["fillId"]),
                )
                existing = await cursor.fetchone()
                if existing:
                    facts = (
                        existing["product_id"],
                        existing["order_id"],
                        existing["side"],
                        existing["quantity"],
                        existing["price"],
                        existing["occurred_at"],
                    )
                    supplied = (
                        fill["productId"],
                        fill["orderId"],
                        fill["side"],
                        quantity,
                        price,
                        occurred_at.astimezone(UTC),
                    )
                    if facts != supplied or (
                        existing["commission"] is not None
                        and commission is not None
                        and existing["commission"] != commission
                    ):
                        raise AppError(409, "Conflicting fill facts", "fill_conflict")
                    if existing["commission"] is None and commission is not None:
                        await cursor.execute(
                            """update trade.exchange_fills set commission=%s
                                   where account_id=%s and fill_id=%s""",
                            (commission, self.account_id, fill["fillId"]),
                        )
                else:
                    await cursor.execute(
                        """insert into trade.exchange_fills
                               (account_id,fill_id,product_id,order_id,side,quantity,price,commission,occurred_at)
                               values (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (
                            self.account_id,
                            fill["fillId"],
                            fill["productId"],
                            fill["orderId"],
                            fill["side"],
                            quantity,
                            price,
                            commission,
                            occurred_at,
                        ),
                    )

    async def _fills_for_product(self, args: dict[str, Any]) -> dict[str, Any]:
        opts = args["paginationOpts"]
        limit = max(1, min(int(opts["numItems"]), 100))
        cursor = opts.get("cursor") or ""
        async with self.pool.connection() as connection, connection.cursor(row_factory=dict_row) as query:
            await query.execute(
                """select * from trade.exchange_fills
                       where account_id=%s and product_id=%s and occurred_at >= %s and fill_id>%s
                       order by fill_id limit %s""",
                (self.account_id, args["productId"], args["from"], cursor, limit + 1),
            )
            rows = await query.fetchall()
        return _page(
            rows,
            limit,
            lambda row: {
                "accountId": row["account_id"],
                "fillId": row["fill_id"],
                "productId": row["product_id"],
                "orderId": row["order_id"],
                "side": row["side"],
                "quantity": str(row["quantity"]),
                "price": str(row["price"]),
                "commission": str(row["commission"]) if row["commission"] is not None else None,
                "occurredAt": row["occurred_at"].isoformat(),
            },
            "fill_id",
        )
