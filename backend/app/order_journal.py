"""Durable submission identities. An unknown outcome is never resubmitted."""

import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from .errors import AppError, DeltaOrderRejected


class OrderJournal:
    """Dispatch contract shared by journal storage backends.

    Subclasses implement ``call`` for the named journal operations. This class owns the
    order-identity rules: an identity is committed before dispatch, a known outcome is
    replayed instead of resubmitted, and an unknown outcome is resolved only by lookup.
    """

    account_id: str

    async def call(self, path: str, args: dict[str, Any], *, query: bool = False) -> Any:
        raise NotImplementedError

    async def ingest_fills(self, fills: list[dict[str, Any]]) -> None:
        def amount(value: Any) -> str:
            return format(Decimal(str(value)).normalize(), "f")

        payload = [
            {
                "fillId": str(fill["id"]),
                "productId": str(fill["product_id"]),
                "orderId": str(fill["order_id"]),
                "side": fill["side"],
                "quantity": amount(fill["size"]),
                "price": amount(fill["price"]),
                "commission": amount(fill["commission"]) if fill.get("commission") is not None else None,
                "occurredAt": datetime.fromisoformat(str(fill["created_at"]).replace("Z", "+00:00"))
                .astimezone(UTC)
                .isoformat(timespec="microseconds"),
            }
            for fill in fills
        ]
        for offset in range(0, len(payload), 100):
            await self.call("exchangeFills:ingest", {"fills": payload[offset : offset + 100]})

    async def submit(
        self,
        payload: dict[str, Any],
        send: Callable[[], Awaitable[dict[str, Any]]],
        lookup: Callable[[str], Awaitable[dict[str, Any]]],
        *,
        context: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        client_id = str(payload.get("client_order_id") or "")
        if not client_id:
            raise AppError(422, "An order identity is required", "order_identity_missing")
        claim = await self.call(
            "orderIntents:begin",
            {
                "clientOrderId": client_id,
                "payload": json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False),
                **({"context": context} if context is not None else {}),
            },
        )
        if not isinstance(claim, dict) or type(claim.get("dispatch")) is not bool:
            raise AppError(503, "Trading journal returned an invalid claim", "journal_invalid_response")
        outcome = claim.get("outcome")
        if not isinstance(outcome, dict):
            raise AppError(503, "Trading journal returned an invalid outcome", "journal_invalid_response")
        if not claim["dispatch"]:
            if outcome.get("kind") == "accepted":
                result = json.loads(outcome["response"])
                self.validate_response(result)
                return result
            if outcome.get("kind") == "rejected":
                raise DeltaOrderRejected(409, outcome["message"], outcome["code"])
            # Not-found, authentication failures and network errors all leave the intent unknown.
            result = await lookup(client_id)
            self.validate_response(result)
        else:
            try:
                result = await send()
                self.validate_response(result)
            except DeltaOrderRejected as error:
                await self.call(
                    "orderIntents:resolve",
                    {
                        "clientOrderId": client_id,
                        "outcome": {"kind": "rejected", "code": error.code, "message": error.message},
                    },
                )
                raise
        await self.call(
            "orderIntents:resolve",
            {
                "clientOrderId": client_id,
                "outcome": {"kind": "accepted", "response": json.dumps(result, sort_keys=True, separators=(",", ":"))},
            },
        )
        return result

    async def strategy_intents(self, strategy_id: str) -> list[dict[str, Any]]:
        intents: list[dict[str, Any]] = []
        cursor: str | None = None
        seen: set[str] = set()
        while True:
            page = await self.call(
                "orderIntents:forStrategy",
                {
                    "strategyId": strategy_id,
                    "paginationOpts": {"numItems": 100, "cursor": cursor},
                },
                query=True,
            )
            if not isinstance(page, dict) or not isinstance(page.get("page"), list):
                raise AppError(503, "Invalid journal history", "journal_invalid_response")
            intents.extend(page["page"])
            if page.get("isDone") is True:
                return intents
            cursor = page.get("continueCursor")
            if not isinstance(cursor, str) or cursor in seen:
                raise AppError(503, "Incomplete journal history", "journal_invalid_response")
            seen.add(cursor)

    async def known_outcome(
        self, intent: dict[str, Any], lookup: Callable[[str], Awaitable[dict[str, Any]]]
    ) -> dict[str, Any]:
        outcome = intent["outcome"]
        if outcome["kind"] != "unknown":
            return outcome
        response = await lookup(intent["clientOrderId"])
        self.validate_response(response)
        outcome = {"kind": "accepted", "response": json.dumps(response, sort_keys=True, separators=(",", ":"))}
        await self.call("orderIntents:resolve", {"clientOrderId": intent["clientOrderId"], "outcome": outcome})
        return outcome

    @staticmethod
    def validate_response(response: Any) -> None:
        result = response.get("result") if isinstance(response, dict) else None
        if not isinstance(result, dict) or not result.get("id"):
            raise AppError(502, "Order acceptance is unconfirmed", "order_outcome_unknown")
