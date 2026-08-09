import asyncio
import logging
import re
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import FastAPI, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .client import INTERVALS, BinanceMarketClient, BinanceMarketError
from .config import get_settings
from .feed import BinanceSpotFeed, replace_latest_candle

settings = get_settings()
logging.basicConfig(level=settings.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    market = BinanceMarketClient(settings)
    feed = BinanceSpotFeed(settings, market)
    app.state.market = market
    app.state.feed = feed
    await feed.start()
    try:
        yield
    finally:
        await feed.stop()
        await market.close()


app = FastAPI(
    title="BTC Spot Intelligence API",
    description="Read-only Binance Spot BTCUSDT streaming market data and analysis for Delta Strategy Desk.",
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


@app.get("/api/market/btcusd")
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
    if current:
        replace_latest_candle(candles, current, limit)
    ticker = {**rest_ticker, **live["ticker"]}
    return response_envelope(
        interval,
        {
            "ticker": ticker,
            "candles": candles,
            "realtime": live["realtime"],
            "analysis": live["analysis"],
        },
    )


@app.get("/api/market/btcusd/ticker")
async def btcusd_ticker(request: Request) -> dict[str, Any]:
    feed: BinanceSpotFeed = request.app.state.feed
    live = feed.snapshot()
    ticker = live["ticker"] or await request.app.state.market.ticker()
    return response_envelope(None, {"ticker": ticker, "realtime": live["realtime"]})


@app.get("/api/market/btcusd/candles")
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
    if current:
        replace_latest_candle(candles, current, limit)
    return response_envelope(interval, {"candles": candles, "realtime": live["realtime"]})


@app.get("/api/market/btcusd/order-book")
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


@app.get("/api/market/btcusd/trades")
async def btcusd_trades(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
) -> dict[str, Any]:
    trades = await request.app.state.market.recent_trades(limit)
    return response_envelope(None, {"trades": trades})


@app.get("/api/market/btcusd/analysis")
async def btcusd_analysis(request: Request) -> dict[str, Any]:
    live = request.app.state.feed.snapshot()
    return response_envelope(None, {"analysis": live["analysis"], "realtime": live["realtime"]})


@app.websocket("/ws/market/btcusd")
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
        "symbol": "BTCUSDT",
        "displaySymbol": "BTC Spot",
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
