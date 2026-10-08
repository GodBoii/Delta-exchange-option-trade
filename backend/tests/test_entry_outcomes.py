from copy import deepcopy
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from app import engine as engine_module
from app.engine import TradingEngine
from app.entry_outcomes import entry_failure
from app.errors import AppError
from app.run_accounting import classify_run
from tests.fakes import FakeApplicationData
from tests.test_engine import settings


class EntryDB:
    def __init__(self, now):
        self.local_data = FakeApplicationData()
        self.row = {
            "id": "scheduled-1", "user_id": "user-1", "status": "scheduled",
            "entry_at": (now - timedelta(seconds=1)).isoformat(), "entry_execution_at": None,
        }

    async def select(self, table, query):
        if table == "strategy_proposals":
            return []
        assert table == "strategies"
        if query.get("status") == f"eq.{self.row['status']}":
            return [deepcopy(self.row)]
        return []

    async def update(self, table, value, query):
        assert table == "strategies"
        if query.get("status") != f"eq.{self.row['status']}" or self.row["entry_execution_at"]:
            return []
        self.row.update(deepcopy(value))
        return [deepcopy(self.row)]


@pytest.mark.parametrize(("code", "category"), [
    ("capital_slots_full", "slots_full"), ("capital_reserved", "capital_full"),
    ("automation_balance_unavailable", "low_balance"), ("insufficient_margin", "low_balance"),
    ("automatic_lot_too_large", "capital_budget"), ("delta_unreachable", "network"),
    ("entry_window_expired", "window_expired"), ("delta_not_connected", "authorization"),
])
def test_entry_reason_keeps_exchange_code_and_message(code, category):
    outcome = entry_failure(AppError(409, "Original reason", code), "now")
    assert outcome == {"code": code, "category": category, "message": "Original reason", "occurredAt": "now"}


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [AppError(502, "Exchange unavailable", "delta_unreachable"),
                                   httpx.ConnectTimeout("timeout"), RuntimeError("internal detail")])
async def test_retry_failure_survives_window_expiry_without_attention(monkeypatch, error):
    now = datetime.now(UTC)
    monkeypatch.setattr(engine_module, "utc_now", lambda: now)
    db = EntryDB(now)
    engine = TradingEngine(db, settings())
    engine.execute_entry = AsyncMock(side_effect=error)
    engine.write_audit = AsyncMock()
    engine.notify_strategy = Mock()
    await engine.process_due_strategies()
    assert db.row["status"] == "scheduled"
    failure = db.row["entry_outcome"]
    assert failure["status"] == "retrying"
    assert failure["category"] == ("system" if isinstance(error, RuntimeError) else "network")
    now += timedelta(seconds=181)
    await engine.process_due_strategies()
    await engine.process_due_strategies()
    assert db.row["status"] == "skipped"
    assert db.row["entry_outcome"]["code"] == "entry_window_expired"
    assert db.row["entry_outcome"]["lastFailure"] == failure
    assert failure["message"] in db.row["last_error"]
    engine.execute_entry.assert_awaited_once()


@pytest.mark.asyncio
async def test_rejection_does_not_overwrite_submitted_entry():
    db = EntryDB(datetime.now(UTC))
    db.row.update(status="attention", entry_execution_at="2026-10-08T00:00:00Z")
    engine = TradingEngine(db, settings())
    await engine.reject_scheduled_entry(db.row["id"], AppError(409, "Low balance", "insufficient_funds"))
    assert db.row["status"] == "attention"


@pytest.mark.asyncio
async def test_terminal_and_deleted_risk_errors_clear_but_unresolved_errors_remain():
    rows = {"done": "completed", "cancelled": "cancelled", "skipped": "skipped", "open": "active",
            "uncertain": "attention"}

    class RiskDB:
        async def select(self, table, query):
            assert table == "strategies"
            if "id" in query:
                return [{"id": key, "status": status} for key, status in rows.items()]
            return []

    engine = TradingEngine(RiskDB(), settings())
    engine.risk_errors = dict.fromkeys([*rows, "deleted"], "AppError")
    await engine.process_active_risks()
    assert engine.risk_errors == {"open": "AppError", "uncertain": "AppError"}


def test_skipped_run_is_not_an_attention_or_unresolved_accounting_alert():
    result = classify_run("skipped", {}, [])
    assert (result.state, result.reason, result.realized_pnl) == ("cancelled", "entry_not_placed", None)
