import asyncio
import json
import logging
import time
from collections import deque
from contextlib import suppress
from typing import Any

from websockets.asyncio.client import connect

from .analysis import calculate_analysis
from .client import BinanceMarketClient, normalize_stream_candle, normalize_ticker, number
from .config import Settings
from .delta_context import DeltaMarketContextClient

logger = logging.getLogger(__name__)
STREAM_INTERVALS = ("1m", "5m", "15m", "1h", "4h", "1d")


class OrderBookSequenceGap(RuntimeError):
    pass


class BinanceSpotFeed:
    """Maintains one shared Binance Spot stream and publishes normalized snapshots."""

    def __init__(
        self,
        settings: Settings,
        rest: BinanceMarketClient,
        delta: DeltaMarketContextClient | None = None,
    ) -> None:
        self.settings = settings
        self.rest = rest
        self.connected = False
        self.book_synced = False
        self.last_event_at = 0
        self.last_trade_at = 0
        self.last_error: str | None = None
        self.reconnects = 0
        self.sequence = 0
        self.ticker: dict[str, Any] = {}
        self.current_candles: dict[str, dict[str, Any]] = {}
        self.analysis_candles: list[dict[str, Any]] = []
        self.bids: dict[float, float] = {}
        self.asks: dict[float, float] = {}
        self.book_update_id = 0
        self.trade_deltas: deque[tuple[int, float]] = deque()
        self.recent_trades: deque[dict[str, Any]] = deque(maxlen=60)
        self.delta = delta
        self.delta_context: dict[str, Any] = {}
        self.delta_context_error: str | None = None
        self._delta_history_at = 0.0
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._dirty = asyncio.Event()
        self._stopping = asyncio.Event()
        self._runner: asyncio.Task[None] | None = None
        self._publisher: asyncio.Task[None] | None = None
        self._delta_runner: asyncio.Task[None] | None = None
        self._analysis: dict[str, Any] = {}
        self._analysis_at = 0.0

    async def start(self) -> None:
        await self._seed()
        self._runner = asyncio.create_task(self._run_forever(), name="binance-spot-stream")
        self._publisher = asyncio.create_task(self._publish_forever(), name="binance-market-publisher")
        if self.delta:
            self._delta_runner = asyncio.create_task(self._refresh_delta_forever(), name="delta-public-context")

    async def stop(self) -> None:
        self._stopping.set()
        tasks = [task for task in (self._runner, self._publisher, self._delta_runner) if task]
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task

    async def _seed(self) -> None:
        try:
            ticker, candles = await asyncio.gather(self.rest.ticker(), self.rest.candles("1m", 500))
            self.ticker = ticker
            self.analysis_candles = candles
            self.last_error = None
        except Exception as error:  # The WebSocket can still recover after REST bootstrap failure.
            self.last_error = str(error)
            logger.warning("Binance Spot bootstrap failed: %s", error)

    async def _run_forever(self) -> None:
        delay = 1.0
        while not self._stopping.is_set():
            try:
                await self._stream_once()
                delay = 1.0
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self.connected = False
                self.book_synced = False
                self.last_error = str(error)
                self.reconnects += 1
                self._dirty.set()
                logger.warning("Binance Spot stream reconnecting: %s", error)
                await asyncio.sleep(delay)
                delay = min(15.0, delay * 2)

    async def _refresh_delta_forever(self) -> None:
        if not self.delta:
            return
        while not self._stopping.is_set():
            try:
                now = time.monotonic()
                include_slow_data = not self.delta_context or now - self._delta_history_at >= self.settings.delta_history_seconds
                context = await self.delta.snapshot(include_slow_data=include_slow_data)
                observed_at = int(time.time() * 1000)
                context["receivedAt"] = observed_at
                self.delta_context = {**self.delta_context, **context}
                if include_slow_data:
                    self._delta_history_at = now
                self.delta_context_error = None
                self._dirty.set()
            except Exception as error:
                self.delta_context_error = str(error)
                logger.warning("Delta public context refresh failed: %s", error)
            with suppress(TimeoutError):
                await asyncio.wait_for(self._stopping.wait(), timeout=self.settings.delta_context_seconds)

    async def _stream_once(self) -> None:
        symbol = self.settings.binance_symbol.lower()
        streams = [
            f"{symbol}@trade",
            f"{symbol}@bookTicker",
            f"{symbol}@depth@100ms",
            f"{symbol}@ticker",
            *(f"{symbol}@kline_{interval}" for interval in STREAM_INTERVALS),
        ]
        uri = f"{self.settings.binance_ws_url}?streams={'/'.join(streams)}"
        depth_buffer: list[dict[str, Any]] = []
        snapshot_task: asyncio.Task[dict[str, Any]] | None = None
        self.book_synced = False
        self.bids = {}
        self.asks = {}
        self.book_update_id = 0

        async with connect(
            uri,
            open_timeout=10,
            close_timeout=5,
            ping_interval=20,
            ping_timeout=20,
            max_queue=4096,
        ) as socket:
            self.connected = True
            self.last_error = None
            snapshot_task = asyncio.create_task(self.rest.order_book(1000, fresh=True))
            self._dirty.set()
            try:
                async for raw_message in socket:
                    message = json.loads(raw_message)
                    stream = str(message.get("stream") or "")
                    data = message.get("data", message)
                    now_ms = int(time.time() * 1000)
                    self.last_event_at = now_ms
                    self.sequence += 1

                    if stream.endswith("@depth@100ms") or data.get("e") == "depthUpdate":
                        if self.book_synced:
                            self._apply_depth(data)
                        else:
                            depth_buffer.append(data)
                    elif stream.endswith("@bookTicker") or ("b" in data and "a" in data and "e" not in data):
                        self._handle_book_ticker(data)
                    elif data.get("e") == "trade":
                        self._handle_trade(data)
                    elif data.get("e") == "24hrTicker":
                        self.ticker = {**self.ticker, **normalize_ticker(data)}
                    elif data.get("e") == "kline":
                        self._handle_kline(data.get("k") or {})

                    if not self.book_synced and snapshot_task.done():
                        snapshot = snapshot_task.result()
                        if self._synchronize_book(snapshot, depth_buffer):
                            depth_buffer.clear()
                    self._dirty.set()
            finally:
                self.connected = False
                if snapshot_task and not snapshot_task.done():
                    snapshot_task.cancel()

    def _synchronize_book(self, snapshot: dict[str, Any], events: list[dict[str, Any]]) -> bool:
        last_update_id = int(snapshot["lastUpdateId"])
        applicable = [event for event in events if int(event.get("u") or 0) > last_update_id]
        start_index = next(
            (
                index
                for index, event in enumerate(applicable)
                if int(event.get("U") or 0) <= last_update_id + 1 <= int(event.get("u") or 0)
            ),
            None,
        )
        if not applicable:
            return False
        if start_index is None:
            raise OrderBookSequenceGap("Could not bridge the Binance order-book snapshot")

        self.bids = {float(price): float(quantity) for price, quantity in snapshot["bids"] if quantity > 0}
        self.asks = {float(price): float(quantity) for price, quantity in snapshot["asks"] if quantity > 0}
        self.book_update_id = last_update_id
        for event in applicable[start_index:]:
            self._apply_depth(event)
        self.book_synced = True
        return True

    def _apply_depth(self, data: dict[str, Any]) -> None:
        first_update_id = int(data.get("U") or 0)
        final_update_id = int(data.get("u") or 0)
        if final_update_id <= self.book_update_id:
            return
        if self.book_update_id and first_update_id > self.book_update_id + 1:
            raise OrderBookSequenceGap(
                f"Binance order-book sequence gap: expected {self.book_update_id + 1}, received {first_update_id}"
            )
        update_levels(self.bids, data.get("b") or [])
        update_levels(self.asks, data.get("a") or [])
        self.book_update_id = final_update_id

    def _handle_book_ticker(self, data: dict[str, Any]) -> None:
        self.ticker.update(
            {
                "bestBid": number(data.get("b")),
                "bestBidQuantity": number(data.get("B")),
                "bestAsk": number(data.get("a")),
                "bestAskQuantity": number(data.get("A")),
            }
        )

    def _handle_trade(self, data: dict[str, Any]) -> None:
        price = number(data.get("p"))
        quantity = number(data.get("q"))
        trade_time = int(data.get("T") or data.get("E") or int(time.time() * 1000))
        signed_quantity = -quantity if data.get("m") else quantity
        self.trade_deltas.append((trade_time, signed_quantity))
        self.recent_trades.appendleft(
            {
                "id": int(data.get("t") or 0),
                "price": price,
                "quantity": quantity,
                "quoteQuantity": price * quantity,
                "time": trade_time,
                "side": "sell" if data.get("m") else "buy",
                "buyerIsMaker": bool(data.get("m")),
            }
        )
        self.last_trade_at = trade_time
        self.ticker["lastPrice"] = price
        self.ticker["closeTime"] = trade_time
        self._prune_trades(trade_time)

    def _handle_kline(self, raw: dict[str, Any]) -> None:
        interval = str(raw.get("i") or "")
        if interval not in STREAM_INTERVALS:
            return
        candle = normalize_stream_candle(raw)
        self.current_candles[interval] = candle
        if interval == "1m":
            replace_latest_candle(self.analysis_candles, candle, 500)

    def _prune_trades(self, now_ms: int) -> None:
        cutoff = now_ms - self.settings.cvd_window_seconds * 1000
        while self.trade_deltas and self.trade_deltas[0][0] < cutoff:
            self.trade_deltas.popleft()

    async def _publish_forever(self) -> None:
        while not self._stopping.is_set():
            await self._dirty.wait()
            await asyncio.sleep(self.settings.market_broadcast_seconds)
            self._dirty.clear()
            payload = self.snapshot()
            for subscriber in tuple(self._subscribers):
                if subscriber.full():
                    with suppress(asyncio.QueueEmpty):
                        subscriber.get_nowait()
                subscriber.put_nowait(payload)

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=2)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(queue)

    def snapshot(self) -> dict[str, Any]:
        bids, asks = self.book_levels(50)
        now = time.monotonic()
        if now - self._analysis_at >= 1 or not self._analysis:
            current_candles = list(self.analysis_candles)
            current_1m = self.current_candles.get("1m")
            if current_1m:
                replace_latest_candle(current_candles, current_1m, 500)
            cvd = sum(delta for _, delta in self.trade_deltas)
            self._analysis = calculate_analysis(current_candles, bids, asks, cvd)
            self._analysis_at = now
        return {
            "type": "market_update",
            "sequence": self.sequence,
            "receivedAt": self.last_event_at or int(time.time() * 1000),
            "symbol": "BTCUSDT",
            "source": "Binance Spot",
            "ticker": dict(self.ticker),
            "candles": {interval: dict(candle) for interval, candle in self.current_candles.items()},
            "orderBook": {
                "lastUpdateId": self.book_update_id,
                "eventTime": self.last_event_at,
                "bids": bids[:15],
                "asks": asks[:15],
            },
            "recentTrades": list(self.recent_trades)[:30],
            "deltaContext": {
                **self.delta_context,
                "available": bool(self.delta_context),
                "lastError": self.delta_context_error,
            },
            "realtime": self.status(),
            "analysis": self._analysis,
        }

    def status(self) -> dict[str, Any]:
        now_ms = int(time.time() * 1000)
        age = now_ms - self.last_event_at if self.last_event_at else None
        return {
            "connected": self.connected,
            "bookSynced": self.book_synced,
            "lastEventAt": self.last_event_at or None,
            "lastTradeAt": self.last_trade_at or None,
            "eventAgeMs": age,
            "lastError": self.last_error,
            "reconnects": self.reconnects,
            "subscribers": len(self._subscribers),
        }

    def book_levels(self, limit: int) -> tuple[list[list[float]], list[list[float]]]:
        bids = [[price, quantity] for price, quantity in sorted(self.bids.items(), reverse=True)[:limit]]
        asks = [[price, quantity] for price, quantity in sorted(self.asks.items())[:limit]]
        return bids, asks

    def order_book_payload(self, limit: int) -> dict[str, Any] | None:
        if not self.book_synced:
            return None
        bids, asks = self.book_levels(limit)
        return {
            "lastUpdateId": self.book_update_id,
            "eventTime": self.last_event_at,
            "transactionTime": self.last_event_at,
            "bids": bids,
            "asks": asks,
        }


def update_levels(book: dict[float, float], levels: list[list[str]]) -> None:
    for raw_price, raw_quantity in levels:
        price = float(raw_price)
        quantity = float(raw_quantity)
        if quantity == 0:
            book.pop(price, None)
        else:
            book[price] = quantity


def replace_latest_candle(candles: list[dict[str, Any]], candle: dict[str, Any], limit: int) -> None:
    if candles and candles[-1].get("openTime") == candle.get("openTime"):
        candles[-1] = dict(candle)
    else:
        candles.append(dict(candle))
    if len(candles) > limit:
        del candles[:-limit]
