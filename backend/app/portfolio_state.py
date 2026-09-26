"""Live account state rebuilt from Delta's private stream.

Delta sends a snapshot for ``positions`` and ``orders`` right after subscribing
and then incremental create/update/delete messages. ``margins`` has no snapshot,
so wallet rows are seeded once from REST and then replaced by pushed rows.

Records are trimmed to the fields the portfolio view reads. A raw Delta position
carries the whole product definition (several kilobytes), which would otherwise
be pushed to the browser on every change.
"""

import asyncio
import contextlib
from datetime import UTC, datetime
from typing import Any

# REST ``positions/margined`` lags the socket by up to ten seconds, so cashflow
# fields that only REST reports are refreshed a little after that window.
ENRICH_DELAY_SECONDS = 12.0

POSITION_FIELDS = (
    "product_id", "product_symbol", "size", "entry_price", "margin", "margin_mode", "liquidation_price",
    "bankruptcy_price", "mark_price", "commission", "realized_pnl", "realized_cashflow", "realized_funding",
    "unrealized_pnl", "unrealized_cashflow", "auto_topup", "adl_level", "created_at", "updated_at",
)
# Fields the incremental socket messages do not carry but REST does.
ENRICH_FIELDS = (
    "realized_pnl", "realized_cashflow", "realized_funding", "commission", "margin_mode",
    "liquidation_price", "bankruptcy_price",
)
PRODUCT_FIELDS = (
    "id", "symbol", "contract_value", "contract_type", "notional_type", "strike_price", "settlement_time",
    "tick_size", "contract_unit_currency", "state", "trading_status",
)
PRODUCT_ASSETS = ("spot_index", "underlying_asset", "settling_asset")
ORDER_FIELDS = (
    "id", "product_id", "product_symbol", "side", "size", "unfilled_size", "limit_price", "average_fill_price",
    "state", "order_type", "created_at", "updated_at", "stop_order_type", "stop_price", "stop_trigger_method",
    "trail_amount", "reduce_only", "bracket_order", "bracket_stop_loss_price", "bracket_stop_loss_limit_price",
    "bracket_take_profit_price", "bracket_take_profit_limit_price", "bracket_trail_amount", "client_order_id",
    "time_in_force", "cancellation_reason",
)
BALANCE_FIELDS = (
    "asset_id", "asset_symbol", "balance", "available_balance", "blocked_margin", "order_margin",
    "position_margin", "cross_order_margin", "cross_position_margin", "commission", "cross_commission",
    "portfolio_margin", "cross_locked_collateral", "balance_inr", "available_balance_inr",
)
CLOSED_ORDER_STATES = frozenset({"closed", "cancelled"})


def _pick(record: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    return {key: record[key] for key in fields if key in record}


def slim_product(product: Any) -> dict[str, Any] | None:
    if not isinstance(product, dict):
        return None
    slim = _pick(product, PRODUCT_FIELDS)
    for key in PRODUCT_ASSETS:
        asset = product.get(key)
        if isinstance(asset, dict) and asset.get("symbol"):
            slim[key] = {"symbol": str(asset["symbol"])}
    return slim


def slim_position(record: dict[str, Any]) -> dict[str, Any]:
    slim = _pick(record, POSITION_FIELDS)
    if "product_symbol" not in slim and record.get("symbol"):
        slim["product_symbol"] = record["symbol"]
    product = slim_product(record.get("product"))
    if product is not None:
        slim["product"] = product
    return slim


def _micros_to_iso(value: Any) -> str | None:
    try:
        return datetime.fromtimestamp(int(value) / 1_000_000, UTC).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def slim_order(record: dict[str, Any]) -> dict[str, Any]:
    slim = _pick(record, ORDER_FIELDS)
    if "id" not in slim and record.get("order_id") is not None:
        slim["id"] = record["order_id"]
    if "product_symbol" not in slim and record.get("symbol"):
        slim["product_symbol"] = record["symbol"]
    return slim


def slim_balance(record: dict[str, Any]) -> dict[str, Any]:
    return _pick(record, BALANCE_FIELDS)


def _product_id(record: dict[str, Any]) -> int | None:
    try:
        value = int(record["product_id"])
    except (KeyError, TypeError, ValueError):
        return None
    return value if value > 0 else None


def _is_flat(record: dict[str, Any]) -> bool:
    try:
        return int(record.get("size") or 0) == 0
    except (TypeError, ValueError):
        return False


class AccountPortfolio:
    """Positions, working orders and wallet rows for one Delta account."""

    def __init__(self) -> None:
        self.positions: dict[int, dict[str, Any]] = {}
        self.orders: dict[str, dict[str, Any]] = {}
        self.balances: dict[str, dict[str, Any]] = {}
        self.positions_ready = False
        self.orders_ready = False
        self.balances_ready = False
        self.version = 0
        # Bumped on every reset so REST replies requested before a reconnect are dropped.
        self.generation = 0
        # One REST wallet request per generation, however many views are open.
        self.balances_requested = False
        # Monotonic deadline after which REST should backfill cashflow fields.
        self.enrich_after: float | None = None
        self._waiters: list[asyncio.Future[None]] = []

    @property
    def ready(self) -> bool:
        return self.positions_ready and self.orders_ready and self.balances_ready

    def apply(self, message: dict[str, Any], now: float) -> bool:
        kind = message.get("type")
        if kind == "positions":
            changed = self._apply_position(message, now)
        elif kind == "orders":
            changed = self._apply_order(message)
        elif kind == "margins":
            changed = self._apply_margin(message)
        else:
            return False
        if changed:
            self._touch()
        return changed

    def _apply_position(self, message: dict[str, Any], now: float) -> bool:
        if message.get("action") == "snapshot":
            rows = message.get("result")
            if not isinstance(rows, list):
                return False
            positions: dict[int, dict[str, Any]] = {}
            for row in rows:
                product_id = _product_id(row) if isinstance(row, dict) else None
                if product_id is not None and not _is_flat(row):
                    positions[product_id] = slim_position(row)
            self.positions = positions
            self.positions_ready = True
            return True
        product_id = _product_id(message)
        if product_id is None:
            return False
        if message.get("action") == "delete" or _is_flat(message):
            return self.positions.pop(product_id, None) is not None
        previous = self.positions.get(product_id, {})
        merged = {**previous, **slim_position(message)}
        if "product" not in merged and "product" in previous:
            merged["product"] = previous["product"]
        self.positions[product_id] = merged
        if self.enrich_after is None:
            self.enrich_after = now + ENRICH_DELAY_SECONDS
        return merged != previous

    def _apply_order(self, message: dict[str, Any]) -> bool:
        if message.get("action") == "snapshot":
            rows = message.get("result")
            if not isinstance(rows, list):
                return False
            orders: dict[str, dict[str, Any]] = {}
            for row in rows:
                if isinstance(row, dict) and row.get("id") is not None and row.get("state") not in CLOSED_ORDER_STATES:
                    orders[str(row["id"])] = slim_order(row)
            self.orders = orders
            self.orders_ready = True
            return True
        update = slim_order(message)
        if update.get("id") is None:
            return False
        order_id = str(update["id"])
        if message.get("action") == "delete" or update.get("state") in CLOSED_ORDER_STATES:
            return self.orders.pop(order_id, None) is not None
        previous = self.orders.get(order_id, {})
        merged = {**previous, **update}
        if "created_at" not in merged:
            created = _micros_to_iso(message.get("timestamp"))
            if created is not None:
                merged["created_at"] = created
        self.orders[order_id] = merged
        return merged != previous

    def _apply_margin(self, message: dict[str, Any]) -> bool:
        asset = message.get("asset_symbol")
        if not asset:
            return False
        key = str(asset)
        merged = {**self.balances.get(key, {}), **slim_balance(message)}
        if merged == self.balances.get(key):
            return False
        self.balances[key] = merged
        return True

    def seed_balances(self, rows: list[Any], generation: int) -> None:
        """REST wallet rows fill assets that no pushed ``margins`` row has covered yet."""
        if generation != self.generation:
            return
        for row in rows:
            if isinstance(row, dict) and row.get("asset_symbol"):
                self.balances.setdefault(str(row["asset_symbol"]), slim_balance(row))
        self.balances_ready = True
        self._touch()

    def enrich_positions(self, rows: list[Any]) -> None:
        """Backfill REST-only fields without letting a lagging REST size override the socket."""
        changed = False
        for row in rows:
            product_id = _product_id(row) if isinstance(row, dict) else None
            current = self.positions.get(product_id) if product_id is not None else None
            if current is None or str(current.get("size")) != str(row.get("size")):
                continue
            merged = {**current, **_pick(row, ENRICH_FIELDS)}
            if "product" not in merged:
                product = slim_product(row.get("product"))
                if product is not None:
                    merged["product"] = product
            if merged != current:
                self.positions[product_id] = merged
                changed = True
        if changed:
            self._touch()

    def missing_products(self) -> set[str]:
        return {
            str(position["product_symbol"])
            for position in self.positions.values()
            if "product" not in position and position.get("product_symbol")
        }

    def set_product(self, symbol: str, product: dict[str, Any]) -> None:
        slim = slim_product(product)
        if slim is None:
            return
        changed = False
        for position in self.positions.values():
            if position.get("product_symbol") == symbol and "product" not in position:
                position["product"] = slim
                changed = True
        if changed:
            self._touch()

    def reset(self) -> None:
        """A dropped private socket makes every incremental record untrustworthy."""
        self.positions, self.orders, self.balances = {}, {}, {}
        self.positions_ready = self.orders_ready = self.balances_ready = False
        self.balances_requested = False
        self.generation += 1
        self.enrich_after = None
        self._touch()

    def snapshot(self) -> dict[str, list[dict[str, Any]]]:
        return {
            "positions": sorted(self.positions.values(), key=lambda row: str(row.get("product_symbol") or "")),
            "orders": sorted(self.orders.values(), key=lambda row: str(row.get("created_at") or ""), reverse=True),
            "balances": sorted(self.balances.values(), key=lambda row: str(row.get("asset_symbol") or "")),
        }

    async def wait_for_change(self, version: int, timeout: float) -> None:
        """Returns as soon as the state moves past ``version``, or after ``timeout``."""
        if self.version != version:
            return
        waiter = asyncio.get_running_loop().create_future()
        self._waiters.append(waiter)
        try:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(waiter, timeout)
        finally:
            with contextlib.suppress(ValueError):
                self._waiters.remove(waiter)

    def _touch(self) -> None:
        self.version += 1
        waiters, self._waiters = self._waiters, []
        for waiter in waiters:
            if not waiter.done():
                waiter.set_result(None)
