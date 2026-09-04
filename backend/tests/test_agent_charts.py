from io import BytesIO
from math import log, sin, sqrt
from statistics import stdev

import pytest
from PIL import Image

from automation_agent import charts

START = 1788480000000
INTERVAL = 900000
NOW = START + 161 * INTERVAL


def candles(count=160, scale=80000, dated=True, gap=False):
    rows = []
    for i in range(count):
        opening = scale * (1 + 0.005 * sin(i / 5))
        close = opening * (1 + 0.001 * sin(i))
        start = START + (i + (100 if gap and i >= count // 2 else 0)) * INTERVAL
        row = {
            "open": opening,
            "high": max(opening, close) * 1.002,
            "low": min(opening, close) * 0.998,
            "close": close,
            "baseVolume": 10 + i % 7,
        }
        if dated:
            row.update(openTime=start, closeTime=start + INTERVAL - 1, closed=True)
        rows.append(row)
    return rows


@pytest.mark.parametrize("kind", ["price", "volume", "volatility", "depth"])
@pytest.mark.parametrize("case", ["normal", "single", "flat", "missing", "large", "gap"])
def test_chart_text_is_in_bounds_and_never_overlaps(monkeypatch, kind, case):
    labels = []
    original = charts._text

    def record(draw, xy, text, size=18, color=charts.TEXT, anchor="lt"):
        box = draw.textbbox(xy, text, font=charts._font(size), anchor=anchor)
        labels.append((text, box))
        original(draw, xy, text, size, color, anchor)

    monkeypatch.setattr(charts, "_text", record)
    rows = candles(
        count=1 if case == "single" else 160,
        scale=1e12 if case == "large" else 80000,
        dated=case != "missing",
        gap=case == "gap",
    )
    if case == "flat":
        for row in rows:
            row.update(open=80000, high=80000, low=80000, close=80000, baseVolume=0)
    # Keep future closes out of the test snapshot even with a deliberately large data gap.
    now = NOW + 200 * INTERVAL
    if kind == "price":
        png = charts.render_candlestick_chart("15 minute", rows, as_of_ms=now)
    elif kind == "volume":
        png = charts.render_volume_chart("15 minute", rows, as_of_ms=now)
    elif kind == "volatility":
        png = charts.render_volatility_chart("15 minute", rows, 35040, as_of_ms=now)
        if case == "single":
            assert png == b""
            return
    else:
        png = charts.render_order_book_chart(
            {"bids": [[79999, 0.000001], [79990, 2]], "asks": [[80001, 1000], [80010, 4]], "eventTime": now},
            as_of_ms=now,
        )
    assert Image.open(BytesIO(png)).size == (1600, 900)
    assert len(png) < 1_000_000
    for text, box in labels:
        assert 0 <= box[0] <= box[2] <= charts.WIDTH, (text, box)
        assert 0 <= box[1] <= box[3] <= charts.HEIGHT, (text, box)
    for index, (first, a) in enumerate(labels):
        for second, b in labels[index + 1 :]:
            assert min(a[2], b[2]) <= max(a[0], b[0]) or min(a[3], b[3]) <= max(a[1], b[1]), (first, second)


def test_volatility_formula_and_gap_reset():
    rows = candles(50)
    result = charts._realized_volatility(rows, 35040, INTERVAL)
    expected = stdev([log(rows[i]["close"] / rows[i - 1]["close"]) for i in range(1, 21)]) * sqrt(35040) * 100
    assert result[:20] == [None] * 20
    assert result[20] == pytest.approx(expected)
    for row in rows[25:]:
        row["openTime"] += 10 * INTERVAL
    result = charts._realized_volatility(rows, 35040, INTERVAL)
    assert result[25:45] == [None] * 20
    assert result[45] is not None


def test_volume_and_volatility_do_not_treat_live_bar_as_completed(monkeypatch):
    rows = candles(22)
    for row in rows:
        row["baseVolume"] = 10
    rows[-1]["baseVolume"] = 10000
    rows[-1]["closed"] = False
    panels = []
    original = charts._Chart.panel

    def record(self, fields):
        panels.append(dict(fields))
        original(self, fields)

    monkeypatch.setattr(charts._Chart, "panel", record)
    charts.render_volume_chart("15 minute", rows, as_of_ms=NOW)
    assert panels[-1]["Relative volume | completed / mean"] == "1.00x"
    assert "LIVE / incomplete" in panels[-1]["Latest shown bar"]
    charts.render_volatility_chart("15 minute", rows, 35040, as_of_ms=NOW)
    assert panels[-1]["Last estimate's bar opens"] == charts._time(rows[-2]["openTime"])


def test_missing_and_invalid_inputs_remain_explicit():
    assert charts._number("NaN") is None
    assert charts._number("inf") is None
    assert charts._volume({"baseVolume": 0, "volume": 10}) == 0
    assert charts._volume({}) is None
    assert charts._candles([{"open": 1, "high": 2, "low": 0.5, "close": 3}]) == []
    assert charts.render_candlestick_chart("1 minute", []) == b""
    assert charts.render_order_book_chart({"bids": [[1, 1]], "asks": []}) == b""
    with pytest.raises(ValueError):
        charts.render_volatility_chart("15 minute", candles(), 0)


def test_depth_sorts_aggregates_and_preserves_price_distance():
    assert charts._book_levels([[99, 2], [100, 3], [99, 4], [98, "NaN"], [97, -1]], True) == [(100, 3), (99, 6)]
    chart = charts._Chart("Test", NOW)
    chart.axes([90, 110], 0, 10, "BTC", [], "USDT")
    assert chart.x(99) - chart.x(90) == pytest.approx(9 * (chart.x(100) - chart.x(99)))
