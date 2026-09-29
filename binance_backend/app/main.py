import asyncio
import hmac
import logging
import re
import sqlite3
import time
from contextlib import asynccontextmanager
from typing import Annotated, Any

import httpx
from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .client import INTERVALS, BinanceMarketClient, BinanceMarketError
from .config import get_settings
from .delta_context import DeltaMarketContextClient
from .evidence import depth_summary, instant
from .feed import BinanceSpotFeed, replace_latest_candle

settings = get_settings()
# One instance serves one market; BTCUSD keeps its original /api/market/btcusd paths.
ROUTE = settings.market_route
logging.basicConfig(level=settings.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    market = BinanceMarketClient(settings)
    delta = DeltaMarketContextClient(settings)
    feed = BinanceSpotFeed(settings, market, delta)
    app.state.market = market
    app.state.feed = feed
    await feed.start()
    try:
        yield
    finally:
        await feed.stop()
        await delta.close()
        await market.close()


app = FastAPI(
    title=f"{settings.base_asset} Spot Intelligence API",
    description=(
        f"Read-only Binance Spot {settings.binance_symbol} streaming market data and analysis for Delta Strategy Desk."
    ),
    version="2.0.0",
    redoc_url=None,
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_origin_regex=settings.frontend_origin_regex,
    allow_credentials=False,
    allow_methods=["GET", "OPTIONS"],
    allow_headers=["Content-Type"],
)


@app.exception_handler(BinanceMarketError)
async def market_error_handler(_: Request, error: BinanceMarketError) -> JSONResponse:
    return JSONResponse(
        status_code=error.status,
        content={"success": False, "error": {"code": "market_data_unavailable", "message": str(error)}},
    )


@app.get("/health")
async def health(request: Request) -> dict[str, Any]:
    feed: BinanceSpotFeed = request.app.state.feed
    return {
        "success": True,
        "service": "binance-market-data-api",
        "source": "Binance Spot",
        "symbol": settings.binance_symbol,
        "realtime": feed.status(),
    }


@app.get(f"/api/market/{ROUTE}")
async def btcusd_market(
    request: Request,
    interval: Annotated[str, Query()] = "1h",
    limit: Annotated[int, Query(ge=20, le=1000)] = 240,
    startTime: Annotated[int | None, Query(ge=0)] = None,
    endTime: Annotated[int | None, Query(ge=0)] = None,
) -> dict[str, Any]:
    validate_interval(interval)
    market: BinanceMarketClient = request.app.state.market
    feed: BinanceSpotFeed = request.app.state.feed
    rest_ticker, candles = await asyncio.gather(
        market.ticker(),
        market.candles(interval, limit, startTime, endTime),
    )
    live = feed.snapshot()
    current = live["candles"].get(interval)
    if (current and (startTime is None or current["openTime"] >= startTime)
            and (endTime is None or current["openTime"] <= endTime)):
        replace_latest_candle(candles, current, limit)
    ticker = {**rest_ticker, **live["ticker"]}
    return response_envelope(
        interval,
        {
            "ticker": ticker,
            "candles": candles,
            "realtime": live["realtime"],
            "analysis": live["analysis"],
            "orderBook": live["orderBook"],
            "recentTrades": live["recentTrades"],
            "deltaContext": live["deltaContext"],
        },
    )


@app.get(f"/api/market/{ROUTE}/ticker")
async def btcusd_ticker(request: Request) -> dict[str, Any]:
    feed: BinanceSpotFeed = request.app.state.feed
    live = feed.snapshot()
    ticker = live["ticker"] or await request.app.state.market.ticker()
    return response_envelope(None, {"ticker": ticker, "realtime": live["realtime"]})


@app.get(f"/api/market/{ROUTE}/candles")
async def btcusd_candles(
    request: Request,
    interval: Annotated[str, Query()] = "1h",
    limit: Annotated[int, Query(ge=1, le=1000)] = 500,
    startTime: Annotated[int | None, Query(ge=0)] = None,
    endTime: Annotated[int | None, Query(ge=0)] = None,
) -> dict[str, Any]:
    validate_interval(interval)
    candles = await request.app.state.market.candles(interval, limit, startTime, endTime)
    live = request.app.state.feed.snapshot()
    current = live["candles"].get(interval)
    if (current and (startTime is None or current["openTime"] >= startTime)
            and (endTime is None or current["openTime"] <= endTime)):
        replace_latest_candle(candles, current, limit)
    return response_envelope(interval, {"candles": candles, "realtime": live["realtime"]})


@app.get(f"/api/market/{ROUTE}/order-book")
async def btcusd_order_book(
    request: Request,
    limit: Annotated[int, Query()] = 20,
) -> dict[str, Any]:
    if limit not in {5, 10, 20, 50, 100, 500, 1000}:
        return JSONResponse(
            status_code=400,
            content={"success": False, "error": {"code": "invalid_limit", "message": "Unsupported order-book limit"}},
        )
    feed: BinanceSpotFeed = request.app.state.feed
    order_book = feed.order_book_payload(limit) or await request.app.state.market.order_book(limit)
    return response_envelope(None, {"orderBook": order_book, "realtime": feed.status()})


@app.get(f"/api/market/{ROUTE}/trades")
async def btcusd_trades(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
) -> dict[str, Any]:
    trades = await request.app.state.market.recent_trades(limit)
    return response_envelope(None, {"trades": trades})


@app.get(f"/api/market/{ROUTE}/analysis")
async def btcusd_analysis(request: Request) -> dict[str, Any]:
    live = request.app.state.feed.snapshot()
    return response_envelope(None, {"analysis": live["analysis"], "realtime": live["realtime"]})


@app.get(f"/api/market/{ROUTE}/history")
async def btcusd_history(request: Request) -> dict[str, Any]:
    feed: BinanceSpotFeed = request.app.state.feed
    now = int(time.time() * 1000)
    try:
        rows = await asyncio.to_thread(feed.history.read, now)
    except sqlite3.Error:
        return {"available": False, "observations": [], "error": "Market history is unavailable"}
    return {"available": True, "observations": rows, "asOf": now, "schemaVersion": 1,
            "intervalMinutes": 10, "error": feed.history_error}


@app.get(f"/api/market/{ROUTE}/delta")
async def btcusd_delta_context(request: Request) -> dict[str, Any]:
    """Return the cached public Delta BTCUSD execution-market snapshot."""
    live = request.app.state.feed.snapshot()
    return {
        "success": True,
        "symbol": settings.delta_symbol,
        "source": "Delta Exchange",
        "deltaContext": live["deltaContext"],
    }


class SelectedContracts(BaseModel):
    symbols: list[str] = Field(min_length=1, max_length=16)


class ExpiryWatchlist(BaseModel):
    expiries: list[str] = Field(max_length=100)


@app.post(f"/api/market/{ROUTE}/watch-expiries")
async def watch_expiries(request: Request, body: ExpiryWatchlist) -> dict:
    if not settings.analysis_service_secret or not hmac.compare_digest(
        request.headers.get("X-Analysis-Secret", ""), settings.analysis_service_secret
    ):
        raise HTTPException(401, "Service authentication required")
    expiries = [instant(value) for value in body.expiries]
    if any(value is None for value in expiries):
        raise HTTPException(422, "Expiry requires an aware ISO timestamp")
    await asyncio.to_thread(request.app.state.feed.evidence.store.watch, expiries, int(time.time() * 1000))
    return {"success": True}


@app.post(f"/api/market/{ROUTE}/selected-contracts")
async def selected_contracts(request: Request, body: SelectedContracts) -> dict:
    # Read-only operation. POST keeps the bounded symbol list out of URL logs.
    try:
        rows = await request.app.state.feed.evidence.selected(body.symbols)
    except (ValueError, httpx.HTTPError) as error:
        raise HTTPException(503, "Selected option evidence is unavailable") from error
    return {"source": "Delta Exchange", "asset": settings.base_asset, "contracts": rows}


@app.get(f"/api/market/{ROUTE}/agent-summary")
async def agent_summary(request: Request) -> dict:
    feed = request.app.state.feed
    now = int(time.time() * 1000)
    futures = await asyncio.to_thread(feed.evidence.futures_summary, feed.delta_context, now)
    current = depth_summary(feed.bids, feed.asks) if feed.book_synced and now - feed.last_depth_at <= 5000 else {}
    options = feed.evidence.overview if now - feed.evidence.options_at <= 90_000 else []
    return {"schemaVersion": 1, "asset": settings.base_asset, "asOf": now,
            "futures": futures, "options": options, "liquidity": current,
            "previousLiquidityBucket": feed.liquidity}


@app.get(f"/api/market/{ROUTE}/option-catalogue")
async def option_catalogue(request: Request) -> dict:
    # Internal numerical catalogue used to resolve saved strikes, never returned to the LLM.
    evidence = request.app.state.feed.evidence
    now = int(time.time() * 1000)
    return {"source": "Delta Exchange", "underlying": settings.base_asset, "receivedAt": evidence.options_at,
            "options": [o for o in evidence.options if 0 <= now - o["observedAt"] <= 90_000]}


@app.websocket(f"/ws/market/{ROUTE}")
async def btcusd_stream(websocket: WebSocket) -> None:
    origin = websocket.headers.get("origin")
    if origin and not origin_allowed(origin):
        await websocket.close(code=1008, reason="Origin not allowed")
        return
    feed: BinanceSpotFeed = websocket.app.state.feed
    queue = feed.subscribe()
    await websocket.accept()
    try:
        await websocket.send_json(feed.snapshot())
        while True:
            await websocket.send_json(await queue.get())
    except WebSocketDisconnect:
        pass
    finally:
        feed.unsubscribe(queue)


def validate_interval(interval: str) -> None:
    if interval not in INTERVALS:
        raise BinanceMarketError(f"Unsupported candle interval: {interval}", status=400)


def response_envelope(interval: str | None, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "success": True,
        "symbol": settings.binance_symbol,
        "displaySymbol": f"{settings.base_asset} Spot",
        "exchangeSymbol": settings.binance_symbol,
        "source": "Binance Spot",
        **({"interval": interval} if interval else {}),
        **body,
    }


def origin_allowed(origin: str) -> bool:
    normalized = origin.rstrip("/")
    explicitly_allowed = normalized in settings.allowed_origins
    matches_local_pattern = re.fullmatch(settings.frontend_origin_regex, normalized) is not None
    return explicitly_allowed or matches_local_pattern
