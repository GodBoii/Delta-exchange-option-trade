import pytest

from app.analysis import calculate_analysis


def candle(index: int, close: float, volume: float = 2.0) -> dict[str, float | int]:
    return {
        "openTime": index * 60_000,
        "open": close - 2,
        "high": close + 4,
        "low": close - 5,
        "close": close,
        "baseVolume": volume,
    }


def test_calculates_market_analysis_from_spot_inputs() -> None:
    candles = [candle(index, 60_000 + index * 10) for index in range(80)]
    bids = [[60_790.0, 4.0], [60_780.0, 6.0]]
    asks = [[60_800.0, 2.0], [60_810.0, 3.0]]

    result = calculate_analysis(candles, bids, asks, cumulative_volume_delta=3.25)

    assert result["atr"]["value"] > 0
    assert result["historicalVolatility"]["annualizedPercent"] >= 0
    assert result["vwap"] > 60_000
    assert result["cvd"]["baseVolume"] == 3.25
    assert result["orderBook"]["bestBid"] == 60_790.0
    assert result["orderBook"]["bestAsk"] == 60_800.0
    assert result["orderBook"]["imbalance"] == pytest.approx(1 / 3)
    assert result["orderBook"]["demandLevel"] == {"price": 60_780.0, "quantity": 6.0}
    assert result["marketStructure"]["state"] == "bullish"
    assert 0 <= result["sidewaysProbability"] <= 100


def test_analysis_handles_empty_bootstrap_state() -> None:
    result = calculate_analysis([], [], [], cumulative_volume_delta=0)

    assert result["atr"]["value"] == 0
    assert result["vwap"] == 0
    assert result["orderBook"]["demandLevel"] is None
    assert result["marketStructure"]["state"] == "forming"
