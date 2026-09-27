"""The one inclusion rule for personal and owner P&L."""

from decimal import Decimal

from app.run_accounting import classify_run, run_detail_payload


def order(kind: str, side: str, price: str, **extra):
    return {
        "id": f"{kind}-{side}",
        "kind": kind,
        "side": side,
        "size": 1,
        "filled_size": "1",
        "average_fill_price": price,
        "contract_value": "0.001",
        "commission": "0.1",
        "state": "closed",
        "product_symbol": "C-BTC-100000",
        "response_json": {"secret-ish": "raw exchange body"},
        **extra,
    }


def test_completed_fully_closed_run_is_settled_with_realized_pnl():
    orders = [order("entry", "sell", "1000"), order("exit", "buy", "400")]
    result = classify_run("completed", {}, orders)
    assert result.state == "settled"
    # 1000*0.001 - 400*0.001 - 0.2 fees
    assert result.realized_pnl == Decimal("0.4")
    assert result.exchange_fees == Decimal("0.2")


def test_open_run_premium_is_not_profit():
    result = classify_run("active", {}, [order("entry", "sell", "1000")])
    assert result.state == "open"
    assert result.realized_pnl is None


def test_partial_close_unknown_fill_and_pending_fees_are_incomplete():
    partial = classify_run("completed", {}, [order("entry", "sell", "1000")])
    assert (partial.state, partial.reason) == ("incomplete", "position_not_fully_closed")
    unknown = classify_run(
        "completed", {}, [order("entry", "sell", "1000"), order("exit", "buy", "1", state="unknown")]
    )
    assert (unknown.state, unknown.reason) == ("incomplete", "fill_state_unknown")
    pending = classify_run(
        "completed",
        {"accountingBasis": "allocated_exchange_fills", "accountingComplete": False, "realizedPnl": None,
         "filledLots": "1", "fullyClosed": True},
        [],
    )
    assert (pending.state, pending.reason) == ("incomplete", "fees_pending")
    assert pending.realized_pnl is None


def test_missing_contract_value_uses_stored_summary_or_stays_incomplete():
    legacy = [order("entry", "sell", "1000", contract_value=None), order("exit", "buy", "400", contract_value=None)]
    unpriced = classify_run("completed", {}, legacy)
    assert (unpriced.state, unpriced.reason) == ("incomplete", "contract_value_missing")
    stored = {"realizedPnl": "0.4", "filledLots": "1", "fullyClosed": True, "commission": "0.2"}
    priced = classify_run("completed", stored, legacy)
    assert priced.state == "settled" and priced.realized_pnl == Decimal("0.4")


def test_schedule_cancel_and_attention_states():
    assert classify_run("draft", {}, []).state == "scheduled"
    assert classify_run("scheduled", {}, []).state == "scheduled"
    assert classify_run("cancelled", {}, []).state == "cancelled"
    assert classify_run("attention", {}, []).state == "attention"
    assert classify_run("completed", {}, []).reason == "no_fills"


def test_sanitized_detail_drops_raw_responses_and_internal_risk_fields():
    row = {
        "id": "run", "name": "Run", "status": "completed",
        "risk_state": {"exposureStatus": "flat", "exchangeAccount": "acct", "plannedProductIds": ["1"]},
        "capital_policy_json": {"allocationMode": "half_balance", "totalBalanceAtEntry": "100", "extra": "x"},
    }
    detail = run_detail_payload(row, [], [order("entry", "sell", "1")], {}, include_raw=False)
    assert "response" not in detail["orders"][0]
    assert detail["riskState"] == {"exposureStatus": "flat"}
    assert detail["capitalPolicy"] == {"allocationMode": "half_balance", "totalBalanceAtEntry": "100"}
    raw = run_detail_payload(row, [], [order("entry", "sell", "1")], {}, include_raw=True)
    assert raw["orders"][0]["response"] == {"secret-ish": "raw exchange body"}
