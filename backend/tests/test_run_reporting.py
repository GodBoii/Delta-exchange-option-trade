from decimal import Decimal

from app.engine import TradingEngine, settlement_summary, slippage_fields


def order(symbol: str, kind: str, price: str, fee: str, lots: int = 6) -> dict:
    return {
        "product_symbol": symbol,
        "kind": kind,
        "side": "sell" if kind == "entry" else "buy",
        "size": lots,
        "filled_size": lots,
        "average_fill_price": price,
        "contract_value": "0.001",
        "commission": fee,
    }


def test_closed_straddle_cash_flows_and_slippage_are_not_double_counted() -> None:
    orders = [
        order("call", "entry", "324", "0.05071526"),
        order("put", "entry", "319", "0.05071514"),
        order("put", "exit", "272", "0.05073259"),
        order("call", "exit", "302", "0.05073259"),
    ]
    references = ["322.27924163", "324.45753651", "271.6022325", "300.19979624"]
    for row, reference in zip(orders, references, strict=True):
        row.update(slippage_fields(row["side"], reference, row["average_fill_price"]))
    summary = settlement_summary(orders)
    for key, expected in {
        "entryPremium": "3.858", "exitPremium": "-3.444", "grossPnl": "0.414",
        "commission": "0.20289558", "realizedPnl": "0.21110442", "slippageCost": "0.03560849640",
    }.items():
        assert Decimal(summary[key]) == Decimal(expected)
    assert summary["fullyClosed"] is True
    assert Decimal(orders[0]["slippage"]) < 0
    assert all(Decimal(row["slippage"]) > 0 for row in orders[1:])


def test_equal_total_lots_do_not_hide_an_unclosed_contract() -> None:
    summary = settlement_summary([
        order("call", "entry", "100", "0", 1),
        order("put", "entry", "100", "0", 1),
        order("call", "exit", "90", "0", 2),
    ])
    assert summary["filledLots"] == summary["closedLots"]
    assert summary["fullyClosed"] is False


async def test_run_detail_uses_current_orders_and_preserves_settlement_metadata() -> None:
    class ReportingDB:
        async def select(self, table: str, params: dict) -> list[dict]:
            if table == "strategies":
                return [{
                    "id": "run", "name": "Run", "status": "completed",
                    "result_json": {"realizedPnl": "999", "settledAt": "2026-09-05T11:00:00Z"},
                }]
            if table == "executions":
                return [{"id": "entry", "kind": "entry", "status": "completed"},
                        {"id": "exit", "kind": "exit", "status": "completed"}]
            assert table == "execution_orders"
            return [{"id": "a", "execution_id": "entry", **order("call", "entry", "100", "0.01")},
                    {"id": "b", "execution_id": "exit", **order("call", "exit", "90", "0.01")}]

    detail = await TradingEngine(ReportingDB(), None).run_detail("run", "owner")
    assert Decimal(detail["settlement"]["realizedPnl"]) == Decimal("0.04")
    assert detail["settlement"]["settledAt"] == "2026-09-05T11:00:00Z"
