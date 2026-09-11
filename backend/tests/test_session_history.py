from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.automation_schedule import fixed_runs_between
from automation_agent.history import STEP_MS, session_history
from automation_agent.market import MarketIntelligenceTools, compact_btc_market_packet


def payload(now):
    end = int(now.timestamp() * 1000) // STEP_MS * STEP_MS
    return {
        "available": True,
        "observations": [
            {
                "end": end - i * STEP_MS,
                "sidewaysScore": 80,
                "volatilityAnnualizedPercent": 40,
                "volumeBtc": 10,
                "volumeUsdt": 800000,
            }
            for i in range(300)
        ],
    }


@pytest.mark.parametrize("day", ["2026-09-06", "2026-03-08", "2026-03-29", "2026-11-01"])
@pytest.mark.parametrize("trigger", ["asia_session", "london_session", "pre_expiry", "new_york_session"])
def test_matching_session_cycle_and_dst(day, trigger):
    midnight = datetime.fromisoformat(day).replace(tzinfo=UTC)
    now = next(
        b.scheduled_for for b in fixed_runs_between(midnight, midnight + timedelta(days=1)) if b.trigger == trigger
    )
    result = session_history(payload(now), now, trigger)
    sessions = result["sessions"]
    assert len(sessions) == 4
    assert sessions[-1]["session"] == trigger
    assert datetime.fromisoformat(sessions[0]["end"]) == now
    assert all(s["coveragePercent"] == 100 and s["averageSidewaysScorePercent"] == 80 for s in sessions)
    assert all(s["averageVolatilityAnnualizedPercent"] == 40 for s in sessions)
    assert all(s["observedTradedVolumeBtc"] == s["sampleCount"] * 10 for s in sessions)
    assert result["recent"]["last1Hour"]["observedTradedVolumeBtc"] == 60
    assert result["recent"]["last2Hour"]["observedTradedVolumeBtc"] == 120


def test_missing_samples_and_partial_sessions_are_explicit():
    now = datetime(2026, 9, 6, 0, 25, tzinfo=UTC)
    data = payload(now)
    data["observations"] = data["observations"][:1]
    result = session_history(data, now, "manual")
    assert result["sessions"][0]["partial"] is True
    assert result["recent"]["last1Hour"]["coveragePercent"] == 16.7
    assert result["sessions"][-1]["averageSidewaysScorePercent"] is None
    assert result["sessions"][-1]["observedTradedVolumeBtc"] is None


def test_agent_context_removes_classification_without_mutating_source():
    packet = {
        "analysis": {
            "marketStructure": {"state": "ranging", "ema20": 10, "ema50": 11, "strength": 2},
            "sidewaysProbability": 89.7,
        },
        "sessionHistory": {"available": False},
    }
    compact = compact_btc_market_packet(packet)
    assert "marketStructure" not in compact["computedAnalysis"]
    assert compact["computedAnalysis"]["emaIndicators"]["ema20"] == 10
    assert compact["computedAnalysis"]["sidewaysProbability"] == 89.7
    assert compact["sessionHistory"] == {"available": False}
    assert packet["analysis"]["marketStructure"]["state"] == "ranging"


@pytest.mark.parametrize("failure", [None, "offline", "invalid", "duplicate"])
def test_collected_packet_includes_history_and_survives_history_failure(monkeypatch, failure):
    now = datetime.now(UTC)
    history = payload(now)
    if failure == "invalid":
        history["observations"][0]["sidewaysScore"] = "bad"
    elif failure == "duplicate":
        history["observations"][1] = history["observations"][0]

    def handler(request):
        if request.url.path.endswith("/history"):
            return httpx.Response(503 if failure == "offline" else 200, json=history)
        return httpx.Response(
            200,
            json={
                "symbol": "BTCUSDT",
                "interval": request.url.params["interval"],
                "analysis": {"sidewaysProbability": 89},
                "candles": [],
            },
        )

    client = httpx.Client
    monkeypatch.setattr(
        "automation_agent.market.httpx.Client",
        lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs),
    )
    tools = MarketIntelligenceTools(binance_url="http://market.test")
    packet = tools.collect_btc_market_packet()
    assert packet["sessionHistory"]["available"] is (failure is None)
    assert packet["analysis"]["sidewaysProbability"] == 89
    assert "sessionHistory" in tools.get_btc_market_packet()
