import asyncio
import json
import logging
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from .application_data import ConvexApplicationData
from .auth import credentials_for_user
from .capital import CapitalPolicy, capital_budget, maximum_concurrent_strategies, policy_from_row
from .config import Settings
from .delta import DeltaClient, RequestBudget
from .delta_events import DeltaEvents, PublicMarkFeeds
from .errors import AppError, DeltaOrderRejected
from .fill_accounting import PositionResult, exclusive_fill_positions
from .models import StrategyDefinition
from .order_journal import ConvexOrderJournal
from .strategy import (
    deferred_control_warnings,
    delta_expiry,
    maximum_expiry_loss,
    resolve_leg,
    strategy_level_metrics,
    validate_entry_policy,
)
from .supabase import SupabaseAdmin

logger = logging.getLogger(__name__)
ATTENTION_RECONCILE_SECONDS = 30.0
FLAT_REPORT_RECONCILE_SECONDS = 300.0
UNAVAILABLE_CONTRACT_CODES = {"invalid_contract", "expired_contract"}

# Columns added by migration 004. Execution must never fail because an audit
# field is missing, so writes degrade to the pre-migration column set instead.
OPTIONAL_ORDER_METADATA = (
    "order_type",
    "limit_price",
    "reference_price",
    "contract_value",
    "slippage",
    "slippage_percent",
)

TERMINAL_SCHEDULED_ENTRY_CODES = frozenset(
    {
        "automatic_lot_too_large",
        "automation_balance_unavailable",
        "automatic_lot_risk_invalid",
        "manual_lots_exceed_capital_budget",
        "capital_slots_full",
        "option_chain_empty",
        "spot_price_missing",
        "strike_not_found",
        "delta_not_connected",
        "unsupported_risk_control",
        "conflicting_stop_percentages",
        "instrument_mismatch",
        "duplicate_leg_id",
        "duplicate_contract",
        "invalid_profit_target",
        "emergency_policy_incomplete",
        "saved_strategy_version_changed",
        "unsupported_payoff",
        "unbounded_payoff",
    }
)


@dataclass(frozen=True, slots=True)
class AccountExposure:
    positions: dict[int, Decimal]
    open_orders: tuple[dict[str, Any], ...]


@dataclass(slots=True)
class AccountSession:
    http: httpx.AsyncClient
    budget: RequestBudget
    credentials: dict[str, str]
    events: DeltaEvents | None
    leases: int = 0
    last_used: float = 0


def has_exchange_exposure(product_ids: set[int], snapshot: AccountExposure) -> bool:
    return any(snapshot.positions.get(product_id, Decimal("0")) != 0 for product_id in product_ids) or any(
        int(order.get("product_id") or 0) in product_ids for order in snapshot.open_orders
    )


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso_now() -> str:
    return utc_now().isoformat().replace("+00:00", "Z")


def decimal_value(value: Any, default: str = "0") -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(default)


def optional_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        parsed = Decimal(str(value))
        return parsed if parsed.is_finite() else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def slippage_fields(side: str, reference: Any, average: Any) -> dict[str, str]:
    """
    Execution slippage against the mark price observed before submission.

    The sign is normalised so positive always means adverse: a buy that filled
    above the reference, or a sell that filled below it. Without a usable
    reference or fill price there is nothing honest to record, so nothing is.
    """
    reference_price = optional_decimal(reference)
    average_price = optional_decimal(average)
    if not reference_price or not average_price or reference_price <= 0 or average_price <= 0:
        return {}
    direction = Decimal("1") if side == "buy" else Decimal("-1")
    slippage = (average_price - reference_price) * direction
    return {
        "slippage": str(slippage),
        "slippage_percent": str(slippage / reference_price * Decimal("100")),
    }


def order_cash_flow(order: dict[str, Any]) -> Decimal:
    """
    Signed premium moved by one recorded order, in quote currency.

    Selling collects premium (positive), buying pays it (negative). Contract
    value converts lots into underlying units; it defaults to 1 so pre-migration
    rows still produce a directionally correct figure.
    """
    filled = decimal_value(order.get("filled_size"))
    if filled <= 0 and str(order.get("state")) == "closed":
        filled = decimal_value(order.get("size"))
    price = optional_decimal(order.get("average_fill_price")) or Decimal("0")
    contract_value = optional_decimal(order.get("contract_value")) or Decimal("1")
    direction = Decimal("1") if order.get("side") == "sell" else Decimal("-1")
    return direction * price * filled * contract_value


def settlement_summary(orders: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Money view of a run, rebuilt from the recorded orders rather than stored
    running totals, so it is identical whether it is computed at exit time or
    when the Information panel is opened months later.
    """
    entry_premium = Decimal("0")
    exit_premium = Decimal("0")
    commission = Decimal("0")
    slippage_cost = Decimal("0")
    requested_lots = Decimal("0")
    filled_lots = Decimal("0")
    closed_lots = Decimal("0")
    symbols: dict[str, dict[str, Decimal]] = {}

    for order in orders:
        is_exit = str(order.get("kind")) == "exit"
        cash = order_cash_flow(order)
        filled = decimal_value(order.get("filled_size"))
        if filled <= 0 and str(order.get("state")) == "closed":
            filled = decimal_value(order.get("size"))
        commission += decimal_value(order.get("commission"))
        slippage = optional_decimal(order.get("slippage"))
        contract_value = optional_decimal(order.get("contract_value")) or Decimal("1")
        if slippage is not None:
            slippage_cost += slippage * filled * contract_value
        if is_exit:
            exit_premium += cash
            closed_lots += filled
        else:
            entry_premium += cash
            requested_lots += decimal_value(order.get("size"))
            filled_lots += filled
        symbol = str(order.get("product_symbol") or "unknown")
        bucket = symbols.setdefault(
            symbol,
            {
                "entryPremium": Decimal("0"),
                "exitPremium": Decimal("0"),
                "commission": Decimal("0"),
                "entryLots": Decimal("0"),
                "exitLots": Decimal("0"),
            },
        )
        bucket["exitPremium" if is_exit else "entryPremium"] += cash
        bucket["exitLots" if is_exit else "entryLots"] += filled
        bucket["commission"] += decimal_value(order.get("commission"))

    gross = entry_premium + exit_premium
    return {
        "entryPremium": str(entry_premium),
        "exitPremium": str(exit_premium),
        "grossPnl": str(gross),
        "commission": str(commission),
        "realizedPnl": str(gross - commission),
        "slippageCost": str(slippage_cost),
        "requestedLots": str(requested_lots),
        "filledLots": str(filled_lots),
        "closedLots": str(closed_lots),
        "fullyClosed": bool(
            filled_lots > 0 and all(bucket["entryLots"] == bucket["exitLots"] for bucket in symbols.values())
        ),
        "bySymbol": [
            {
                "symbol": symbol,
                "entryPremium": str(bucket["entryPremium"]),
                "exitPremium": str(bucket["exitPremium"]),
                "commission": str(bucket["commission"]),
                "entryLots": str(bucket["entryLots"]),
                "exitLots": str(bucket["exitLots"]),
                "realizedPnl": str(bucket["entryPremium"] + bucket["exitPremium"] - bucket["commission"]),
            }
            for symbol, bucket in sorted(symbols.items())
        ],
    }


def base36(value: int) -> str:
    alphabet = "0123456789abcdefghijklmnopqrstuvwxyz"
    if value == 0:
        return "0"
    result = ""
    while value:
        value, remainder = divmod(value, 36)
        result = alphabet[remainder] + result
    return result


def settlement_client_order_id(strategy_id: str, fill: dict[str, Any]) -> str:
    identity = str(fill.get("id") or fill.get("order_id") or fill.get("created_at") or "settlement")
    token = "".join(character for character in identity.lower() if character.isalnum())[-18:]
    return f"st_{strategy_id[:8]}_{token}"[:32]


class TradingEngine:
    def __init__(self, db: SupabaseAdmin, settings: Settings) -> None:
        self.db = db
        self.settings = settings
        self.contract_values: dict[str, Decimal] = {}
        self.product_specs: dict[str, dict[str, Any]] = {}
        self.last_attention_reconcile = 0.0
        self.startup_recovered = not getattr(settings, "convex_order_journal_enabled", False)
        self.running_operations: set[str] = set()
        self.synced_fills: OrderedDict[tuple[str, str], str] = OrderedDict()
        self.sessions: dict[str, AccountSession] = {}
        self.exchange_http = httpx.AsyncClient(
            timeout=httpx.Timeout(12, connect=5),
            limits=httpx.Limits(max_connections=256, max_keepalive_connections=64),
        )
        self.account_budgets: dict[str, RequestBudget] = {}
        self.account_groups: dict[str, str] = {}
        self.account_groups_refresh_at = 0.0
        self.session_lock = asyncio.Lock()
        self.wake = asyncio.Event()
        self.public_marks = PublicMarkFeeds(
            self.wake, settings.delta_public_ws_url, settings.delta_private_ws_url
        ) if getattr(settings, "delta_events_enabled", False) else None
        self.last_public_mark_prune = 0.0
        self.risk_errors: dict[str, str] = {}
        self.application_data = (
            ConvexApplicationData(settings.convex_url, settings.convex_trading_secret, db.client)
            if getattr(settings, "convex_library_enabled", False)
            else None
        )

    async def client_for_user(self, user_id: str) -> DeltaClient:
        credentials = await credentials_for_user(self.db, user_id)
        async with self.session_lock:
            session = self.sessions.get(user_id)
            if session is not None and session.credentials != credentials:
                if session.leases:
                    raise AppError(
                        409, "Credentials changed during an account operation", "account_credentials_changed"
                    )
                await self.close_session(user_id)
                session = None
            if session is None:
                for owner, idle in list(self.sessions.items()):
                    if idle.leases == 0 and time.monotonic() - idle.last_used > 300:
                        await self.close_session(owner)
                events = (
                    DeltaEvents(
                        credentials["api_key"],
                        credentials["api_secret"],
                        self.wake,
                        self.settings.delta_public_ws_url,
                        self.settings.delta_private_ws_url,
                        shared_marks=self.public_marks,
                    )
                    if self.settings.delta_events_enabled
                    else None
                )
                session = AccountSession(
                    self.exchange_http,
                    self.account_budgets.setdefault(credentials.get("delta_user_id") or user_id, RequestBudget()),
                    credentials,
                    events,
                )
                self.sessions[user_id] = session
                if events is not None:
                    events.start(("private",))
            session.leases += 1
            session.last_used = time.monotonic()

        def release() -> None:
            session.leases -= 1
            session.last_used = time.monotonic()

        client = DeltaClient(
            self.settings,
            credentials["api_key"],
            credentials["api_secret"],
            http_client=session.http,
            release=release,
            budget=session.budget,
            events=session.events,
        )
        if self.settings.convex_order_journal_enabled:
            if (
                not self.settings.convex_url
                or not self.settings.convex_trading_secret
                or not credentials.get("delta_user_id")
            ):
                await client.close()
                raise AppError(503, "Convex trading journal is not configured", "journal_not_configured")
            client.order_journal = ConvexOrderJournal(
                self.settings.convex_url,
                self.settings.convex_trading_secret,
                f"india:{credentials['delta_user_id']}",
                client.client,
            )
        return client

    async def close_session(self, user_id: str) -> None:
        session = self.sessions.pop(user_id, None)
        if session is not None and session.events is not None:
            await session.events.close()

    async def close(self) -> None:
        for user_id in list(self.sessions):
            await self.close_session(user_id)
        if self.public_marks is not None:
            await self.public_marks.close()
        await self.exchange_http.aclose()

    async def capital_policy(self, user_id: str) -> CapitalPolicy:
        if self.application_data is not None:
            return policy_from_row(await self.application_data.request("library:getCapital", {"userId": user_id}))
        rows = await self.db.select(
            "capital_settings",
            {"select": "allocation_mode,capital_amount", "user_id": f"eq.{user_id}", "limit": "1"},
        )
        if rows:
            return policy_from_row(rows[0])
        inserted = await self.db.upsert(
            "capital_settings",
            {"user_id": user_id, "allocation_mode": "half_balance", "capital_amount": None},
            on_conflict="user_id",
        )
        return policy_from_row(inserted[0] if inserted else None)

    async def saved_strategies(self, user_id: str, strategy_id: str | None = None) -> list[dict[str, Any]]:
        if self.application_data is not None:
            return await self.application_data.saved_strategies(user_id, strategy_id)
        params = {"select": "*", "or": f"(user_id.eq.{user_id},user_id.is.null)", "order": "name.asc"}
        if strategy_id is not None:
            params.update({"id": f"eq.{strategy_id}", "limit": "1"})
        return await self.db.select("saved_strategies", params)

    async def save_capital_policy(self, user_id: str, mode: str, amount: float | None) -> None:
        payload = {
            "user_id": user_id,
            "allocation_mode": mode,
            "capital_amount": amount if mode == "fixed_amount" else None,
        }
        if self.application_data is not None:
            payload["capital_amount"] = str(amount) if mode == "fixed_amount" else None
            await self.application_data.request("library:setCapital", {"value": payload}, mutation=True)
        elif not await self.db.upsert("capital_settings", payload, on_conflict="user_id"):
            raise AppError(500, "Could not save the capital policy", "capital_settings_failed")

    async def usd_capital(self, client: DeltaClient) -> tuple[Decimal, Decimal]:
        balances = (await client.balances()).get("result") or []
        usd_wallet = next(
            (
                item
                for item in balances
                if isinstance(item, dict) and str(item.get("asset_symbol", "")).upper() == "USD"
            ),
            None,
        )
        available = decimal_value(usd_wallet.get("available_balance")) if usd_wallet else Decimal("0")
        total_balance = decimal_value(usd_wallet.get("balance")) if usd_wallet else Decimal("0")
        if total_balance <= 0:
            total_balance = available
        return available, total_balance

    async def resolve_strategy(self, client: DeltaClient, definition: StrategyDefinition) -> list[dict[str, Any]]:
        chains: dict[str, list[dict[str, Any]]] = {}
        resolved: list[dict[str, Any]] = []
        for leg in definition.legs:
            expiry = leg.expiry.isoformat()
            if expiry not in chains:
                chains[expiry] = (
                    await client.option_chain(definition.instrument.underlying, delta_expiry(leg.expiry))
                )["result"]
            resolved.append(resolve_leg(leg, chains[expiry]))
        return resolved

    async def apply_automatic_lots(
        self,
        client: DeltaClient,
        definition: StrategyDefinition,
        resolved: list[dict[str, Any]],
        policy: CapitalPolicy | None = None,
        wallet: tuple[Decimal, Decimal] | None = None,
    ) -> list[dict[str, Any]]:
        policy = policy or CapitalPolicy()
        available, total_balance = wallet or await self.usd_capital(client)
        if available <= 0:
            raise AppError(409, "No available Delta balance can fund this strategy", "automation_balance_unavailable")
        allocation = capital_budget(
            available,
            total_balance,
            policy.allocation_mode,
            policy.capital_amount,
        )
        usable_capital = allocation * Decimal("0.98")
        products = await asyncio.gather(*(self.product_spec(client, str(leg["productSymbol"])) for leg in resolved))
        contract_values = [decimal_value(product.get("contract_value")) for product in products]

        signed_premium = Decimal("0")
        estimated_order_margin = Decimal("0")
        estimated_entry_fees = Decimal("0")
        for leg, multiplier, product in zip(resolved, contract_values, products, strict=True):
            quantity = Decimal(str(leg["lots"])) if definition.lotsMode == "manual" else Decimal("1")
            executable_price = optional_decimal(leg.get("bestAsk") if leg["position"] == "buy" else leg.get("bestBid"))
            if definition.riskBasis == "defined_max_loss" and (executable_price is None or executable_price <= 0):
                raise AppError(409, "A spread leg has no executable quote", "spread_quote_unavailable")
            if executable_price is None or executable_price <= 0:
                executable_price = optional_decimal(leg.get("markPrice"))
            if executable_price is None or not multiplier.is_finite() or executable_price <= 0 or multiplier <= 0:
                raise AppError(409, "A live option price is unavailable for automatic lots", "option_price_unavailable")
            direction = Decimal("1") if leg["position"] == "sell" else Decimal("-1")
            signed_premium += direction * executable_price * multiplier * quantity
            if definition.riskBasis == "defined_max_loss":
                rate = optional_decimal(product.get("taker_commission_rate"))
                if rate is None or rate <= 0:
                    raise AppError(409, "Spread trading fee is unavailable", "spread_fee_unavailable")
                spot = decimal_value(leg.get("spotPrice"))
                if spot <= 0:
                    raise AppError(409, "Spread spot price is unavailable", "spread_quote_unavailable")
                # Delta caps option fees at 3.5% of premium. Round-trip cost
                # uses today's quote as an estimate; actual exit cost can differ.
                estimated_entry_fees += min(
                    spot * multiplier * quantity * rate,
                    executable_price * multiplier * quantity * Decimal("0.035"),
                )
            if leg["position"] == "buy":
                estimated_order_margin += executable_price * multiplier * quantity
            else:
                spot = decimal_value(leg.get("spotPrice"))
                initial_margin_percent = decimal_value(product.get("initial_margin"))
                if (
                    not spot.is_finite()
                    or spot <= 0
                    or not initial_margin_percent.is_finite()
                    or initial_margin_percent < 0
                ):
                    raise AppError(409, "Short-option margin inputs are unavailable", "automatic_lot_risk_invalid")
                estimated_order_margin += spot * multiplier * quantity * initial_margin_percent / Decimal("100")
            leg["contractValue"] = str(multiplier)
            leg["sizingPrice"] = str(executable_price)

        if definition.riskBasis == "defined_max_loss" and signed_premium <= estimated_entry_fees * Decimal("2.36"):
            raise AppError(
                409, "Executable spread credit does not cover estimated round-trip fees",
                "spread_credit_too_small",
            )

        if definition.riskBasis == "net_debit":
            risk_per_lot = -signed_premium
        elif definition.riskBasis == "defined_max_loss":
            risk_per_lot = maximum_expiry_loss(
                [
                    {
                        "side": leg["position"],
                        "option_type": leg["optionType"],
                        "expiry": leg["expiry"],
                        "strike": leg["strike"],
                        "entry_price": leg["sizingPrice"],
                        "contract_value": leg["contractValue"],
                        "filled_size": leg["lots"] if definition.lotsMode == "manual" else 1,
                    }
                    for leg in resolved
                ]
            )
        else:
            risk_per_lot = signed_premium * decimal_value(definition.stopLossPercent) / Decimal("100")
        risk_per_lot = max(risk_per_lot, estimated_order_margin)
        if risk_per_lot <= 0:
            raise AppError(409, "Automatic lot risk could not be calculated", "automatic_lot_risk_invalid")

        if definition.lotsMode == "manual":
            if risk_per_lot > usable_capital:
                raise AppError(
                    409,
                    "The manual lot size exceeds the account capital budget",
                    "manual_lots_exceed_capital_budget",
                )
            return resolved

        lots_by_risk = int(usable_capital // risk_per_lot)
        lots = min(lots_by_risk, definition.maximumLots) if definition.maximumLots else lots_by_risk
        if lots < 1:
            raise AppError(
                409,
                "One lot does not fit inside the account capital budget",
                "automatic_lot_too_large",
            )
        return [{**leg, "lots": lots} for leg in resolved]

    async def reserve_capital_slot(
        self,
        user_id: str,
        strategy_id: str,
        maximum_slots: int,
        *,
        wallet: tuple[Decimal, Decimal],
        policy: CapitalPolicy,
    ) -> dict[str, Any]:
        extra = {}
        if getattr(self.db, "runtime", None) is not None:
            available, total = wallet
            extra = {
                "p_budget": format(
                    capital_budget(available, total, policy.allocation_mode, policy.capital_amount), "f"
                ),
                "p_total_balance": format(total, "f"),
            }
        reservation = await self.db.rpc(
            "reserve_strategy_capital_slot",
            {"p_user_id": user_id, "p_strategy_id": strategy_id, "p_maximum_slots": maximum_slots, **extra},
        )
        if not reservation:
            raise AppError(
                409,
                f"All {maximum_slots} capital allocations are occupied",
                "capital_slots_full",
            )
        return {
            "slot": int(reservation["slot"]),
            "created": bool(reservation["created"]),
            "occupiedBefore": int(reservation["occupiedBefore"]),
        }

    async def release_capital_slot(self, user_id: str, strategy_id: str) -> None:
        await self.db.rpc(
            "release_strategy_capital_slot",
            {"p_user_id": user_id, "p_strategy_id": strategy_id},
        )

    async def account_exposure(self, client: DeltaClient) -> AccountExposure:
        positions_response, orders_response = await asyncio.gather(client.positions(), client.open_orders())
        if not isinstance(orders_response.get("result"), list):
            raise AppError(502, "Delta orders snapshot is unavailable", "orders_unknown")
        orders = list(orders_response["result"])
        after = str((orders_response.get("meta") or {}).get("after") or "")
        seen: set[str] = set()
        while after and after not in seen:
            seen.add(after)
            orders_response = await client.open_orders(after=after)
            if not isinstance(orders_response.get("result"), list):
                raise AppError(502, "Delta orders page is unavailable", "orders_unknown")
            orders.extend(orders_response["result"])
            after = str((orders_response.get("meta") or {}).get("after") or "")
        if after:
            raise AppError(502, "Delta repeated an orders cursor", "orders_unknown")
        if any(not isinstance(order, dict) or not order.get("id") or not order.get("product_id") for order in orders):
            raise AppError(502, "Delta returned an invalid working order", "orders_unknown")
        raw_positions = positions_response.get("result")
        if not isinstance(raw_positions, list):
            raise AppError(502, "Delta positions snapshot is unavailable", "position_size_unknown")
        positions: dict[int, Decimal] = {}
        for item in raw_positions:
            if not isinstance(item, dict) or item.get("product_id") is None:
                raise AppError(502, "Delta returned an invalid position", "position_size_unknown")
            size = optional_decimal(item.get("size"))
            if size is None or size != size.to_integral_value():
                raise AppError(502, "Delta returned an invalid position quantity", "position_size_unknown")
            positions[int(item["product_id"])] = size
        return AccountExposure(positions=positions, open_orders=tuple(orders))

    async def fills_since(
        self,
        client: DeltaClient,
        product_ids: set[int],
        start_time: int,
    ) -> list[dict[str, Any]]:
        async def pages(ids: list[int] | None) -> list[dict[str, Any]]:
            result: list[dict[str, Any]] = []
            after: str | None = None
            seen: set[str] = set()
            while True:
                response = await client.fills(ids, start_time, after)
                page = response.get("result")
                if not isinstance(page, list) or any(not isinstance(fill, dict) for fill in page):
                    raise AppError(502, "Delta fills page is unavailable", "fills_incomplete")
                result.extend(page)
                next_after = str((response.get("meta") or {}).get("after") or "")
                if not next_after:
                    return result
                if next_after in seen:
                    raise AppError(502, "Delta repeated a fills cursor", "fills_incomplete")
                seen.add(next_after)
                after = next_after

        try:
            batches = [sorted(product_ids)[index : index + 10] for index in range(0, len(product_ids), 10)]
            return [fill for batch in await asyncio.gather(*(pages(ids) for ids in batches)) for fill in batch]
        except AppError as error:
            if error.code not in UNAVAILABLE_CONTRACT_CODES:
                raise
            logger.info("Falling back to account fills because a recorded contract has expired")
            return [fill for fill in await pages(None) if int(fill.get("product_id") or 0) in product_ids]

    async def preview_strategy(self, client: DeltaClient, definition: StrategyDefinition) -> dict[str, Any]:
        legs = await self.resolve_strategy(client, definition)
        deferred = deferred_control_warnings(definition)
        warnings = [
            "Delta Exchange cannot atomically batch different option contracts. "
            "Legs execute sequentially and stop after the first failure.",
            "Market orders may fill at prices different from the preview, especially in thin option books.",
        ]
        if definition.riskMode == "combined_premium":
            warnings.append(
                "The combined stop arms only after every entry leg is filled, then the backend monitors actual "
                "filled credit against live mark prices and submits separate reduce-only exits."
            )
        if deferred:
            warnings.append(
                f"{', '.join(deferred)} settings are saved for review but are not automatically monitored "
                "by this scheduler version."
            )
        return {"definition": definition.model_dump(mode="json", exclude_none=True), "legs": legs, "warnings": warnings}

    async def save_strategy(
        self,
        user_id: str,
        definition: StrategyDefinition,
        status: str,
        saved_strategy_id: str | None = None,
    ) -> dict[str, Any]:
        if status == "scheduled":
            now = utc_now()
            if definition.entry.exitAt <= now:
                raise AppError(400, "The scheduled exit time is already in the past", "exit_time_passed")
            lateness = (now - definition.entry.entryAt).total_seconds()
            if lateness > self.settings.max_entry_lateness_seconds:
                raise AppError(400, "The scheduled entry time is too far in the past", "entry_time_passed")
        if saved_strategy_id:
            saved = await self.saved_strategies(user_id, saved_strategy_id)
            if not saved:
                raise AppError(404, "Saved strategy not found", "saved_strategy_not_found")
        rows = await self.db.insert(
            "strategies",
            {
                "user_id": user_id,
                "saved_strategy_id": saved_strategy_id,
                "name": definition.name,
                "status": status,
                "definition_json": definition.model_dump(mode="json", exclude_none=True),
                "entry_at": definition.entry.entryAt.isoformat(),
                "exit_at": definition.entry.exitAt.isoformat(),
            },
        )
        if not rows:
            raise AppError(500, "Could not save the strategy", "strategy_save_failed")
        return {"id": rows[0]["id"], "status": rows[0]["status"]}

    async def cancel_strategy(self, strategy_id: str, user_id: str) -> None:
        rows = await self.db.update(
            "strategies",
            {"status": "cancelled", "last_error": None},
            {
                "select": "id",
                "id": f"eq.{strategy_id}",
                "user_id": f"eq.{user_id}",
                "status": "in.(draft,scheduled)",
            },
        )
        if not rows:
            raise AppError(409, "Only draft or scheduled strategies can be cancelled", "cannot_cancel")

    async def strategy_by_id(self, strategy_id: str) -> dict[str, Any]:
        rows = await self.db.select("strategies", {"select": "*", "id": f"eq.{strategy_id}", "limit": "1"})
        if not rows:
            raise AppError(404, "Strategy not found", "strategy_not_found")
        return rows[0]

    async def claim_strategy(
        self,
        strategy_id: str,
        statuses: list[str],
        next_status: str,
        execution_field: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        rows = await self.db.update(
            "strategies",
            {"status": next_status, **(metadata or {})},
            {
                "select": "id",
                "id": f"eq.{strategy_id}",
                execution_field: "is.null",
                "status": f"in.({','.join(statuses)})",
            },
        )
        if not rows:
            raise AppError(409, "Strategy is already running or cannot be executed", "execution_in_progress")

    async def claim_exit(self, strategy_id: str) -> None:
        rows = await self.db.update(
            "strategies",
            {"status": "executing_exit", "exit_execution_at": None},
            {
                "select": "id",
                "id": f"eq.{strategy_id}",
                "status": "in.(active,attention)",
            },
        )
        if not rows:
            raise AppError(409, "Strategy is already exiting or cannot be exited", "exit_in_progress")

    async def record_order(self, order: dict[str, Any]) -> None:
        payload = {
            key: value for key, value in order.items() if value is not None or key not in OPTIONAL_ORDER_METADATA
        }
        try:
            rows = await self.db.insert("execution_orders", payload)
        except AppError:
            stripped = {key: value for key, value in payload.items() if key not in OPTIONAL_ORDER_METADATA}
            if stripped == payload:
                raise
            logger.warning(
                "Recording execution metadata failed; retrying without the migration 004 columns. "
                "Apply supabase/migrations/004_run_execution_metadata.sql to keep slippage and premium history."
            )
            rows = await self.db.insert("execution_orders", stripped)
        if not rows:
            raise AppError(500, "Could not record an order result", "order_record_failed")

    async def write_audit(self, table: str, payload: dict[str, Any], params: dict[str, str]) -> None:
        """
        Best-effort write for reporting-only columns. A failure here must never
        change the outcome of a trade, so it is logged and swallowed.
        """
        try:
            await self.db.update(table, payload, params)
        except Exception as exc:
            logger.warning("Could not persist run metadata on %s: %s", table, exc)

    async def live_position_size(self, client: DeltaClient, product_id: int) -> Decimal:
        response = await client.position(product_id)
        result = response.get("result")
        size = optional_decimal(result.get("size")) if isinstance(result, dict) else None
        if size is None or size != size.to_integral_value():
            raise AppError(502, "Delta position quantity is unavailable or invalid", "position_size_unknown")
        return size

    async def place_reduce_only_close(
        self,
        client: DeltaClient,
        product_id: int,
        size: Decimal,
        initial_size: Decimal,
        client_order_id: str,
        product_symbol: str | None = None,
        context: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        if size <= 0 or size != size.to_integral_value():
            raise AppError(409, "Delta position size is not a positive whole number", "invalid_close_size")
        if initial_size == 0 or size > abs(initial_size):
            raise AppError(409, "Close size is larger than the live position", "invalid_close_size")

        payload: dict[str, Any] = {
            "product_id": product_id,
            "size": int(size),
            "side": "sell" if initial_size > 0 else "buy",
            "order_type": "market_order",
            "reduce_only": True,
            "client_order_id": client_order_id,
        }
        if product_symbol:
            payload["product_symbol"] = product_symbol
        order = (
            await client.place_order(payload, context=context)
            if getattr(client, "order_journal", None) is not None
            else await client.place_order(payload)
        )
        result = order.get("result") or {}
        if not result.get("id"):
            raise AppError(502, "Delta accepted no identifiable close order", "invalid_close_response")
        return order

    async def verify_position_reduction(
        self,
        client: DeltaClient,
        product_id: int,
        size: Decimal,
        initial_size: Decimal,
    ) -> Decimal:
        expected_size = initial_size - (size if initial_size > 0 else -size)
        deadline = time.monotonic() + self.settings.exit_verify_timeout_seconds
        while True:
            live_size = await self.live_position_size(client, product_id)
            if live_size == 0 or (
                expected_size != 0 and live_size * initial_size > 0 and abs(live_size) <= abs(expected_size)
            ):
                return live_size
            if time.monotonic() >= deadline:
                raise AppError(
                    502,
                    f"Delta did not confirm position reduction for product {product_id}",
                    "exit_not_confirmed",
                )
            await asyncio.sleep(self.settings.exit_verify_poll_seconds)

    async def submit_verified_close(
        self,
        client: DeltaClient,
        product_id: int,
        size: Decimal,
        initial_size: Decimal,
        client_order_id: str,
        product_symbol: str | None = None,
    ) -> tuple[dict[str, Any], Decimal]:
        order = await self.place_reduce_only_close(
            client, product_id, size, initial_size, client_order_id, product_symbol
        )
        remaining_size = await self.verify_position_reduction(client, product_id, size, initial_size)
        return order, remaining_size

    async def close_account_position(self, user_id: str, product_id: int) -> dict[str, Any]:
        client = await self.client_for_user(user_id)
        try:
            open_orders = [
                order
                for order in (await self.account_exposure(client)).open_orders
                if int(order["product_id"]) == product_id
            ]
            if any(order.get("reduce_only") is True and not order.get("stop_order_type") for order in open_orders):
                raise AppError(409, "An exit order is already working", "exit_order_pending")
            cancelled = 0
            for order in open_orders:
                if order.get("stop_order_type") or order.get("reduce_only") is True:
                    continue
                order_id = order.get("id")
                if order_id is not None:
                    await client.cancel_order(int(order_id), product_id)
                    cancelled += 1
            initial_size = await self.live_position_size(client, product_id)
            if initial_size == 0:
                raise AppError(409, "This Delta position is already closed", "position_already_closed")
            client_order_id = f"dp_{product_id}_{base36(int(time.time() * 1000))}"[:32]
            order, remaining_size = await self.submit_verified_close(
                client,
                product_id,
                abs(initial_size),
                initial_size,
                client_order_id,
            )
            return {
                "orderId": str((order.get("result") or {}).get("id")),
                "productId": product_id,
                "closedSize": str(abs(initial_size)),
                "remainingSize": str(remaining_size),
                "cancelledOrders": cancelled,
                "verified": True,
            }
        finally:
            await client.close()

    async def execute_entry(self, strategy_id: str) -> dict[str, Any]:
        if strategy_id in self.running_operations:
            raise AppError(409, "Strategy execution is already running", "execution_in_progress")
        self.running_operations.add(strategy_id)
        try:
            return await self._execute_entry(strategy_id)
        finally:
            self.running_operations.discard(strategy_id)

    async def _execute_entry(self, strategy_id: str) -> dict[str, Any]:
        if not self.startup_recovered:
            raise AppError(503, "Interrupted executions must be recovered before entry", "startup_recovery_pending")
        row = await self.strategy_by_id(strategy_id)
        if row.get("entry_execution_at"):
            raise AppError(409, "Strategy entry has already run", "already_executed")
        if row.get("shared_decision_id"):
            enabled_accounts = await self.db.select(
                "automation_settings", {"user_id": f"eq.{row['user_id']}", "enabled": "eq.true", "select": "user_id"}
            )
            recheck = await self.activation_recheck_states([strategy_id])
            if not enabled_accounts or recheck[strategy_id] != "ready":
                raise AppError(409, "Shared strategy is not authorized for entry", "activation_recheck_failed")
        definition = StrategyDefinition.model_validate(row["definition_json"])
        if self.application_data is not None and row.get("saved_strategy_id"):
            proposals = await self.db.select(
                "strategy_proposals",
                {
                    "select": "saved_strategy_version",
                    "strategy_id": f"eq.{strategy_id}",
                    "limit": "1",
                },
            )
            if proposals:
                current = await self.saved_strategies(str(row["user_id"]), str(row["saved_strategy_id"]))
                if (
                    not current
                    or not current[0]["enabled_for_ai"]
                    or current[0]["version"] != proposals[0]["saved_strategy_version"]
                ):
                    raise AppError(
                        409, "The selected library version changed or was retired", "saved_strategy_version_changed"
                    )
        client = await self.client_for_user(str(row["user_id"]))
        reservation: dict[str, Any] | None = None
        reservation_should_remain = False
        try:
            policy = await self.capital_policy(str(row["user_id"]))
            wallet = await self.usd_capital(client)
            available, total_balance = wallet
            maximum_slots = maximum_concurrent_strategies(
                total_balance,
                policy.allocation_mode,
                policy.capital_amount,
            )
            try:
                reservation = await self.reserve_capital_slot(
                    str(row["user_id"]), strategy_id, maximum_slots, wallet=wallet, policy=policy
                )
            except AppError as error:
                if error.code != "capital_slots_full":
                    raise
                await self.reconcile_attention_runs(str(row["user_id"]), client)
                reservation = await self.reserve_capital_slot(
                    str(row["user_id"]), strategy_id, maximum_slots, wallet=wallet, policy=policy
                )
            resolved = await self.resolve_strategy(client, definition)
            validate_entry_policy(definition, resolved)
            resolved = await self.apply_automatic_lots(client, definition, resolved, policy, wallet)
            exclusive = getattr(client, "order_journal", None) is not None
            allocated_budget = capital_budget(
                available,
                total_balance,
                policy.allocation_mode,
                policy.capital_amount,
            )
            try:
                await self.claim_strategy(
                    strategy_id,
                    ["draft", "scheduled"],
                    "executing_entry",
                    "entry_execution_at",
                    {
                        "capital_slot": reservation["slot"],
                        "risk_state": {
                            "exclusiveFillAccounting": exclusive,
                            "plannedProductIds": [str(leg["productId"]) for leg in resolved],
                            **({"exchangeAccount": client.order_journal.account_id} if exclusive else {}),
                        },
                        "capital_budget": str(allocated_budget),
                        "capital_policy_json": {
                            **policy.as_json(),
                            "maximumConcurrentStrategies": maximum_slots,
                            "totalBalanceAtEntry": str(total_balance),
                            "availableBalanceAtEntry": str(available),
                        },
                    },
                )
                if exclusive:
                    await client.order_journal.call(
                        "orderIntents:claimProducts",
                        {
                            "strategyId": strategy_id,
                            "productIds": [str(leg["productId"]) for leg in resolved],
                        },
                    )
                    baseline = await self.account_exposure(client)
                    sizes = await asyncio.gather(
                        *(self.live_position_size(client, int(leg["productId"])) for leg in resolved)
                    )
                    if any(sizes) or has_exchange_exposure({int(leg["productId"]) for leg in resolved}, baseline):
                        raise AppError(409, "A selected contract already has account exposure", "contract_already_open")
                executions = await self.db.insert(
                    "executions", {"strategy_id": strategy_id, "kind": "entry", "status": "running"}
                )
                if not executions:
                    raise AppError(500, "Could not start the execution record", "execution_record_failed")
            except Exception as error:
                if exclusive:
                    await self.db.update(
                        "strategies",
                        {
                            "status": "attention",
                            "last_error": f"Entry preparation interrupted: {error}",
                        },
                        {"id": f"eq.{strategy_id}", "status": "eq.executing_entry"},
                    )
                if reservation["created"]:
                    await self.release_capital_slot(str(row["user_id"]), strategy_id)
                raise
            execution_id = str(executions[0]["id"])
            failure: Exception | None = None
            submitted_orders = 0
            for index, leg in enumerate(resolved):
                client_order_id = f"ds_{strategy_id[:8]}_{index}_{base36(int(time.time() * 1000))}"[:32]
                try:
                    mark = float(leg.get("markPrice") or 0)
                    direction = 1 if leg["position"] == "buy" else -1
                    bracket: dict[str, Any] = {}
                    if mark > 0:
                        if definition.riskMode == "combined_premium" or (
                            definition.riskMode == "strategy_level" and leg["position"] == "sell"
                        ):
                            if (
                                leg["position"] == "sell"
                                and definition.emergencyExitEnabled
                                and definition.emergencyStopLossPercent
                            ):
                                emergency_multiplier = 1 + float(definition.emergencyStopLossPercent) / 100
                                bracket["bracket_stop_loss_price"] = str(max(0.00000001, mark * emergency_multiplier))
                                bracket["bracket_stop_trigger_method"] = "mark_price"
                        elif leg.get("targetProfit"):
                            bracket["bracket_take_profit_price"] = str(
                                max(0.00000001, mark + direction * float(leg["targetProfit"]))
                            )
                        if definition.riskMode == "legwise" and leg.get("stopLoss"):
                            bracket["bracket_stop_loss_price"] = str(
                                max(0.00000001, mark - direction * float(leg["stopLoss"]))
                            )
                        if definition.riskMode == "legwise" and leg.get("trailStop"):
                            bracket["bracket_trail_amount"] = str(leg["trailStop"])
                        if definition.riskMode == "legwise" and (leg.get("targetProfit") or leg.get("stopLoss")):
                            bracket["bracket_stop_trigger_method"] = "mark_price"
                    payload = {
                        "product_id": leg["productId"],
                        "product_symbol": leg["productSymbol"],
                        "size": leg["lots"],
                        "side": leg["position"],
                        "order_type": leg["orderType"],
                        "time_in_force": "gtc",
                        "reduce_only": False,
                        "client_order_id": client_order_id,
                        **bracket,
                    }
                    if leg["orderType"] == "limit_order":
                        payload["limit_price"] = leg["limitPrice"]
                    # A lost response or a failed accounting write may hide an accepted order.
                    reservation_should_remain = True
                    order = (
                        await client.place_order(
                            payload,
                            context={
                                "strategyId": strategy_id,
                                "executionId": execution_id,
                                "legId": leg["id"],
                                "contractValue": str(leg["contractValue"]),
                                "referencePrice": str(mark),
                            },
                        )
                        if exclusive
                        else await client.place_order(payload)
                    )
                    submitted_orders += 1
                    result = order["result"]
                    requested_size = decimal_value(leg["lots"])
                    unfilled_size = decimal_value(result.get("unfilled_size"), str(leg["lots"]))
                    reference_price = str(mark) if mark > 0 else None
                    await self.record_order(
                        {
                            "execution_id": execution_id,
                            "leg_id": leg["id"],
                            "delta_order_id": str(result.get("id") or ""),
                            "client_order_id": client_order_id,
                            "product_id": leg["productId"],
                            "product_symbol": leg["productSymbol"],
                            "side": leg["position"],
                            "size": leg["lots"],
                            "filled_size": str(max(Decimal("0"), requested_size - unfilled_size)),
                            "average_fill_price": result.get("average_fill_price"),
                            "commission": str(decimal_value(result.get("paid_commission") or result.get("commission"))),
                            "state": str(result.get("state") or "submitted"),
                            "order_type": leg["orderType"],
                            "limit_price": str(leg["limitPrice"]) if leg.get("limitPrice") is not None else None,
                            "reference_price": reference_price,
                            "contract_value": leg.get("contractValue"),
                            "response_json": order,
                            **slippage_fields(str(leg["position"]), reference_price, result.get("average_fill_price")),
                        }
                    )
                    if exclusive:
                        await client.order_journal.call("orderIntents:materialized", {"clientOrderId": client_order_id})
                except Exception as exc:
                    failure = exc
                    if isinstance(exc, DeltaOrderRejected) and submitted_orders == 0:
                        reservation_should_remain = False
                    await self.record_order(
                        {
                            "execution_id": execution_id,
                            "leg_id": leg["id"],
                            "client_order_id": client_order_id,
                            "product_id": leg["productId"],
                            "product_symbol": leg["productSymbol"],
                            "side": leg["position"],
                            "size": leg["lots"],
                            "state": "failed" if isinstance(exc, DeltaOrderRejected) else "unknown",
                            "order_type": leg["orderType"],
                            "limit_price": str(leg["limitPrice"]) if leg.get("limitPrice") is not None else None,
                            "reference_price": str(leg.get("markPrice")) if leg.get("markPrice") else None,
                            "response_json": {"error": str(exc)},
                        }
                    )
                    break
            completed = iso_now()
            await asyncio.gather(
                self.db.update(
                    "executions",
                    {
                        "status": "partial_or_failed" if failure else "completed",
                        "error": str(failure) if failure else None,
                        "completed_at": completed,
                    },
                    {"id": f"eq.{execution_id}"},
                ),
                self.db.update(
                    "strategies",
                    {
                        "status": "attention" if failure else "active",
                        "entry_execution_at": completed,
                        "last_error": str(failure) if failure else None,
                        **(
                            {
                                "risk_state": {
                                    "exclusiveFillAccounting": True,
                                    "exchangeAccount": client.order_journal.account_id,
                                    "plannedProductIds": [str(leg["productId"]) for leg in resolved],
                                    "exitRequested": True,
                                }
                            }
                            if exclusive and failure
                            else {}
                        ),
                    },
                    {"id": f"eq.{strategy_id}"},
                ),
            )
            if failure:
                if submitted_orders == 0 and not reservation_should_remain:
                    await self.release_capital_slot(str(row["user_id"]), strategy_id)
                else:
                    reservation_should_remain = True
                    await self.db.update(
                        "strategy_capital_slots",
                        {"status": "active"},
                        {"user_id": f"eq.{row['user_id']}", "strategy_id": f"eq.{strategy_id}"},
                    )
                raise failure
            await self.db.update(
                "strategy_capital_slots",
                {"status": "active"},
                {
                    "user_id": f"eq.{row['user_id']}",
                    "strategy_id": f"eq.{strategy_id}",
                    "status": "eq.reserved",
                },
            )
            await self.write_audit(
                "strategy_proposals",
                {"status": "activated"},
                {"strategy_id": f"eq.{strategy_id}", "status": "eq.scheduled"},
            )
            reservation_should_remain = True
            return {
                "executionId": execution_id,
                "legs": len(resolved),
                "capitalSlot": reservation["slot"],
                "capitalBudget": str(allocated_budget),
            }
        except Exception:
            if reservation and reservation["created"] and not reservation_should_remain:
                with suppress(Exception):
                    await self.release_capital_slot(str(row["user_id"]), strategy_id)
            raise
        finally:
            await client.close()

    async def entry_orders(self, strategy_id: str) -> list[dict[str, Any]]:
        entry_executions = await self.db.select(
            "executions",
            {"select": "id", "strategy_id": f"eq.{strategy_id}", "kind": "eq.entry"},
        )
        execution_ids = [str(item["id"]) for item in entry_executions]
        if not execution_ids:
            return []
        return await self.db.select(
            "execution_orders",
            {
                # `*` keeps this reader working whether or not the execution
                # metadata migration has been applied to the project.
                "select": "*",
                "execution_id": f"in.({','.join(execution_ids)})",
                "state": "neq.failed",
            },
        )

    async def reconcile_entry_fills(self, client: DeltaClient, orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not orders:
            return orders
        for order in orders:
            if order.get("delta_order_id") or order.get("state") != "unknown":
                continue
            response = await client.order_by_client_id(str(order["client_order_id"]))
            ConvexOrderJournal.validate_response(response)
            result = response["result"]
            values = {"delta_order_id": str(result["id"]), "state": str(result.get("state") or "submitted")}
            await self.db.update("execution_orders", values, {"id": f"eq.{order['id']}"})
            order.update(values)
        product_ids = sorted({int(order["product_id"]) for order in orders})
        created = [datetime.fromisoformat(str(order["created_at"]).replace("Z", "+00:00")) for order in orders]
        start_time = int((min(created).timestamp() - 5) * 1_000_000)
        fills = await self.fills_since(client, set(product_ids), start_time)
        by_order: dict[str, list[dict[str, Any]]] = {}
        for fill in fills:
            by_order.setdefault(str(fill.get("order_id") or ""), []).append(fill)

        reconciled: list[dict[str, Any]] = []
        for order in orders:
            order_fills = by_order.get(str(order.get("delta_order_id") or ""), [])
            if order_fills:
                filled_size = sum((decimal_value(fill.get("size")) for fill in order_fills), Decimal("0"))
                notional = sum(
                    (decimal_value(fill.get("price")) * decimal_value(fill.get("size")) for fill in order_fills),
                    Decimal("0"),
                )
                average_fill_price = notional / filled_size if filled_size else Decimal("0")
                commission = sum((decimal_value(fill.get("commission")) for fill in order_fills), Decimal("0"))
                slippage = slippage_fields(str(order.get("side")), order.get("reference_price"), average_fill_price)
                original = order
                order = {
                    **order,
                    "filled_size": str(filled_size),
                    "average_fill_price": str(average_fill_price),
                    "commission": str(commission),
                    **slippage,
                }
                values = {
                    "filled_size": str(filled_size),
                    "average_fill_price": str(average_fill_price),
                    "commission": str(commission),
                    **slippage,
                }
                # Compare against the stored row, before replacing it with reconciled values.
                required = {key: values[key] for key in ("filled_size", "average_fill_price", "commission")}
                if any(
                    optional_decimal(original.get(key)) != optional_decimal(value) for key, value in required.items()
                ):
                    await self.db.update("execution_orders", required, {"id": f"eq.{order['id']}"})
                if any(
                    optional_decimal(original.get(key)) != optional_decimal(value) for key, value in slippage.items()
                ):
                    await self.write_audit("execution_orders", slippage, {"id": f"eq.{order['id']}"})
            reconciled.append(order)
        return reconciled

    async def contract_value(self, client: DeltaClient, symbol: str) -> Decimal:
        if symbol not in self.contract_values:
            product = await self.product_spec(client, symbol)
            value = decimal_value(product.get("contract_value"))
            if value <= 0:
                raise AppError(502, f"Contract value is unavailable for {symbol}", "contract_value_unavailable")
            self.contract_values[symbol] = value
        return self.contract_values[symbol]

    async def product_spec(self, client: DeltaClient, symbol: str) -> dict[str, Any]:
        if symbol not in self.product_specs:
            try:
                response = await client.product(symbol)
                product = response.get("result") or {}
            except AppError as error:
                if error.code not in UNAVAILABLE_CONTRACT_CODES and error.status != 404:
                    raise
                product = {}
            if not product:
                after: str | None = None
                seen: set[str] = set()
                while True:
                    response = await client.products(
                        {
                            "contract_types": "call_options,put_options",
                            "states": "expired,settled",
                            "page_size": 100,
                            "after": after,
                        }
                    )
                    for item in response.get("result") or []:
                        item_symbol = str(item.get("symbol") or "")
                        if item_symbol:
                            self.product_specs[item_symbol] = item
                    if symbol in self.product_specs:
                        return self.product_specs[symbol]
                    next_after = str((response.get("meta") or {}).get("after") or "")
                    if not next_after or next_after in seen:
                        break
                    seen.add(next_after)
                    after = next_after
                raise AppError(502, f"Product specification is unavailable for {symbol}", "product_spec_unavailable")
            self.product_specs[symbol] = product
        return self.product_specs[symbol]

    async def run_executions(self, strategy_id: str) -> list[dict[str, Any]]:
        return await self.db.select(
            "executions",
            {
                "select": "id,kind,status,error,started_at,completed_at",
                "strategy_id": f"eq.{strategy_id}",
                "order": "started_at.asc",
            },
        )

    async def run_orders(
        self, strategy_id: str, executions: list[dict[str, Any]] | None = None
    ) -> list[dict[str, Any]]:
        """Every order the run placed, entry and exit, tagged with its phase."""
        executions = executions if executions is not None else await self.run_executions(strategy_id)
        kinds = {str(item["id"]): str(item["kind"]) for item in executions}
        if not kinds:
            return []
        rows = await self.db.select(
            "execution_orders",
            {"select": "*", "execution_id": f"in.({','.join(kinds)})", "order": "created_at.asc"},
        )
        return [{**row, "kind": kinds.get(str(row["execution_id"]), "entry")} for row in rows]

    async def enrich_contract_values(self, client: DeltaClient, orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Backfill the per-lot contract value on recorded orders so premium maths
        is exact. Delta's product endpoint is public and the result is cached, so
        this costs at most one request per symbol per process.
        """
        resolved: dict[str, Decimal] = {}
        for symbol in sorted({str(order["product_symbol"]) for order in orders if not order.get("contract_value")}):
            try:
                resolved[symbol] = await self.contract_value(client, symbol)
            except Exception as exc:
                logger.warning("Could not read the contract value for %s: %s", symbol, exc)

        enriched: list[dict[str, Any]] = []
        for order in orders:
            value = resolved.get(str(order.get("product_symbol")))
            if order.get("contract_value") or value is None:
                enriched.append(order)
                continue
            if order.get("id"):
                await self.write_audit("execution_orders", {"contract_value": str(value)}, {"id": f"eq.{order['id']}"})
            enriched.append({**order, "contract_value": str(value)})
        return enriched

    async def record_settlement(self, client: DeltaClient, strategy_id: str) -> dict[str, Any] | None:
        """
        Persist the money view of a finished run. Reporting only: a failure here
        is logged and never changes the execution outcome.
        """
        try:
            orders = await self.enrich_contract_values(client, await self.run_orders(strategy_id))
            summary = settlement_summary(orders)
        except Exception as exc:
            logger.warning("Could not summarise the settlement for strategy %s: %s", strategy_id, exc)
            return None
        summary["settledAt"] = iso_now()
        await self.write_audit(
            "strategies",
            {
                "result_json": summary,
                "realized_pnl": summary["realizedPnl"] if decimal_value(summary["closedLots"]) > 0 else None,
            },
            {"id": f"eq.{strategy_id}"},
        )
        return summary

    async def finalize_exchange_settlement(
        self,
        client: DeltaClient,
        row: dict[str, Any],
        entry_orders: list[dict[str, Any]],
        fills: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        strategy_id = str(row["id"])
        enriched = await self.enrich_contract_values(client, entry_orders)
        contract_values = {
            int(order["product_id"]): optional_decimal(order.get("contract_value")) for order in enriched
        }
        if any(not value or value <= 0 for value in contract_values.values()):
            return None

        remaining: dict[int, Decimal] = {}
        for order in enriched:
            product_id = int(order["product_id"])
            filled = decimal_value(order.get("filled_size"))
            if filled <= 0 and str(order.get("state")) == "closed":
                filled = decimal_value(order.get("size"))
            remaining[product_id] = remaining.get(product_id, Decimal("0")) + (
                filled if order.get("side") == "buy" else -filled
            )

        valid: list[tuple[dict[str, Any], int, str]] = []
        for fill in sorted(fills, key=lambda item: (str(item.get("created_at") or ""), str(item.get("id") or ""))):
            size = decimal_value(fill.get("size"))
            side = str(fill.get("side") or "")
            product_id = int(fill.get("product_id") or 0)
            if size <= 0 or size != size.to_integral_value() or side not in {"buy", "sell"}:
                continue
            if product_id not in contract_values:
                continue
            owned = remaining.get(product_id, Decimal("0"))
            if owned == 0 or (owned > 0 and side != "sell") or (owned < 0 and side != "buy"):
                continue
            allocated = min(size, abs(owned))
            commission = decimal_value(fill.get("commission")) * allocated / size
            fill = {
                **fill,
                "exchange_size": str(size),
                "size": str(allocated),
                "commission": str(commission),
            }
            valid.append((fill, int(allocated), settlement_client_order_id(strategy_id, fill)))
            remaining[product_id] = owned - allocated if owned > 0 else owned + allocated
        if not valid or any(size != 0 for size in remaining.values()):
            return None

        client_ids = [client_id for _, _, client_id in valid]
        existing = await self.db.select(
            "execution_orders",
            {
                "select": "client_order_id,execution_id",
                "client_order_id": f"in.({','.join(client_ids)})",
            },
        )
        existing_ids = {str(item["client_order_id"]): str(item["execution_id"]) for item in existing}
        execution_id = next(iter(existing_ids.values()), None)
        completed = max((str(fill.get("created_at") or "") for fill, _, _ in valid), default="") or iso_now()
        if execution_id is None:
            executions = await self.db.insert(
                "executions",
                {"strategy_id": strategy_id, "kind": "exit", "status": "running"},
            )
            if not executions:
                raise AppError(500, "Could not record the Delta settlement", "settlement_record_failed")
            execution_id = str(executions[0]["id"])

        for fill, size, client_id in valid:
            if client_id in existing_ids:
                continue
            product_id = int(fill["product_id"])
            await self.record_order(
                {
                    "execution_id": execution_id,
                    "leg_id": "settlement",
                    "delta_order_id": str(fill.get("order_id") or fill.get("id") or ""),
                    "client_order_id": client_id,
                    "product_id": product_id,
                    "product_symbol": str(fill.get("product_symbol") or "unknown"),
                    "side": str(fill["side"]),
                    "size": size,
                    "filled_size": str(size),
                    "average_fill_price": str(fill.get("price") or "0"),
                    "commission": str(decimal_value(fill.get("commission"))),
                    "state": "settled",
                    "order_type": "settlement",
                    "contract_value": str(contract_values[product_id]),
                    "response_json": {"success": True, "result": fill},
                }
            )

        await self.db.update(
            "executions",
            {"status": "completed", "error": None, "completed_at": completed},
            {"id": f"eq.{execution_id}"},
        )
        summary = settlement_summary(await self.run_orders(strategy_id))
        reconciled_at = iso_now()
        summary.update(
            {
                "settledAt": completed,
                "closureReason": "exchange_settlement",
                "reconciledAt": reconciled_at,
                "exchangeSettlementFillIds": [str(fill.get("id") or fill.get("order_id")) for fill, _, _ in valid],
            }
        )
        risk_state = {
            **(row.get("risk_state") or {}),
            "exposureStatus": "flat",
            "closureReason": "exchange_settlement",
            "reconciledAt": reconciled_at,
        }
        updated = await self.db.update(
            "strategies",
            {
                "status": "completed",
                "exit_execution_at": completed,
                "last_error": None,
                "risk_state": risk_state,
                "result_json": summary,
                "realized_pnl": summary["realizedPnl"],
            },
            {"id": f"eq.{strategy_id}"},
        )
        if not updated:
            raise AppError(500, "Could not finalize the Delta settlement", "settlement_finalize_failed")
        await self.release_capital_slot(str(row["user_id"]), strategy_id)
        logger.info("Reconciled Delta settlement strategy_id=%s user_id=%s", strategy_id, row["user_id"])
        return summary

    async def reconcile_run_if_flat(
        self,
        row: dict[str, Any],
        client: DeltaClient,
        snapshot: AccountExposure | None = None,
        entry_orders: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any] | None:
        entry_orders = entry_orders if entry_orders is not None else await self.entry_orders(str(row["id"]))
        if not entry_orders:
            return None
        if any(order.get("state") == "unknown" for order in entry_orders):
            entry_orders = await self.reconcile_entry_fills(client, entry_orders)
        product_ids = {int(order["product_id"]) for order in entry_orders}
        risk_state = dict(row.get("risk_state") or {})
        closed_accounting_only = (
            risk_state.get("exclusiveFillAccounting")
            and risk_state.get("exposureStatus") == "flat"
            and row.get("exit_execution_at")
        )
        if not closed_accounting_only:
            snapshot = snapshot or await self.account_exposure(client)
            positions = dict(snapshot.positions)
            for product_id in product_ids:
                if positions.get(product_id, Decimal("0")) != 0:
                    continue
                try:
                    positions[product_id] = await self.live_position_size(client, product_id)
                except AppError as error:
                    if error.code not in UNAVAILABLE_CONTRACT_CODES:
                        raise
            current = AccountExposure(positions=positions, open_orders=snapshot.open_orders)
            if has_exchange_exposure(product_ids, current):
                if risk_state.get("exposureStatus") != "open":
                    risk_state.update({"exposureStatus": "open", "reconciledAt": iso_now()})
                    await self.db.update(
                        "strategies",
                        {"risk_state": risk_state},
                        {"id": f"eq.{row['id']}"},
                    )
                return None

        owned = await self.owned_fill_positions(row, client, entry_orders)
        account_fills: list[dict[str, Any]] | None = None
        if owned is None:
            enriched = await self.enrich_contract_values(client, entry_orders)
            created = [
                datetime.fromisoformat(str(order["created_at"]).replace("Z", "+00:00")) for order in entry_orders
            ]
            account_fills = await self.fills_since(client, product_ids, int((min(created).timestamp() - 5) * 1_000_000))
            entry_ids = {str(order.get("delta_order_id")) for order in entry_orders}
            first = min(
                (str(fill.get("created_at") or "") for fill in account_fills if str(fill.get("order_id")) in entry_ids),
                default="",
            )
            scoped = [fill for fill in account_fills if str(fill.get("created_at") or "") >= first]
            if first and any(
                str(fill.get("order_id")) not in entry_ids and fill.get("fill_type") == "normal" for fill in scoped
            ):
                try:
                    allocation = exclusive_fill_positions(enriched, scoped)
                    # A flat account plus a complete zero-sum interval proves this
                    # historical allocation without inventing an initial position.
                    if allocation and all(position.remaining == 0 for position in allocation.values()):
                        owned = allocation
                except AppError as error:
                    logger.warning(
                        "Historical fill allocation unresolved strategy_id=%s code=%s", row["id"], error.code
                    )
        if owned is not None and all(position.remaining == 0 for position in owned.values()):
            summary = self.owned_settlement(owned)
            completed = max(
                (str(fill["created_at"]) for position in owned.values() for fill in position.exit_fills),
                default=iso_now(),
            )
            summary.update({"settledAt": completed, "closureReason": "exchange_fills", "reconciledAt": iso_now()})
            updated = await self.db.update(
                "strategies",
                {
                    "status": "completed" if summary["accountingComplete"] else "attention",
                    "exit_execution_at": completed,
                    "last_error": None
                    if summary["accountingComplete"]
                    else "Positions closed; final commissions pending",
                    "result_json": summary,
                    "realized_pnl": summary["realizedPnl"],
                    "risk_state": {**risk_state, "exposureStatus": "flat", "reconciledAt": iso_now()},
                },
                {"id": f"eq.{row['id']}"},
            )
            if not updated:
                raise AppError(503, "Could not finalize owned fill accounting", "settlement_finalize_failed")
            if getattr(client, "order_journal", None) is not None:
                await client.order_journal.call("orderIntents:releaseProducts", {"strategyId": str(row["id"])})
            await self.release_capital_slot(str(row["user_id"]), str(row["id"]))
            return {
                "verified": True,
                "ordersSubmitted": 0,
                "positionsVerified": len(product_ids),
                "closureReason": "exchange_fills",
                "settlement": summary,
            }

        created = [datetime.fromisoformat(str(order["created_at"]).replace("Z", "+00:00")) for order in entry_orders]
        start_time = int((min(created).timestamp() - 5) * 1_000_000)
        fills = account_fills if account_fills is not None else await self.fills_since(client, product_ids, start_time)
        settlements = [fill for fill in fills if str(fill.get("fill_type")) == "settlement"]
        if settlements:
            summary = await self.finalize_exchange_settlement(client, row, entry_orders, settlements)
            if summary is not None:
                return {
                    "verified": True,
                    "ordersSubmitted": 0,
                    "positionsVerified": len(product_ids),
                    "closureReason": "exchange_settlement",
                    "settlement": summary,
                }

        reconciled_at = iso_now()
        risk_state.update({"exposureStatus": "flat", "closureReason": "exchange_flat", "reconciledAt": reconciled_at})
        message = "Delta confirms this strategy has no open position or order; settlement P&L is unavailable"
        await self.release_capital_slot(str(row["user_id"]), str(row["id"]))
        await self.db.update(
            "strategies",
            {"status": "attention", "last_error": message, "risk_state": risk_state},
            {"id": f"eq.{row['id']}"},
        )
        logger.warning("Released flat unresolved strategy_id=%s user_id=%s", row["id"], row["user_id"])
        return {
            "verified": True,
            "ordersSubmitted": 0,
            "positionsVerified": len(product_ids),
            "closureReason": "exchange_flat",
            "settlement": None,
        }

    async def reconcile_attention_runs(
        self,
        user_id: str | None = None,
        client: DeltaClient | None = None,
    ) -> None:
        params = {
            "select": "*",
            "status": "eq.attention",
            "limit": "100",
        }
        if user_id is not None:
            params["user_id"] = f"eq.{user_id}"
        rows = await self.strategy_pages(params)
        groups: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            risk_state = row.get("risk_state") or {}
            if not row.get("entry_execution_at") and not risk_state.get("exclusiveFillAccounting"):
                continue
            if risk_state.get("exposureStatus") == "flat" and risk_state.get("reconciledAt"):
                try:
                    reconciled_at = datetime.fromisoformat(str(risk_state["reconciledAt"]).replace("Z", "+00:00"))
                except ValueError:
                    reconciled_at = None
                if reconciled_at and reconciled_at.utcoffset() is None:
                    reconciled_at = None
                if reconciled_at and (utc_now() - reconciled_at).total_seconds() < FLAT_REPORT_RECONCILE_SECONDS:
                    continue
            groups.setdefault(str(row["user_id"]), []).append(row)
        for owner, owned_rows in groups.items():
            owned_client: DeltaClient | None = None
            try:
                owned_client = client if client is not None and owner == user_id else await self.client_for_user(owner)
                snapshot = await self.account_exposure(owned_client)
                for row in owned_rows:
                    try:
                        await self.recover_order_records(row, owned_client)
                        if await self.resolve_unsubmitted_run(row, owned_client):
                            continue
                        result = await self.reconcile_run_if_flat(row, owned_client, snapshot)
                        state = row.get("risk_state") or {}
                        if result is None and state.get("exclusiveFillAccounting") and state.get("exitRequested"):
                            await self.execute_exit(str(row["id"]))
                    except Exception:
                        logger.exception("Could not reconcile attention strategy %s", row["id"])
            except Exception:
                logger.exception("Could not load Delta exposure for user %s", owner)
            finally:
                if owned_client is not None and owned_client is not client:
                    await owned_client.close()

    async def run_detail(self, strategy_id: str, user_id: str) -> dict[str, Any]:
        """
        Everything recorded about a single run: schedule, criteria, per-leg
        fills with slippage, settlement, risk monitor state, and raw responses.
        """
        rows = await self.db.select(
            "strategies",
            {"select": "*", "id": f"eq.{strategy_id}", "user_id": f"eq.{user_id}", "limit": "1"},
        )
        if not rows:
            raise AppError(404, "Strategy not found", "strategy_not_found")
        row = rows[0]
        executions = await self.run_executions(strategy_id)
        orders = await self.run_orders(strategy_id, executions)
        if orders and any(not order.get("contract_value") for order in orders):
            client = DeltaClient(self.settings)
            try:
                orders = await self.enrich_contract_values(client, orders)
            finally:
                await client.close()

        stored = row.get("result_json") or {}
        settlement = dict(stored)
        if orders and stored.get("accountingBasis") != "allocated_exchange_fills":
            settlement.update(settlement_summary(orders))
        return {
            "id": row["id"],
            "name": row["name"],
            "status": row["status"],
            "createdAt": row.get("created_at"),
            "updatedAt": row.get("updated_at"),
            "entryAt": row.get("entry_at"),
            "exitAt": row.get("exit_at"),
            "entryExecutedAt": row.get("entry_execution_at"),
            "exitExecutedAt": row.get("exit_execution_at"),
            "lastError": row.get("last_error"),
            "definition": row.get("definition_json") or {},
            "savedStrategyId": row.get("saved_strategy_id"),
            "capitalSlot": row.get("capital_slot"),
            "capitalBudget": str(row.get("capital_budget")) if row.get("capital_budget") is not None else None,
            "capitalPolicy": row.get("capital_policy_json") or {},
            "riskState": row.get("risk_state") or {},
            "riskMonitoredAt": row.get("risk_monitor_at"),
            "combinedStopTriggeredAt": row.get("combined_stop_triggered_at"),
            "settlement": settlement,
            "executions": [
                {
                    "id": item["id"],
                    "kind": item["kind"],
                    "status": item["status"],
                    "error": item.get("error"),
                    "startedAt": item.get("started_at"),
                    "completedAt": item.get("completed_at"),
                }
                for item in executions
            ],
            "orders": [
                {
                    "id": order["id"],
                    "kind": order.get("kind"),
                    "legId": order.get("leg_id"),
                    "deltaOrderId": order.get("delta_order_id"),
                    "clientOrderId": order.get("client_order_id"),
                    "productId": order.get("product_id"),
                    "productSymbol": order.get("product_symbol"),
                    "side": order.get("side"),
                    "size": str(order.get("size")),
                    "filledSize": str(order.get("filled_size") or "0"),
                    "averageFillPrice": order.get("average_fill_price"),
                    "referencePrice": order.get("reference_price"),
                    "slippage": order.get("slippage"),
                    "slippagePercent": order.get("slippage_percent"),
                    "contractValue": order.get("contract_value"),
                    "orderType": order.get("order_type"),
                    "limitPrice": order.get("limit_price"),
                    "commission": str(order.get("commission") or "0"),
                    "state": order.get("state"),
                    "createdAt": order.get("created_at"),
                    "response": order.get("response_json") or {},
                }
                for order in orders
            ],
        }

    async def delete_strategy(self, strategy_id: str, user_id: str) -> None:
        """
        Remove a finished run and its execution audit trail. Refused while the
        run could still hold an open Delta position, because deleting the record
        would leave that position untracked.
        """
        rows = await self.db.select(
            "strategies",
            {
                "select": "id,status,entry_execution_at,exit_execution_at,risk_state",
                "id": f"eq.{strategy_id}",
                "user_id": f"eq.{user_id}",
                "limit": "1",
            },
        )
        if not rows:
            raise AppError(404, "Strategy not found", "strategy_not_found")
        row = rows[0]
        status = str(row["status"])
        if status in {"executing_entry", "executing_exit"}:
            raise AppError(409, "This run is executing right now. Wait for it to finish", "cannot_delete_running")
        if status == "active":
            raise AppError(409, "Exit this live run before deleting it from history", "cannot_delete_live")
        exposure_status = (row.get("risk_state") or {}).get("exposureStatus")
        if (
            status == "attention"
            and row.get("entry_execution_at")
            and not row.get("exit_execution_at")
            and exposure_status != "flat"
        ):
            raise AppError(
                409,
                "This run entered but never confirmed an exit, so it may still hold a position. "
                "Exit it before deleting the record",
                "cannot_delete_unresolved",
            )
        if not await self.db.delete(
            "strategies", {"select": "id", "id": f"eq.{strategy_id}", "user_id": f"eq.{user_id}"}
        ):
            raise AppError(409, "The run could not be deleted", "delete_failed")

    async def owned_fill_positions(
        self,
        row: dict[str, Any],
        client: DeltaClient,
        orders: list[dict[str, Any]],
    ) -> dict[int, PositionResult] | None:
        if not (row.get("risk_state") or {}).get("exclusiveFillAccounting"):
            return None
        if (
            client.order_journal is None
            or (row.get("risk_state") or {}).get("exchangeAccount") != client.order_journal.account_id
        ):
            raise AppError(
                409, "Run belongs to a different exchange account or journal is disabled", "exchange_account_mismatch"
            )
        if not orders:
            raise AppError(409, "Entry records missing", "fill_ownership_unknown")
        entries = await self.enrich_contract_values(client, orders)
        start = min(datetime.fromisoformat(str(order["created_at"]).replace("Z", "+00:00")) for order in entries)
        fills = await self.fills_since(
            client, {int(order["product_id"]) for order in entries}, int((start.timestamp() - 5) * 1_000_000)
        )
        entry_ids = {str(order["delta_order_id"]) for order in entries}
        first = min((str(fill["created_at"]) for fill in fills if str(fill.get("order_id")) in entry_ids), default=None)
        if first is None and any(decimal_value(order.get("filled_size")) != 0 for order in entries):
            raise AppError(409, "Entry fills unavailable", "fills_incomplete")
        first = first or start.isoformat()
        confirmed_closed = (row.get("risk_state") or {}).get("exposureStatus") == "flat" and row.get(
            "exit_execution_at"
        )
        scoped = [
            fill
            for fill in fills
            if str(fill.get("created_at") or "") >= first
            and (not confirmed_closed or str(fill.get("created_at") or "") <= str(row["exit_execution_at"]))
        ]
        positions = exclusive_fill_positions(entries, scoped)
        if client.order_journal is not None:
            account = client.order_journal.account_id
            changed = [
                fill
                for fill in scoped
                if self.synced_fills.get((account, str(fill["id"])))
                != json.dumps(fill, sort_keys=True, separators=(",", ":"))
            ]
            if changed:
                await client.order_journal.ingest_fills(changed)
                for fill in changed:
                    key = (account, str(fill["id"]))
                    self.synced_fills[key] = json.dumps(fill, sort_keys=True, separators=(",", ":"))
                    self.synced_fills.move_to_end(key)
                while len(self.synced_fills) > 5000:
                    self.synced_fills.popitem(last=False)
        if confirmed_closed and all(position.remaining == 0 for position in positions.values()):
            return positions
        for product_id, position in positions.items():
            try:
                live_size = await self.live_position_size(client, product_id)
            except AppError as error:
                if error.code not in UNAVAILABLE_CONTRACT_CODES or position.remaining:
                    raise
                snapshot = await self.account_exposure(client)
                if has_exchange_exposure({product_id}, snapshot):
                    raise AppError(409, "Expired product still has exposure", "fill_ownership_unknown") from error
                live_size = Decimal("0")
            if live_size != position.remaining:
                raise AppError(409, "Fill history and account position disagree", "fill_ownership_unknown")
        return positions

    async def recover_order_records(self, row: dict[str, Any], client: DeltaClient) -> None:
        if not (row.get("risk_state") or {}).get("exclusiveFillAccounting"):
            return
        journal = client.order_journal
        if journal is None or journal.account_id != row["risk_state"].get("exchangeAccount"):
            raise AppError(409, "Recovery requires the original exchange account", "exchange_account_mismatch")
        for intent in await journal.strategy_intents(str(row["id"])):
            if intent.get("materialized"):
                continue
            context = intent["context"]
            executions = await self.db.select(
                "executions",
                {
                    "select": "id",
                    "id": f"eq.{context['executionId']}",
                    "strategy_id": f"eq.{row['id']}",
                },
            )
            if not executions:
                raise AppError(409, "Journal execution does not belong to this strategy", "journal_execution_missing")
            outcome = await journal.known_outcome(intent, client.order_by_client_id)
            payload = json.loads(intent["payload"])
            response = json.loads(outcome["response"]) if outcome["kind"] == "accepted" else None
            result = response["result"] if response else {}
            size = decimal_value(payload["size"])
            unfilled = optional_decimal(result.get("unfilled_size"))
            if response and (unfilled is None or unfilled < 0 or unfilled > size):
                raise AppError(502, "Recovered order quantity is unconfirmed", "order_outcome_unknown")
            record = {
                "execution_id": context["executionId"],
                "leg_id": context["legId"],
                "client_order_id": intent["clientOrderId"],
                "product_id": payload["product_id"],
                "product_symbol": payload["product_symbol"],
                "side": payload["side"],
                "size": int(size),
                "delta_order_id": str(result["id"]) if response else None,
                "filled_size": str(size - unfilled) if unfilled is not None else "0",
                "state": str(result.get("state") or "submitted") if response else "failed",
                "average_fill_price": result.get("average_fill_price"),
                "commission": result.get("paid_commission") or result.get("commission") or "0",
                "order_type": payload["order_type"],
                "limit_price": payload.get("limit_price"),
                "contract_value": context.get("contractValue"),
                "reference_price": context.get("referencePrice"),
                "response_json": response or {"error": outcome["code"]},
                "created_at": datetime.fromtimestamp(intent["_creationTime"] / 1000, UTC).isoformat(),
            }
            saved = await self.db.upsert("execution_orders", record, on_conflict="client_order_id")
            if not saved:
                raise AppError(503, "Recovered order was not saved", "order_record_failed")
            await journal.call("orderIntents:materialized", {"clientOrderId": intent["clientOrderId"]})

    async def recover_interrupted_states(self) -> None:
        if not getattr(self.settings, "convex_order_journal_enabled", False):
            return
        rows = await self.strategy_pages({"select": "id,risk_state", "status": "in.(executing_entry,executing_exit)"})
        for row in rows:
            if str(row["id"]) in self.running_operations:
                continue
            state = row.get("risk_state") or {}
            if state.get("exclusiveFillAccounting"):
                await self.db.update(
                    "strategies",
                    {
                        "status": "attention",
                        "last_error": "Interrupted execution; reconciling before recovery",
                        "risk_state": {**state, "exitRequested": True},
                    },
                    {"id": f"eq.{row['id']}", "status": "in.(executing_entry,executing_exit)"},
                )
        self.startup_recovered = True

    async def resolve_unsubmitted_run(self, row: dict[str, Any], client: DeltaClient) -> bool:
        state = row.get("risk_state") or {}
        if not state.get("exclusiveFillAccounting"):
            return False
        journal = client.order_journal
        if journal is None or journal.account_id != state.get("exchangeAccount"):
            raise AppError(409, "Recovery requires the original exchange account", "exchange_account_mismatch")
        intents = await journal.strategy_intents(str(row["id"]))
        if any(intent["outcome"]["kind"] != "rejected" for intent in intents):
            return False
        product_ids = {int(value) for value in state.get("plannedProductIds", [])}
        if not product_ids or has_exchange_exposure(product_ids, await self.account_exposure(client)):
            return False
        if any(await asyncio.gather(*(self.live_position_size(client, product_id) for product_id in product_ids))):
            return False
        await self.db.update(
            "strategies",
            {
                "status": "cancelled",
                "last_error": "Entry did not establish exposure",
                "risk_state": {**state, "exposureStatus": "flat", "reconciledAt": iso_now()},
            },
            {"id": f"eq.{row['id']}", "status": "eq.attention"},
        )
        await journal.call("orderIntents:releaseProducts", {"strategyId": str(row["id"])})
        await self.release_capital_slot(str(row["user_id"]), str(row["id"]))
        return True

    @staticmethod
    def owned_settlement(positions: dict[int, PositionResult]) -> dict[str, Any]:
        gross = sum((position.realized_gross for position in positions.values()), Decimal("0"))
        fees = sum((position.fees for position in positions.values()), Decimal("0"))
        fees_complete = all(position.fees_complete for position in positions.values())
        by_symbol = []
        entry_total = exit_total = Decimal("0")
        for position in positions.values():
            direction = Decimal("1") if position.side == "sell" else Decimal("-1")
            entry = direction * position.entry_price * position.entry_size * position.multiplier
            closed = position.entry_size - abs(position.remaining)
            exit_cash = position.realized_gross - direction * position.entry_price * closed * position.multiplier
            entry_total += entry
            exit_total += exit_cash
            by_symbol.append(
                {
                    "symbol": position.symbol,
                    "entryPremium": str(entry),
                    "exitPremium": str(exit_cash),
                    "commission": str(position.fees) if position.fees_complete else None,
                    "entryLots": str(position.entry_size),
                    "exitLots": str(closed),
                    "realizedPnl": str(position.realized_gross - position.fees) if position.fees_complete else None,
                }
            )
        return {
            "entryPremium": str(entry_total),
            "exitPremium": str(exit_total),
            "bySymbol": by_symbol,
            "accountingBasis": "allocated_exchange_fills",
            "grossPnl": str(gross),
            "accountingComplete": fees_complete,
            "commission": str(fees) if fees_complete else None,
            "realizedPnl": str(gross - fees) if fees_complete else None,
            "filledLots": str(sum((position.entry_size for position in positions.values()), Decimal("0"))),
            "requestedLots": str(sum((position.entry_size for position in positions.values()), Decimal("0"))),
            "closedLots": str(
                sum((position.entry_size - abs(position.remaining) for position in positions.values()), Decimal("0"))
            ),
            "fullyClosed": all(position.remaining == 0 for position in positions.values()),
        }

    async def monitor_combined_strategy(self, row: dict[str, Any]) -> bool:
        definition = StrategyDefinition.model_validate(row["definition_json"])
        if definition.riskMode not in {"combined_premium", "strategy_level"} and not (row.get("risk_state") or {}).get(
            "exclusiveFillAccounting"
        ):
            return False
        client = await self.client_for_user(str(row["user_id"]))
        try:
            orders = await self.entry_orders(str(row["id"]))
            if not (row.get("risk_state") or {}).get("exclusiveFillAccounting") or any(
                decimal_value(order.get("filled_size")) < decimal_value(order["size"]) for order in orders
            ):
                orders = await self.reconcile_entry_fills(client, orders)
            owned = await self.owned_fill_positions(row, client, orders)
            ready = {order["leg_id"] for order in orders} == {leg.id for leg in definition.legs} and all(
                decimal_value(order.get("filled_size")) >= decimal_value(order.get("size")) for order in orders
            )
            if not ready and row.get("entry_execution_at"):
                started = datetime.fromisoformat(str(row["entry_execution_at"]).replace("Z", "+00:00"))
                if (utc_now() - started).total_seconds() >= getattr(self.settings, "entry_fill_deadline_seconds", 60):
                    return await self.claim_risk_exit(
                        str(row["id"]), row.get("risk_state") or {}, "partial_entry_timeout"
                    )
            risk_state: dict[str, Any] = {
                **(row.get("risk_state") or {}),
                "mode": "strategy_level",
                "status": "armed" if ready else "awaiting_fills",
                "riskBasis": definition.riskBasis,
                "stopPercent": str(definition.stopLossPercent),
                "takeProfitPercent": str(definition.takeProfitPercent),
                "legs": [
                    {
                        "legId": order["leg_id"],
                        "symbol": order["product_symbol"],
                        "filledSize": str(order.get("filled_size") or "0"),
                        "requestedSize": str(order["size"]),
                    }
                    for order in orders
                ],
            }
            if owned and all(position.remaining == 0 for position in owned.values()):
                await self.reconcile_run_if_flat(row, client, entry_orders=orders)
                return False
            if owned and definition.squareOff == "complete" and any(position.exit_fills for position in owned.values()):
                return await self.claim_risk_exit(str(row["id"]), risk_state, "external_leg_exit")
            if definition.riskMode == "legwise":
                return False
            if not ready:
                await self.db.update(
                    "strategies",
                    {"risk_state": risk_state, "risk_monitor_at": iso_now()},
                    {"id": f"eq.{row['id']}", "status": "eq.active"},
                )
                return False

            open_orders = [order for order in orders if owned is None or owned[int(order["product_id"])].remaining]
            market_data = await asyncio.gather(
                *(
                    (client.risk_ticker if isinstance(client, DeltaClient) else client.ticker)(
                        str(order["product_symbol"])
                    )
                    for order in open_orders
                ),
                *(self.contract_value(client, str(order["product_symbol"])) for order in orders),
            )
            ticker_results = market_data[: len(open_orders)]
            tickers = {
                str(order["product_symbol"]): ticker for order, ticker in zip(open_orders, ticker_results, strict=True)
            }
            contract_values = market_data[len(open_orders) :]
            if any(decimal_value(ticker.get("result", {}).get("mark_price")) <= 0 for ticker in ticker_results):
                risk_state.update({"status": "price_unavailable", "message": "A live mark price is unavailable"})
                await self.db.update(
                    "strategies",
                    {"risk_state": risk_state, "risk_monitor_at": iso_now()},
                    {"id": f"eq.{row['id']}", "status": "eq.active"},
                )
                raise AppError(503, "A risk mark price is unavailable", "risk_price_unavailable")
            priced_legs: list[dict[str, Any]] = []
            for order, multiplier in zip(orders, contract_values, strict=True):
                position = owned.get(int(order["product_id"])) if owned is not None else None
                ticker = tickers.get(str(order["product_symbol"]))
                mark = decimal_value(ticker.get("result", {}).get("mark_price")) if ticker else Decimal("0")
                payoff = {}
                if definition.riskBasis == "defined_max_loss":
                    specification = await self.product_spec(client, str(order["product_symbol"]))
                    leg = next(leg for leg in definition.legs if leg.id == order["leg_id"])
                    payoff = {
                        "strike": specification.get("strike_price"),
                        "option_type": leg.optionType,
                        "expiry": leg.expiry.isoformat(),
                    }
                priced_legs.append(
                    {
                        "side": order["side"],
                        "filled_size": position.entry_size if position is not None else order["filled_size"],
                        "entry_price": position.entry_price if position is not None else order["average_fill_price"],
                        "mark_price": mark,
                        "contract_value": multiplier,
                        **payoff,
                        **(
                            {
                                "remaining_size": abs(position.remaining),
                                "realized_gross": position.realized_gross,
                                "remaining_entry_price": position.open_price,
                            }
                            if position is not None
                            else {}
                        ),
                    }
                )
            metrics = strategy_level_metrics(
                priced_legs,
                risk_basis=definition.riskBasis,
                stop_percent=decimal_value(definition.stopLossPercent),
                take_profit_percent=decimal_value(definition.takeProfitPercent),
            )
            entry_value = metrics["entry_value"]
            current_value = metrics["current_value"]
            if not isinstance(entry_value, Decimal) or entry_value <= 0:
                risk_state.update({"status": "attention", "message": "The strategy entry risk basis is not positive"})
                await self.db.update(
                    "strategies",
                    {"risk_state": risk_state, "risk_monitor_at": iso_now()},
                    {"id": f"eq.{row['id']}", "status": "eq.active"},
                )
                return False

            risk_state.update(
                {
                    "entryValue": str(entry_value),
                    "currentValue": str(current_value),
                    "currentValueLabel": metrics["current_label"],
                    "stopValue": str(metrics["stop_value"]),
                    "targetValue": str(metrics["target_value"]),
                    "profit": str(metrics["profit"]),
                    "returnPercent": str(Decimal(str(metrics["profit"])) / entry_value * 100),
                    "pricedAt": iso_now(),
                }
            )
            trigger_reason = (
                "stop_loss" if metrics["stop_triggered"] else "take_profit" if metrics["target_triggered"] else None
            )
            if not trigger_reason:
                last_report = row.get("risk_monitor_at")
                last_report_at = (
                    datetime.fromisoformat(str(last_report).replace("Z", "+00:00")) if last_report else None
                )
                interval = getattr(self.settings, "risk_state_persist_seconds", 10)
                if last_report_at is None or (utc_now() - last_report_at).total_seconds() >= interval:
                    await self.db.update(
                        "strategies",
                        {"risk_state": risk_state, "risk_monitor_at": iso_now()},
                        {"id": f"eq.{row['id']}", "status": "eq.active"},
                    )
                return False
            return await self.claim_risk_exit(str(row["id"]), risk_state, trigger_reason)
        finally:
            await client.close()

    async def claim_risk_exit(self, strategy_id: str, state: dict[str, Any], reason: str) -> bool:
        claimed = await self.db.update(
            "strategies",
            {
                "status": "executing_exit",
                "combined_stop_triggered_at": iso_now(),
                "risk_state": {**state, "status": "triggered", "exitReason": reason, "exitRequested": True},
            },
            {"select": "id", "id": f"eq.{strategy_id}", "status": "eq.active", "combined_stop_triggered_at": "is.null"},
        )
        return bool(claimed)

    async def process_active_risks(self) -> None:
        active = await self.strategy_pages(
            {
                "select": "id,user_id,definition_json,risk_state,risk_monitor_at,entry_execution_at",
                "status": "eq.active",
                "combined_stop_triggered_at": "is.null",
                "limit": "25",
            },
        )
        async def monitor_one(row: dict[str, Any]) -> None:
            try:
                if await self.monitor_combined_strategy(row):
                    await self.execute_exit(str(row["id"]), preclaimed=True)
                self.risk_errors.pop(str(row["id"]), None)
            except Exception as exc:
                self.risk_errors[str(row["id"])] = type(exc).__name__
                logger.exception("Combined risk monitor failed for strategy %s", row["id"])
                await self.db.update(
                    "strategies",
                    {"last_error": f"Combined risk monitor: {exc}", "risk_monitor_at": iso_now()},
                    {"id": f"eq.{row['id']}"},
                )
                await self.db.update(
                    "strategies",
                    {"status": "attention"},
                    {"id": f"eq.{row['id']}", "status": "eq.executing_exit", "exit_execution_at": "is.null"},
                )

        await self._dispatch_accounts(active, monitor_one)

    async def strategy_pages(self, params: dict[str, str]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        query = {**params, "order": "id.asc", "limit": "100"}
        cursor: str | None = None
        while True:
            page = await self.db.select("strategies", query)
            rows.extend(page)
            if len(page) < 100:
                return rows
            next_cursor = str(page[-1]["id"])
            if cursor is not None and next_cursor <= cursor:
                raise AppError(502, "Strategy pagination did not advance", "strategy_scan_incomplete")
            cursor = next_cursor
            query["id"] = f"gt.{cursor}"

    async def execute_exit(self, strategy_id: str, preclaimed: bool = False) -> dict[str, Any]:
        if strategy_id in self.running_operations:
            raise AppError(409, "Strategy execution is already running", "execution_in_progress")
        self.running_operations.add(strategy_id)
        try:
            return await self._execute_exit(strategy_id, preclaimed)
        finally:
            self.running_operations.discard(strategy_id)

    async def _execute_exit(self, strategy_id: str, preclaimed: bool = False) -> dict[str, Any]:
        row = await self.strategy_by_id(strategy_id)
        if row.get("status") == "completed":
            raise AppError(409, "Strategy exit has already run", "already_exited")
        client = await self.client_for_user(str(row["user_id"]))
        try:
            await self.recover_order_records(row, client)
            recorded_orders = await self.entry_orders(strategy_id)
            snapshot = await self.account_exposure(client)
            reconciled = await self.reconcile_run_if_flat(
                row,
                client,
                snapshot=snapshot,
                entry_orders=recorded_orders,
            )
            if reconciled is not None:
                return reconciled
            if not preclaimed:
                await self.claim_exit(strategy_id)
            elif row.get("status") != "executing_exit":
                raise AppError(409, "Combined stop exit was not claimed", "exit_not_claimed")
            executions = await self.db.insert(
                "executions", {"strategy_id": strategy_id, "kind": "exit", "status": "running"}
            )
            if not executions:
                await self.db.update(
                    "strategies",
                    {
                        "status": "attention",
                        "exit_execution_at": None,
                        "last_error": "Could not start the exit execution record",
                    },
                    {"id": f"eq.{strategy_id}", "status": "eq.executing_exit"},
                )
                raise AppError(500, "Could not start the execution record", "execution_record_failed")
            execution_id = str(executions[0]["id"])
            if (row.get("risk_state") or {}).get("exclusiveFillAccounting"):
                row["risk_state"] = {**row["risk_state"], "exitRequested": True}
                await self.db.update("strategies", {"risk_state": row["risk_state"]}, {"id": f"eq.{strategy_id}"})
            failures: list[Exception] = []
            submitted = 0
            verified = 0

            if not recorded_orders:
                failures.append(AppError(409, "No recorded entry orders are available to exit", "entry_orders_missing"))
            else:
                product_ids = sorted({int(item["product_id"]) for item in recorded_orders})
                try:
                    open_order_ids = {
                        str(item.get("id"))
                        for item in snapshot.open_orders
                        if int(item.get("product_id") or 0) in product_ids
                        if item.get("id") is not None
                    }
                    for item in recorded_orders:
                        delta_order_id = str(item.get("delta_order_id") or "")
                        if delta_order_id and delta_order_id in open_order_ids:
                            await client.cancel_order(int(delta_order_id), int(item["product_id"]))
                    recorded_orders = await self.reconcile_entry_fills(client, recorded_orders)
                except Exception as exc:
                    failures.append(
                        AppError(
                            502,
                            f"Could not cancel or reconcile outstanding entry orders: {exc}",
                            "entry_order_cleanup_failed",
                        )
                    )

            products: dict[int, dict[str, Any]] = {}
            owned: dict[int, PositionResult] | None = None
            if not failures:
                try:
                    owned = await self.owned_fill_positions(row, client, recorded_orders)
                    if owned is not None and any(
                        int(order.get("product_id") or 0) in owned
                        and order.get("reduce_only") is True
                        and not order.get("stop_order_type")
                        for order in snapshot.open_orders
                    ):
                        raise AppError(
                            409, "An exit order is still working; reconcile before another close", "exit_order_pending"
                        )
                except Exception as error:
                    failures.append(error)
            for item in recorded_orders:
                product_id = int(item["product_id"])
                filled = decimal_value(item.get("filled_size"))
                owned_size = filled
                if owned_size <= 0 and str(item.get("state")) == "closed":
                    owned_size = decimal_value(item["size"])
                signed_size = owned_size if item["side"] == "buy" else -owned_size
                product = products.setdefault(
                    product_id,
                    {
                        "product_id": product_id,
                        "product_symbol": item["product_symbol"],
                        "signed_size": Decimal("0"),
                        "contract_value": item.get("contract_value"),
                    },
                )
                product["signed_size"] += signed_size
            if owned is not None:
                for product_id, product in products.items():
                    product["signed_size"] = owned[product_id].remaining

            async def close_product(index: int, product_id: int, product: dict[str, Any]) -> None:
                nonlocal submitted, verified
                try:
                    live_size = await self.live_position_size(client, product_id)
                    owned_signed_size = decimal_value(product["signed_size"])
                    if live_size == 0 or owned_signed_size == 0:
                        return
                    if live_size * owned_signed_size <= 0:
                        raise AppError(
                            409,
                            f"Live {product['product_symbol']} position does not match the strategy direction",
                            "position_direction_mismatch",
                        )
                    close_size = min(abs(live_size), abs(owned_signed_size))
                    client_order_id = f"dx_{strategy_id[:8]}_{index}_{base36(int(time.time() * 1000))}"[:32]
                    # Exit dispatch must not wait for a reporting-only quote.
                    reference_price: str | None = None
                    order = await self.place_reduce_only_close(
                        client,
                        product_id,
                        close_size,
                        live_size,
                        client_order_id,
                        str(product["product_symbol"]),
                        {
                            "strategyId": strategy_id,
                            "executionId": execution_id,
                            "legId": "exit",
                            **(
                                {"contractValue": str(product["contract_value"])}
                                if product.get("contract_value")
                                else {}
                            ),
                        },
                    )
                    submitted += 1
                    result = order.get("result") or {}
                    requested_size = Decimal(int(close_size))
                    unfilled_size = decimal_value(result.get("unfilled_size"), str(requested_size))
                    close_side = "sell" if live_size > 0 else "buy"
                    await self.record_order(
                        {
                            "execution_id": execution_id,
                            "leg_id": "exit",
                            "delta_order_id": str(result.get("id") or ""),
                            "client_order_id": client_order_id,
                            "product_id": product_id,
                            "product_symbol": product["product_symbol"],
                            "side": close_side,
                            "size": int(close_size),
                            "filled_size": str(max(Decimal("0"), requested_size - unfilled_size)),
                            "average_fill_price": result.get("average_fill_price"),
                            "commission": str(decimal_value(result.get("paid_commission") or result.get("commission"))),
                            "state": str(result.get("state") or "submitted"),
                            "order_type": "market_order",
                            "reference_price": reference_price,
                            "contract_value": product.get("contract_value"),
                            "response_json": order,
                            **slippage_fields(close_side, reference_price, result.get("average_fill_price")),
                        }
                    )
                    if getattr(client, "order_journal", None) is not None:
                        await client.order_journal.call("orderIntents:materialized", {"clientOrderId": client_order_id})
                    remaining_size = await self.verify_position_reduction(client, product_id, close_size, live_size)
                    verified += 1
                    await self.db.update(
                        "execution_orders",
                        {
                            "state": "verified_closed",
                            "response_json": {
                                **order,
                                "verification": {
                                    "initialSize": str(live_size),
                                    "remainingSize": str(remaining_size),
                                    "verifiedAt": iso_now(),
                                },
                            },
                        },
                        {"client_order_id": f"eq.{client_order_id}"},
                    )
                except Exception as exc:
                    failures.append(exc)

            if not failures:
                # Buy back shorts before selling their hedges. Pure same-direction
                # portfolios retain concurrent per-contract closing.
                for short_phase in (True, False):
                    await asyncio.gather(
                        *(
                            close_product(index, product_id, product)
                            for index, (product_id, product) in enumerate(products.items())
                            if (decimal_value(product["signed_size"]) < 0) == short_phase
                        )
                    )
                    if failures:
                        break
            if owned is not None and not failures:
                try:
                    final = await self.reconcile_run_if_flat(row, client)
                    if final is None:
                        raise AppError(409, "Exit has remaining exposure or executable orders", "exit_not_confirmed")
                    await self.db.update(
                        "executions", {"status": "completed", "completed_at": iso_now()}, {"id": f"eq.{execution_id}"}
                    )
                    return {
                        "executionId": execution_id,
                        "ordersSubmitted": submitted,
                        "positionsVerified": verified,
                        "verified": True,
                        "settlement": final.get("settlement"),
                    }
                except Exception as error:
                    failures.append(error)
            failure = failures[0] if failures else None
            completed = iso_now()
            risk_state = dict(row.get("risk_state") or {})
            risk_state["exposureStatus"] = "unknown" if failure else "flat"
            risk_state["reconciledAt"] = completed
            if row.get("combined_stop_triggered_at"):
                risk_state["status"] = "attention" if failure else "exit_verified"
                risk_state["exitVerifiedAt" if not failure else "exitFailedAt"] = completed
            await asyncio.gather(
                self.db.update(
                    "executions",
                    {
                        "status": "partial_or_failed" if failure else "completed",
                        "error": str(failure) if failure else None,
                        "completed_at": completed,
                    },
                    {"id": f"eq.{execution_id}"},
                ),
                self.db.update(
                    "strategies",
                    {
                        "status": "attention" if failure else "completed",
                        "exit_execution_at": None if failure else completed,
                        "last_error": str(failure) if failure else None,
                        "risk_state": risk_state,
                    },
                    {"id": f"eq.{strategy_id}"},
                ),
            )
            settlement = await self.record_settlement(client, strategy_id)
            if failure:
                raise failure
            await self.release_capital_slot(str(row["user_id"]), strategy_id)
            return {
                "executionId": execution_id,
                "ordersSubmitted": submitted,
                "positionsVerified": verified,
                "verified": True,
                "settlement": settlement,
            }
        finally:
            await client.close()

    async def _dispatch_accounts(
        self, rows: list[dict[str, Any]], operation: Callable[[dict[str, Any]], Awaitable[None]]
    ) -> None:
        if not rows:
            return
        if not getattr(self.settings, "convex_runtime_enabled", False):
            for row in rows:
                await operation(row)
            return
        user_ids = list({str(row["user_id"]) for row in rows})
        now = time.monotonic()
        if now >= self.account_groups_refresh_at:
            self.account_groups.clear()
            self.account_groups_refresh_at = now + getattr(self.settings, "account_group_cache_seconds", 30)
        missing = [user_id for user_id in user_ids if user_id not in self.account_groups]
        if missing:
            groups = await asyncio.gather(
                *(
                    self.application_data.request(
                        "accounts:executionGroupsForUsers", {"userIds": missing[start : start + 100]}
                    )
                    for start in range(0, len(missing), 100)
                )
            )
            self.account_groups.update(
                (item["userId"], item["accountId"]) for group in groups for item in group
            )
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            user_id = str(row["user_id"])
            grouped.setdefault(self.account_groups.get(user_id, user_id), []).append(row)
        semaphore = asyncio.Semaphore(getattr(self.settings, "execution_account_concurrency", 8))

        async def execute_account(items: list[dict[str, Any]]) -> None:
            async with semaphore:
                for item in items:
                    await operation(item)

        await asyncio.gather(*(execute_account(items) for items in grouped.values()))

    async def process_due_strategies(self) -> None:
        monotonic_now = time.monotonic()
        if self.public_marks is not None and monotonic_now - self.last_public_mark_prune >= 60:
            await self.public_marks.prune(now=monotonic_now)
            self.last_public_mark_prune = monotonic_now
        reconcile_attention = monotonic_now - self.last_attention_reconcile >= ATTENTION_RECONCILE_SECONDS
        if reconcile_attention:
            self.last_attention_reconcile = monotonic_now
        now = utc_now()
        now_iso = now.isoformat().replace("+00:00", "Z")
        due_entries, due_exits = await asyncio.gather(
            self.strategy_pages(
                {
                    "select": "id,user_id,entry_at",
                    "status": "eq.scheduled",
                    "entry_execution_at": "is.null",
                    "entry_at": f"lte.{now_iso}",
                    "limit": "25",
                },
            ),
            self.strategy_pages(
                {
                    "select": "id,user_id",
                    "status": "eq.active",
                    "exit_execution_at": "is.null",
                    "exit_at": f"lte.{now_iso}",
                    "limit": "25",
                },
            ),
        )

        async def exit_one(row: dict[str, Any]) -> None:
            try:
                await self.execute_exit(str(row["id"]))
            except Exception:
                logger.exception("Scheduled exit failed for strategy %s", row["id"])

        await self._dispatch_accounts(due_exits, exit_one)
        await self.process_active_risks()
        if reconcile_attention:
            await self.reconcile_attention_runs()
        recheck_states = await self.activation_recheck_states([str(row["id"]) for row in due_entries])

        async def enter_one(row: dict[str, Any]) -> None:
            entry_at = datetime.fromisoformat(str(row["entry_at"]).replace("Z", "+00:00"))
            lateness = (utc_now() - entry_at).total_seconds()
            recheck_state = recheck_states[str(row["id"])]
            if recheck_state == "pending" and lateness <= self.settings.max_entry_lateness_seconds:
                return
            if recheck_state in {"failed", "dropped"}:
                await self.reject_scheduled_entry(
                    str(row["id"]),
                    AppError(
                        409,
                        "the activation recheck did not reconfirm this strategy",
                        "activation_recheck_failed",
                    ),
                )
                return
            if lateness > self.settings.max_entry_lateness_seconds:
                await self.reject_scheduled_entry(
                    str(row["id"]),
                    AppError(
                        409,
                        f"the execution window expired after {int(lateness)} seconds",
                        "entry_window_expired",
                    ),
                )
                return
            try:
                await self.execute_entry(str(row["id"]))
            except AppError as error:
                if error.code in TERMINAL_SCHEDULED_ENTRY_CODES:
                    await self.reject_scheduled_entry(str(row["id"]), error)
                    logger.warning(
                        "Scheduled entry rejected for strategy %s code=%s message=%s",
                        row["id"],
                        error.code,
                        error.message,
                    )
                    return
                logger.exception("Scheduled entry failed for strategy %s", row["id"])
            except Exception:
                logger.exception("Scheduled entry failed for strategy %s", row["id"])

        await self._dispatch_accounts(due_entries, enter_one)

    async def activation_recheck_states(self, strategy_ids: list[str]) -> dict[str, str]:
        states = dict.fromkeys(strategy_ids, "ready")
        if not strategy_ids:
            return states
        runtime = getattr(self.db, "runtime", None)
        if runtime is not None:
            groups = await asyncio.gather(
                *(
                    runtime.data.request(
                        "runtimeControl:recheckStates",
                        {"strategyIds": strategy_ids[start : start + 100]},
                    )
                    for start in range(0, len(strategy_ids), 100)
                )
            )
            return {strategy_id: state for group in groups for strategy_id, state in group.items()}
        proposals = await self.db.select(
            "strategy_proposals",
            {"select": "*", "strategy_id": f"in.({','.join(strategy_ids)})"},
        )
        if not proposals:
            return states
        proposal_by_id: dict[str, list[str]] = {}
        for row in proposals:
            decision_id = str(row.get("shared_decision_id") or row["id"])
            proposal_by_id.setdefault(decision_id, []).append(str(row["strategy_id"]))
            states[str(row["strategy_id"])] = "pending"
        rechecks = await self.db.select(
            "automation_agent_runs",
            {
                "select": "strategy_proposal_id,status,outcome",
                "strategy_proposal_id": f"in.({','.join(proposal_by_id)})",
                "trigger": "eq.activation_recheck",
            },
        )
        for recheck in rechecks:
            linked_strategies = proposal_by_id.get(str(recheck.get("strategy_proposal_id")))
            if not linked_strategies:
                continue
            if recheck.get("outcome") == "strategy_reconfirmed" and recheck.get("status") == "completed":
                state = "ready"
            elif recheck.get("outcome") == "strategy_dropped":
                state = "dropped"
            elif recheck.get("status") in {"failed", "cancelled"}:
                state = "failed"
            else:
                state = "pending"
            for strategy_id in linked_strategies:
                states[strategy_id] = state
        return states

    async def reject_scheduled_entry(self, strategy_id: str, error: AppError) -> None:
        reason = f"Entry not placed: {error.message}"
        rows = await self.db.update(
            "strategies",
            {"status": "attention", "last_error": reason},
            {"id": f"eq.{strategy_id}", "status": "eq.scheduled", "entry_execution_at": "is.null"},
        )
        if not rows:
            return
        await self.write_audit(
            "strategy_proposals",
            {"status": "rejected", "rejection_reason": reason},
            {"strategy_id": f"eq.{strategy_id}", "status": "eq.scheduled"},
        )


class Scheduler:
    def __init__(self, engine: TradingEngine, poll_seconds: float, enabled: bool = True) -> None:
        self.engine = engine
        self.poll_seconds = poll_seconds
        self.enabled = enabled
        self.last_started_at: str | None = None
        self.last_completed_at: str | None = None
        self.last_error: str | None = None
        self.task: asyncio.Task[None] | None = None
        self.stop_event = asyncio.Event()

    def start(self) -> None:
        if not self.enabled:
            logger.warning("Scheduler is disabled")
            return
        self.task = asyncio.create_task(self.run(), name="delta-strategy-scheduler")

    async def stop(self) -> None:
        self.stop_event.set()
        self.engine.wake.set()
        if self.task:
            try:
                await asyncio.wait_for(self.task, timeout=5)
            except TimeoutError:
                self.task.cancel()
                with suppress(asyncio.CancelledError):
                    await self.task

    async def run(self) -> None:
        logger.info("Python scheduler started; polling every %.1f seconds", self.poll_seconds)
        while not self.stop_event.is_set():
            self.engine.wake.clear()
            self.last_started_at = iso_now()
            try:
                await self.engine.recover_interrupted_states()
                await self.engine.process_due_strategies()
                self.last_completed_at = iso_now()
                self.last_error = "One or more strategy risk checks failed" if self.engine.risk_errors else None
            except Exception as exc:
                self.last_error = str(exc)
                logger.exception("Scheduler polling cycle failed")
            with suppress(TimeoutError):
                await asyncio.wait_for(self.engine.wake.wait(), timeout=self.poll_seconds)

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "running": bool(self.task and not self.task.done()),
            "pollSeconds": self.poll_seconds,
            "lastStartedAt": self.last_started_at,
            "lastCompletedAt": self.last_completed_at,
            "lastError": self.last_error,
            "riskFailureCount": len(self.engine.risk_errors),
            "connectedPrivateStreams": sum(
                bool(session.events and session.events.connected["private"])
                for session in self.engine.sessions.values()
            ),
            "publicMarkStreams": len(self.engine.public_marks.feeds) if self.engine.public_marks else 0,
            "publicMarkSymbols": len(self.engine.public_marks.by_symbol) if self.engine.public_marks else 0,
        }
