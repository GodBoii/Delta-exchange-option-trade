from types import SimpleNamespace

from app.run_accounting import run_detail_payload
from app.trade_monitor import monitor_snapshot


def test_monitor_reads_only_the_selected_account_and_recorded_contracts():
    seen = []

    def mark(symbol, max_age):
        seen.append((symbol, max_age))
        return "123.45" if symbol == "C-BTC-1" else None

    events = SimpleNamespace(
        closed=False, connected={"private": True}, portfolio=SimpleNamespace(ready=True), watch_mark=mark,
    )
    other = SimpleNamespace(events=SimpleNamespace(watch_mark=lambda *_: (_ for _ in ()).throw(AssertionError())))
    engine = SimpleNamespace(sessions={"selected": SimpleNamespace(events=events), "other": other})
    detail = {"id": "run", "orders": [{"productSymbol": "C-BTC-1"}, {"productSymbol": "C-BTC-1"},
                                       {"productSymbol": "P-BTC-1"}]}
    snapshot = monitor_snapshot(engine, "selected", detail)
    assert seen == [("C-BTC-1", 15.0), ("P-BTC-1", 15.0)]
    assert snapshot["quotes"] == [{"symbol": "C-BTC-1", "markPrice": "123.45"},
                                  {"symbol": "P-BTC-1", "markPrice": None}]
    assert snapshot["privateStream"] == "live"
    assert snapshot["run"] is detail
    assert snapshot["observedAt"]


def test_archive_and_missing_or_closed_streams_do_not_read_live_prices():
    events = SimpleNamespace(closed=True, watch_mark=lambda *_: (_ for _ in ()).throw(AssertionError()))
    engine = SimpleNamespace(sessions={"user": SimpleNamespace(events=events)})
    detail = {"id": "run", "orders": [{"productSymbol": "C-BTC-1"}]}
    for user_id, source in (("user", "archive"), ("missing", "live"), ("user", "live")):
        snapshot = monitor_snapshot(engine, user_id, detail, source=source)
        assert snapshot["privateStream"] == "unavailable"
        assert snapshot["quotes"] == [{"symbol": "C-BTC-1", "markPrice": None}]


def test_owner_risk_values_are_allowlisted_without_internal_account_or_raw_payloads():
    detail = run_detail_payload(
        {"id": "run", "risk_state": {"profit": "2.5", "returnPercent": "5", "pricedAt": "now",
                                     "exitRequested": True, "exchangeAccount": "secret-account",
                                     "legs": [{"internal": "secret"}], "api_secret": "secret"}},
        [], [{"id": "order", "response_json": {"api_secret": "secret"}}], {}, include_raw=False,
    )
    assert detail["riskState"] == {"profit": "2.5", "returnPercent": "5", "pricedAt": "now", "exitRequested": True}
    assert "response" not in detail["orders"][0]
