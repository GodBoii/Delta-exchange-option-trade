from __future__ import annotations

from datetime import UTC, datetime
from functools import lru_cache
from io import BytesIO
from math import isfinite, log, sqrt
from statistics import mean, stdev
from typing import Any

from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 1600, 900
LEFT, RIGHT, TOP, BOTTOM = 120, 1120, 180, 704
BACKGROUND, GRID, TEXT, MUTED = "#071018", "#203746", "#e5edf4", "#9db2c2"
UP, DOWN, ACCENT, BLUE, PURPLE = "#39d98a", "#ff6b73", "#f2b84b", "#55a7ff", "#c49bff"
INTERVAL_MS = {"1 minute": 60_000, "15 minute": 900_000, "1 day": 86_400_000}


@lru_cache(maxsize=8)
def _font(size: int):
    return ImageFont.load_default(size=size)


def _number(value: Any) -> float | None:
    try:
        number = float(value)
        return number if isfinite(number) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _fmt(value: float | None) -> str:
    if value is None:
        return "Unavailable"
    if abs(value) >= 1e9 or 0 < abs(value) < 0.01:
        return f"{value:.5g}"
    return f"{value:,.2f}"


def _time(value: Any, pattern: str = "%d %b %Y %H:%M:%S UTC") -> str:
    timestamp = _number(value)
    if timestamp is None or timestamp <= 0:
        return "Time unavailable"
    try:
        return datetime.fromtimestamp(timestamp / 1000, UTC).strftime(pattern)
    except (ValueError, OSError, OverflowError):
        return "Time unavailable"


def _text(draw, xy, text: str, size: int = 18, color: str = TEXT, anchor: str = "lt") -> None:
    draw.text(xy, text, font=_font(size), fill=color, anchor=anchor)


def _wrap(draw, text: str, width: int, size: int) -> list[str]:
    lines: list[str] = []
    for paragraph in text.split("\n"):
        line = ""
        for word in paragraph.split():
            candidate = f"{line} {word}".strip()
            if line and draw.textlength(candidate, font=_font(size)) > width:
                lines.append(line)
                line = word
            else:
                line = candidate
        lines.append(line)
    return lines


class _Chart:
    """Fixed plot and annotation regions keep labels outside the data area."""

    def __init__(self, title: str, as_of_ms: int | None):
        self.image = Image.new("RGB", (WIDTH, HEIGHT), BACKGROUND)
        self.draw = ImageDraw.Draw(self.image)
        self.now = as_of_ms if as_of_ms is not None else int(datetime.now(UTC).timestamp() * 1000)
        _text(self.draw, (40, 26), title, 30)
        _text(
            self.draw, (40, 76), f"Source: Binance Spot | Captured {_time(self.now)} | Quote currency: USDT", 18, MUTED
        )
        self.draw.line((40, 110, WIDTH - 40, 110), fill=GRID)

    def axes(self, xs: list[float], low: float, high: float, unit: str, ticks: list[tuple[float, str]], title: str):
        self.xmin, self.xmax = min(xs), max(xs)
        if self.xmax == self.xmin:
            self.xmin -= 0.5
            self.xmax += 0.5
        self.low, self.high = low, high if high > low else low + 1
        _text(self.draw, (LEFT, 145), unit, 18, MUTED)
        for index in range(6):
            value = self.low + (self.high - self.low) * index / 5
            y = self.y(value)
            self.draw.line((LEFT, y, RIGHT, y), fill=GRID)
            _text(self.draw, (LEFT - 12, y), _fmt(value), 17, MUTED, "rm")
        for value, label in ticks:
            x = self.x(value)
            self.draw.line((x, TOP, x, BOTTOM), fill=GRID)
            anchor = "lt" if x == LEFT else "rt" if x == RIGHT else "mt"
            for index, line in enumerate(label.split("\n")):
                _text(self.draw, (x, BOTTOM + 16 + index * 23), line, 17, MUTED, anchor)
        _text(self.draw, ((LEFT + RIGHT) / 2, 775), title, 18, MUTED, "mt")

    def x(self, value: float) -> float:
        return LEFT + (value - self.xmin) / (self.xmax - self.xmin) * (RIGHT - LEFT)

    def y(self, value: float) -> float:
        return BOTTOM - (value - self.low) / (self.high - self.low) * (BOTTOM - TOP)

    def line(self, xs: list[float], values: list[float | None], color: str, gap: float | None = None):
        points = []
        previous = None
        for x, value in zip(xs, values, strict=True):
            if value is None or (gap is not None and previous is not None and x - previous > gap * 1.1):
                self._stroke(points, color)
                points = []
            if value is not None:
                points.append((self.x(x), self.y(value)))
            previous = x
        self._stroke(points, color)

    def _stroke(self, points: list[tuple[float, float]], color: str):
        if len(points) > 1:
            self.draw.line(points, fill=color, width=3)
        elif points:
            x, y = points[0]
            self.draw.ellipse((x - 3, y - 3, x + 3, y + 3), fill=color)

    def panel(self, fields: list[tuple[str, str]]):
        self.draw.rounded_rectangle((1170, 130, 1560, 860), radius=12, fill="#10212e", outline=GRID)
        _text(self.draw, (1192, 151), "CHART CONTEXT", 18, ACCENT)
        y = 188
        for label, value in fields:
            lines = _wrap(self.draw, value, 344, 19)
            if y + 22 + len(lines) * 25 > 843:
                raise ValueError(f"Chart context exceeds its panel: {label}")
            _text(self.draw, (1192, y), label, 15, MUTED)
            for index, line in enumerate(lines):
                _text(self.draw, (1192, y + 22 + index * 25), line, 19)
            y += 34 + len(lines) * 25

    def notes(self, first: str, second: str):
        for index, line in enumerate(_wrap(self.draw, first + "\n" + second, RIGHT - 40, 17)):
            if index >= 3:
                raise ValueError("Chart notes exceed their reserved area")
            _text(self.draw, (40, 810 + index * 25), line, 17, MUTED)

    def save(self) -> bytes:
        output = BytesIO()
        self.image.save(output, format="PNG")
        return output.getvalue()


def _candles(candles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    valid = []
    for candle in candles:
        values = [_number(candle.get(key)) for key in ("open", "high", "low", "close")]
        if any(value is None or value <= 0 for value in values):
            continue
        opening, high, low, close = values
        if low <= min(opening, close) <= max(opening, close) <= high:
            valid.append(candle)
    if valid and all((_number(c.get("openTime")) or 0) > 0 for c in valid):
        valid = sorted({float(c["openTime"]): c for c in valid}.values(), key=lambda c: float(c["openTime"]))
    return valid[-160:]


def _volume(candle: dict[str, Any]) -> float | None:
    value = _number(candle.get("baseVolume", candle.get("volume")))
    return value if value is not None and value >= 0 else None


def _status(candle: dict[str, Any], now: int) -> str:
    if candle.get("closed") is False or (_number(candle.get("closeTime")) or 0) > now:
        return "LIVE / incomplete"
    if candle.get("closed") is True or (_number(candle.get("closeTime")) or 0) > 0:
        return "Completed"
    return "Completion unknown"


def _timeline(candles: list[dict[str, Any]]) -> tuple[list[float], list[tuple[float, str]], str]:
    times = [_number(c.get("openTime")) for c in candles]
    dated = all(value is not None and value > 0 for value in times)
    xs = [float(value) for value in times] if dated else [float(i) for i in range(len(candles))]
    if dated:
        positions = [xs[0]] if len(xs) == 1 else [xs[0] + (xs[-1] - xs[0]) * i / 4 for i in range(5)]
        ticks = [(value, _time(value, "%d %b %Y\n%H:%M")) for value in positions]
    else:
        indices = sorted({round(i * (len(xs) - 1) / 4) for i in range(5)})
        ticks = [(xs[i], str(i + 1)) for i in indices]
    return xs, ticks, "Bar open time | UTC" if dated else "Bar index | timestamps unavailable"


def _ema(values: list[float], period: int) -> list[float]:
    result = [values[0]]
    for value in values[1:]:
        result.append(result[-1] + (value - result[-1]) * 2 / (period + 1))
    return result


def render_candlestick_chart(label: str, candles: list[dict[str, Any]], *, as_of_ms: int | None = None) -> bytes:
    usable = _candles(candles)
    if not usable:
        return b""
    chart = _Chart(f"BTCUSDT | Candlesticks | {label}", as_of_ms)
    xs, ticks, title = _timeline(usable)
    high, low = max(float(c["high"]) for c in usable), min(float(c["low"]) for c in usable)
    pad = max((high - low) * 0.06, high * 0.0001)
    chart.axes(xs, max(0, low - pad), high + pad, "Price | USDT per BTC", ticks, title)
    step = min((b - a for a, b in zip(xs, xs[1:], strict=False)), default=1)
    width = min(9, max(1, (RIGHT - LEFT) * step / (chart.xmax - chart.xmin) * 0.6))
    closes = [float(c["close"]) for c in usable]
    ema20, ema50 = _ema(closes, 20), _ema(closes, 50)
    vwap_values = []
    notional = volume = 0.0
    complete_volume = True
    for x, c in zip(xs, usable, strict=True):
        opening, close = float(c["open"]), float(c["close"])
        color = UP if close >= opening else DOWN
        live = _status(c, chart.now) == "LIVE / incomplete"
        chart.draw.line(
            (chart.x(x), chart.y(float(c["high"])), chart.x(x), chart.y(float(c["low"]))),
            fill=color,
        )
        top, bottom = sorted((chart.y(opening), chart.y(close)))
        chart.draw.rectangle(
            (chart.x(x) - width / 2, top, chart.x(x) + width / 2, max(bottom, top + 1)),
            fill=color,
            outline=ACCENT if live else color,
        )
        v = _volume(c)
        complete_volume = complete_volume and v is not None
        if v is not None:
            notional += (float(c["high"]) + float(c["low"]) + close) / 3 * v
            volume += v
        vwap_values.append(notional / volume if volume and complete_volume else None)
    for values, color in ((ema20, BLUE), (ema50, PURPLE), (vwap_values, ACCENT)):
        chart.line(xs, values, color, INTERVAL_MS.get(label) if "UTC" in title else None)
    for x, text, color in ((460, "EMA 20", BLUE), (600, "EMA 50", PURPLE), (740, "Window VWAP", ACCENT)):
        _text(chart.draw, (x, 145), text, 18, color)
    latest = usable[-1]
    chart.panel(
        [
            ("Latest bar", _status(latest, chart.now)),
            (
                "Latest OHLC | USDT",
                f"O {_fmt(float(latest['open']))}   H {_fmt(float(latest['high']))}\n"
                f"L {_fmt(float(latest['low']))}   C {_fmt(closes[-1])}",
            ),
            ("Shown high / low", f"{_fmt(high)} / {_fmt(low)}"),
            ("Shown close-to-close return", f"{(closes[-1] / closes[0] - 1) * 100:+.2f}%"),
            ("EMA 20 / EMA 50", f"{_fmt(ema20[-1])} / {_fmt(ema50[-1])}"),
            ("Window VWAP | HLC3 weighted by BTC", _fmt(vwap_values[-1])),
            ("Sample", f"{len(usable)} bars | {label}"),
            ("Latest bar opens", _time(latest.get("openTime"))),
        ]
    )
    chart.notes(
        "Green: close >= open. Red: close < open. Gold outline: live bar, not a confirmed close.",
        "EMAs start at the first shown close. VWAP resets at the left edge; it is not session VWAP.",
    )
    return chart.save()


def render_volume_chart(label: str, candles: list[dict[str, Any]], *, as_of_ms: int | None = None) -> bytes:
    usable = _candles(candles)
    volumes = [_volume(c) for c in usable]
    if not usable or all(v is None for v in volumes):
        return b""
    chart = _Chart(f"BTCUSDT | Traded volume | {label}", as_of_ms)
    xs, ticks, title = _timeline(usable)
    maximum = max(v or 0 for v in volumes)
    chart.axes(xs, 0, maximum * 1.12 if maximum else 1, "Volume | BTC per bar", ticks, title)
    step = min((b - a for a, b in zip(xs, xs[1:], strict=False)), default=1)
    width = min(12, max(1, (RIGHT - LEFT) * step / (chart.xmax - chart.xmin) * 0.65))
    completed = []
    baseline = []
    for x, c, volume in zip(xs, usable, volumes, strict=True):
        baseline.append(mean(completed[-20:]) if len(completed) >= 20 else None)
        if volume is None:
            continue
        live = _status(c, chart.now) == "LIVE / incomplete"
        color = UP if float(c["close"]) >= float(c["open"]) else DOWN
        chart.draw.rectangle(
            (chart.x(x) - width / 2, chart.y(volume), chart.x(x) + width / 2, BOTTOM),
            fill=color,
            outline=ACCENT if live else color,
        )
        if _status(c, chart.now) == "Completed":
            completed.append(volume)
    chart.line(xs, baseline, BLUE, INTERVAL_MS.get(label) if "UTC" in title else None)
    _text(chart.draw, (530, 145), "Blue: previous 20 completed bars' mean", 18, BLUE)
    last_closed = completed[-1] if completed else None
    comparison = mean(completed[-21:-1]) if len(completed) >= 21 else None
    ratio = last_closed / comparison if last_closed is not None and comparison else None
    chart.panel(
        [
            ("Latest completed volume | BTC", _fmt(last_closed)),
            ("Previous 20 completed bars | mean BTC", _fmt(comparison)),
            (
                "Relative volume | completed / mean",
                f"{ratio:.2f}x"
                if ratio is not None
                else ("Undefined: baseline volume is zero" if comparison == 0 else "Insufficient completed bars"),
            ),
            ("Latest shown bar", f"{_fmt(volumes[-1])} BTC\n{_status(usable[-1], chart.now)}"),
            ("Shown total | includes live bar", f"{_fmt(sum(v for v in volumes if v is not None))} BTC"),
            ("Sample", f"{len(usable)} bars | {sum(v is None for v in volumes)} missing volumes"),
            ("Interpretation", "Traded base-asset volume. Candle colors are not buy/sell volume."),
        ]
    )
    chart.notes(
        "Bars measure traded BTC, not USDT turnover. Green/red follow candle direction.",
        "Gold outline marks partial volume. Compare completed bars; do not infer weak flow from an unfinished bar.",
    )
    return chart.save()


def _realized_volatility(candles: list[dict[str, Any]], periods_per_year: int, interval: int | None):
    returns = []
    values: list[float | None] = [None]
    for previous, current in zip(candles, candles[1:], strict=False):
        before, after = _number(previous.get("openTime")), _number(current.get("openTime"))
        if interval and before is not None and after is not None and after - before > interval * 1.1:
            returns = []
            values.append(None)
            continue
        returns.append(log(float(current["close"])) - log(float(previous["close"])))
        values.append(stdev(returns[-20:]) * sqrt(periods_per_year) * 100 if len(returns) >= 20 else None)
    return values


def render_volatility_chart(
    label: str,
    candles: list[dict[str, Any]],
    periods_per_year: int,
    *,
    as_of_ms: int | None = None,
) -> bytes:
    if periods_per_year <= 0:
        raise ValueError("periods_per_year must be positive")
    chart = _Chart(f"BTCUSDT | Rolling realized volatility | {label}", as_of_ms)
    usable = [c for c in _candles(candles) if _status(c, chart.now) != "LIVE / incomplete"]
    if len(usable) < 21:
        return b""
    values = _realized_volatility(usable, periods_per_year, INTERVAL_MS.get(label))
    available = [v for v in values if v is not None]
    if not available:
        return b""
    xs, ticks, title = _timeline(usable)
    chart.axes(xs, 0, max(max(available) * 1.12, 1), "Annualized realized volatility | %", ticks, title)
    chart.line(xs, values, BLUE, INTERVAL_MS.get(label) if "UTC" in title else None)
    hours = 20 * 365 * 24 / periods_per_year
    chart.panel(
        [
            ("Latest valid estimate | annualized", f"{_fmt(available[-1])}%"),
            (
                "Change from preceding valid estimate",
                f"{available[-1] - available[-2]:+.2f} percentage points"
                if len(available) >= 2
                else "No preceding estimate",
            ),
            ("Shown minimum / maximum", f"{_fmt(min(available))}% / {_fmt(max(available))}%"),
            ("Rolling lookback", f"20 close-to-close log returns\n{hours:g} hours per window"),
            ("Annualization", f"{periods_per_year:,} periods/year\n365-day convention"),
            (
                "Last estimate's bar opens",
                _time(usable[max(i for i, v in enumerate(values) if v is not None)].get("openTime")),
            ),
            (
                "Data quality",
                "Live bars excluded. Windows restart after timestamp gaps."
                if all(_status(c, chart.now) == "Completed" for c in usable)
                else "Some completion timestamps unavailable.",
            ),
        ]
    )
    chart.notes(
        f"Formula: sample stdev of 20 log returns x sqrt({periods_per_year:,}) x 100. Live bars excluded.",
        "Historical variability, not implied volatility or a forecast. Annualized % is not the expected session move.",
    )
    return chart.save()


def _book_levels(raw: list[Any], reverse: bool) -> list[tuple[float, float]]:
    quantities: dict[float, float] = {}
    for level in raw:
        if not isinstance(level, (list, tuple)) or len(level) < 2:
            continue
        price, quantity = _number(level[0]), _number(level[1])
        if price is not None and quantity is not None and price > 0 and quantity > 0:
            quantities[price] = quantities.get(price, 0) + quantity
    return sorted(quantities.items(), reverse=reverse)[:100]


def render_order_book_chart(order_book: dict[str, Any], *, as_of_ms: int | None = None) -> bytes:
    bids, asks = _book_levels(order_book.get("bids") or [], True), _book_levels(order_book.get("asks") or [], False)
    if not bids or not asks:
        return b""
    chart = _Chart("BTCUSDT | Cumulative order-book depth", as_of_ms)
    bid, ask = bids[0][0], asks[0][0]
    mid = (bid + ask) / 2
    bid_total, ask_total = sum(q for _, q in bids), sum(q for _, q in asks)
    low, high = min(p for p, _ in bids + asks), max(p for p, _ in bids + asks)
    pad = max((high - low) * 0.06, mid * 1e-7)
    xs = [low - pad, high + pad]
    ticks = [(xs[0] + (xs[-1] - xs[0]) * i / 4, _fmt(xs[0] + (xs[-1] - xs[0]) * i / 4)) for i in range(5)]
    chart.axes(
        xs,
        0,
        max(bid_total, ask_total) * 1.12,
        "Cumulative resting quantity | BTC",
        ticks,
        "Limit price | USDT per BTC",
    )
    for levels, color in ((bids, UP), (asks, DOWN)):
        total = 0.0
        points = [(chart.x(levels[0][0]), chart.y(0))]
        for price, quantity in levels:
            points.append((chart.x(price), chart.y(total)))
            total += quantity
            points.append((chart.x(price), chart.y(total)))
        chart.draw.line(points, fill=color, width=3)
    for y in range(TOP, BOTTOM, 12):
        chart.draw.line((chart.x(mid), y, chart.x(mid), min(y + 6, BOTTOM)), fill=ACCENT, width=2)
    _text(chart.draw, (640, 145), "Bids", 18, UP)
    _text(chart.draw, (730, 145), "Asks", 18, DOWN)
    _text(chart.draw, (820, 145), "Dashed: midpoint", 18, ACCENT)
    chart.panel(
        [
            ("Best bid / best ask | USDT", f"{_fmt(bid)} / {_fmt(ask)}"),
            ("Spread | USDT and basis points", f"{_fmt(ask - bid)} USDT | {(ask - bid) / mid * 10000:.4f} bps"),
            ("Midpoint | USDT", f"{mid:,.3f}"),
            (f"Bid depth | {len(bids)} supplied levels", f"{_fmt(bid_total)} BTC"),
            (f"Ask depth | {len(asks)} supplied levels", f"{_fmt(ask_total)} BTC"),
            ("Displayed-depth imbalance", f"{(bid_total - ask_total) / (bid_total + ask_total) * 100:+.2f}%"),
            ("Feed timestamp", _time(order_book.get("eventTime"))),
            (
                "Validity",
                "Crossed/locked book: unreliable." if bid >= ask else "Snapshot only; not the full market book.",
            ),
        ]
    )
    chart.notes(
        "Bids accumulate from best bid toward lower prices; asks from best ask toward higher prices.",
        "Imbalance = (bid BTC - ask BTC) / total BTC. Resting orders can cancel; this is not executed flow.",
    )
    return chart.save()
