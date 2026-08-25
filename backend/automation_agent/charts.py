from __future__ import annotations

from io import BytesIO
from math import log, sqrt
from statistics import stdev
from typing import Any

from PIL import Image, ImageDraw, ImageFont

WIDTH = 1200
HEIGHT = 640
PADDING = 56
BACKGROUND = "#071018"
GRID = "#18303f"
TEXT = "#c9d8e3"
UP = "#39d98a"
DOWN = "#ff6b73"
ACCENT = "#f2b84b"
BLUE = "#55a7ff"


def render_candlestick_chart(label: str, candles: list[dict[str, Any]]) -> bytes:
    """Render one compact chart image for multimodal analysis."""
    usable = [candle for candle in candles if _number(candle.get("high")) > 0 and _number(candle.get("low")) > 0]
    if not usable:
        return b""
    usable = usable[-160:]
    high = max(_number(candle["high"]) for candle in usable)
    low = min(_number(candle["low"]) for candle in usable)
    price_range = max(high - low, high * 0.0001)

    image = Image.new("RGB", (WIDTH, HEIGHT), BACKGROUND)
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=22)
    small = ImageFont.load_default(size=16)
    draw.text((PADDING, 18), f"BTCUSDT · {label}", fill=TEXT, font=font)

    chart_top = PADDING + 18
    chart_bottom = HEIGHT - PADDING
    chart_height = chart_bottom - chart_top
    chart_width = WIDTH - PADDING * 2
    for index in range(5):
        y = chart_top + index * chart_height / 4
        draw.line((PADDING, y, WIDTH - PADDING, y), fill=GRID, width=1)
        price = high - index * price_range / 4
        draw.text((WIDTH - PADDING + 6, y - 8), f"{price:,.0f}", fill=TEXT, font=small)

    step = chart_width / max(1, len(usable))
    body_width = max(2, min(8, int(step * 0.65)))

    def y_for(price: float) -> float:
        return chart_top + (high - price) / price_range * chart_height

    for index, candle in enumerate(usable):
        open_price = _number(candle.get("open"))
        close_price = _number(candle.get("close"))
        candle_high = _number(candle.get("high"))
        candle_low = _number(candle.get("low"))
        color = UP if close_price >= open_price else DOWN
        x = PADDING + (index + 0.5) * step
        draw.line((x, y_for(candle_high), x, y_for(candle_low)), fill=color, width=1)
        top = min(y_for(open_price), y_for(close_price))
        bottom = max(y_for(open_price), y_for(close_price))
        if bottom - top < 1:
            bottom = top + 1
        draw.rectangle((x - body_width / 2, top, x + body_width / 2, bottom), fill=color)

    last = _number(usable[-1].get("close"))
    draw.text((PADDING, HEIGHT - 36), f"Last {last:,.2f} · {len(usable)} candles", fill=TEXT, font=small)
    buffer = BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def render_volume_chart(label: str, candles: list[dict[str, Any]]) -> bytes:
    values = [_number(candle.get("volume") or candle.get("baseVolume")) for candle in candles[-160:]]
    return _render_bars(f"BTCUSDT volume · {label}", values, ACCENT)


def render_volatility_chart(label: str, candles: list[dict[str, Any]], periods_per_year: int) -> bytes:
    closes = [_number(candle.get("close")) for candle in candles if _number(candle.get("close")) > 0]
    returns = [log(current / previous) for previous, current in zip(closes, closes[1:], strict=False) if previous > 0]
    values = [
        stdev(returns[index - 19 : index + 1]) * sqrt(periods_per_year) * 100 for index in range(19, len(returns))
    ]
    return _render_line(f"BTCUSDT rolling realized volatility · {label}", values, BLUE, suffix="%")


def render_order_book_chart(order_book: dict[str, Any]) -> bytes:
    bids = order_book.get("bids") or []
    asks = order_book.get("asks") or []
    bid_depth = _cumulative_depth(bids)
    ask_depth = _cumulative_depth(asks)
    return _render_two_lines("BTCUSDT Binance Spot cumulative order-book depth", bid_depth, ask_depth)


def render_open_interest_chart(history: list[dict[str, Any]]) -> bytes:
    values = [_number(point.get("close")) for point in history[-160:]]
    return _render_line("Delta BTCUSD open interest history", values, ACCENT)


def _render_line(label: str, values: list[float], color: str, *, suffix: str = "") -> bytes:
    if len(values) < 2:
        return b""
    image, draw, font, small, top, bottom = _chart_canvas(label)
    maximum = max(values)
    minimum = min(values)
    span = max(maximum - minimum, abs(maximum) * 0.0001, 1e-9)
    width = WIDTH - PADDING * 2
    height = bottom - top
    points = [
        (
            PADDING + index / max(1, len(values) - 1) * width,
            top + (maximum - value) / span * height,
        )
        for index, value in enumerate(values)
    ]
    draw.line(points, fill=color, width=3, joint="curve")
    draw.text((PADDING, HEIGHT - 36), f"Latest {values[-1]:,.2f}{suffix}", fill=TEXT, font=small)
    return _save(image)


def _render_bars(label: str, values: list[float], color: str) -> bytes:
    if not values or max(values) <= 0:
        return b""
    image, draw, _, small, top, bottom = _chart_canvas(label)
    maximum = max(values)
    width = WIDTH - PADDING * 2
    step = width / len(values)
    for index, value in enumerate(values):
        x = PADDING + index * step
        height = (value / maximum) * (bottom - top)
        draw.rectangle((x, bottom - height, x + max(1, step * 0.72), bottom), fill=color)
    draw.text((PADDING, HEIGHT - 36), f"Latest {values[-1]:,.2f}", fill=TEXT, font=small)
    return _save(image)


def _render_two_lines(label: str, left: list[float], right: list[float]) -> bytes:
    if len(left) < 2 or len(right) < 2:
        return b""
    image, draw, _, small, top, bottom = _chart_canvas(label)
    maximum = max([*left, *right])
    width = WIDTH - PADDING * 2
    height = bottom - top

    def points(values: list[float]) -> list[tuple[float, float]]:
        return [
            (
                PADDING + index / max(1, len(values) - 1) * width,
                bottom - value / max(maximum, 1e-9) * height,
            )
            for index, value in enumerate(values)
        ]

    draw.line(points(left), fill=UP, width=3)
    draw.line(points(right), fill=DOWN, width=3)
    draw.text((PADDING, HEIGHT - 36), "Bid depth", fill=UP, font=small)
    draw.text((PADDING + 120, HEIGHT - 36), "Ask depth", fill=DOWN, font=small)
    return _save(image)


def _chart_canvas(label: str) -> tuple[Image.Image, ImageDraw.ImageDraw, Any, Any, int, int]:
    image = Image.new("RGB", (WIDTH, HEIGHT), BACKGROUND)
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=22)
    small = ImageFont.load_default(size=16)
    draw.text((PADDING, 18), label, fill=TEXT, font=font)
    top = PADDING + 18
    bottom = HEIGHT - PADDING
    for index in range(5):
        y = top + index * (bottom - top) / 4
        draw.line((PADDING, y, WIDTH - PADDING, y), fill=GRID, width=1)
    return image, draw, font, small, top, bottom


def _cumulative_depth(levels: list[Any]) -> list[float]:
    cumulative = 0.0
    result: list[float] = []
    for level in levels[:100]:
        if not isinstance(level, list) or len(level) < 2:
            continue
        cumulative += _number(level[1])
        result.append(cumulative)
    return result


def _save(image: Image.Image) -> bytes:
    buffer = BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
