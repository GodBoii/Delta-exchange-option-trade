import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.default_strategies import default_strategy_definitions, eth_strategy_definitions
from app.exit_schedule import ExitChoice, resolve_exit_schedule, template_from_definition
from app.materialized_definition import validate_materialized_definition
from automation_agent.tools import AutomationStrategyTools


def chain(asset: str, *expiries: datetime, omit_put_at: datetime | None = None) -> list[dict]:
    center = 84000 if asset == "BTC" else 2700
    step = 200 if asset == "BTC" else 10
    return [
        {
            "symbol": f"{prefix}-{asset}-{strike}-{expiry:%d%m%y}",
            "strike": strike,
            "spot": center,
            "expiry": expiry.isoformat(),
        }
        for expiry in expiries
        for prefix in ("C", "P")
        if not (prefix == "P" and expiry == omit_put_at)
        for strike in range(center - 4 * step, center + 5 * step, step)
    ]


def template(asset: str = "BTC") -> dict:
    definitions = default_strategy_definitions if asset == "BTC" else eth_strategy_definitions
    item = next(row for row in definitions(datetime(2026, 9, 29, tzinfo=UTC)) if "Short ATM straddle" in row.name)
    return template_from_definition(item.model_dump(mode="json", exclude_none=True))


@pytest.mark.parametrize("asset", ["BTC", "ETH"])
@pytest.mark.parametrize("hours", [7, 11])
def test_evening_holds_use_upcoming_expiry_and_can_cross_midnight(asset: str, hours: int) -> None:
    entry = datetime(2026, 9, 29, 13, 45, tzinfo=UTC)
    expiry = datetime(2026, 9, 30, 12, tzinfo=UTC)
    live, detail = resolve_exit_schedule(
        template(asset), entry_at=entry, choice=ExitChoice(kind="intraday", hours=hours),
        options=chain(asset, expiry, expiry + timedelta(days=1)), trigger="new_york_session",
    )
    assert detail["durationMinutes"] == hours * 60
    assert detail["contractExpiryUtc"] == expiry.isoformat()
    assert detail["sessionCrossings"] == 0
    assert live["entry"]["strategyType"] == "intraday"


@pytest.mark.parametrize("trigger,hour,minute,duration", [
    ("asia_session", 0, 15, 420), ("london_session", 7, 15, 280), ("pre_expiry", 10, 15, 100),
])
@pytest.mark.parametrize("asset", ["BTC", "ETH"])
def test_presets_shorten_at_current_expiry_buffer(trigger, hour, minute, duration, asset):
    entry = datetime(2026, 9, 29, hour, minute, tzinfo=UTC)
    expiry = datetime(2026, 9, 29, 12, tzinfo=UTC)
    _, schedule = resolve_exit_schedule(
        template(asset), entry_at=entry, choice=ExitChoice(kind="intraday", hours=7),
        options=chain(asset, expiry), trigger=trigger,
    )
    assert schedule["durationMinutes"] == duration
    assert schedule["contractExpiryUtc"] == expiry.isoformat()


@pytest.mark.parametrize("kind,hours", [("overnight", 16), ("positional", 48), ("expiry", None)])
def test_removed_choices_rejected(kind, hours):
    with pytest.raises(ValueError):
        ExitChoice(kind=kind, hours=hours)


def test_second_expiry_parameter_rejected():
    with pytest.raises(ValueError):
        ExitChoice(kind="intraday", hours=7, expiry_number=2)


@pytest.mark.parametrize("trigger", [None, "manual", "asia_session", "london_session", "pre_expiry", "midnight_review"])
def test_eleven_hours_requires_evening_review(trigger):
    entry = datetime(2026, 9, 29, 13, tzinfo=UTC)
    with pytest.raises(ValueError, match="evening"):
        resolve_exit_schedule(
            template(), entry_at=entry, choice=ExitChoice(kind="intraday", hours=11),
            options=chain("BTC", datetime(2026, 9, 30, 12, tzinfo=UTC)), trigger=trigger,
        )


def test_missing_current_chain_does_not_fall_back_to_tomorrow():
    entry = datetime(2026, 9, 29, 10, 15, tzinfo=UTC)
    today = datetime(2026, 9, 29, 12, tzinfo=UTC)
    options = chain("BTC", today, today + timedelta(days=1), omit_put_at=today)
    with pytest.raises(ValueError, match="Upcoming session expiry"):
        resolve_exit_schedule(template(), entry_at=entry, choice=ExitChoice(kind="intraday", hours=7), options=options)


@pytest.mark.parametrize("hours", [8, 25])
def test_custom_exit_cannot_bypass_maximum_or_session(hours):
    entry = datetime(2026, 9, 29, 13, tzinfo=UTC)
    with pytest.raises(ValueError, match="maximum|session"):
        resolve_exit_schedule(
            template(), entry_at=entry, choice=ExitChoice(kind="specific_time", exit_at=entry + timedelta(hours=hours)),
            options=chain("BTC", datetime(2026, 9, 30, 12, tzinfo=UTC)),
        )


def test_entry_cannot_move_past_reviewed_expiry():
    review = datetime(2026, 9, 29, 10, tzinfo=UTC)
    entry = datetime(2026, 9, 29, 13, tzinfo=UTC)
    with pytest.raises(ValueError, match="reviewed"):
        resolve_exit_schedule(
            template(), entry_at=entry, choice=ExitChoice(kind="intraday", hours=7),
            options=chain("BTC", datetime(2026, 9, 30, 12, tzinfo=UTC)), review_at=review,
        )


def test_entry_inside_expiry_buffer_rejected():
    entry = datetime(2026, 9, 29, 11, 56, tzinfo=UTC)
    with pytest.raises(ValueError, match="buffer"):
        resolve_exit_schedule(
            template(), entry_at=entry, choice=ExitChoice(kind="intraday", hours=7),
            options=chain("BTC", datetime(2026, 9, 29, 12, tzinfo=UTC)),
        )


def test_specific_time_requires_timezone() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        ExitChoice(kind="specific_time", exit_at=datetime(2026, 9, 30))


def test_new_saved_template_contains_only_trading_rules_and_materializes_safely() -> None:
    saved = template()
    assert saved["schemaVersion"] == 3
    assert not any(key in saved for key in ("entry", "holdingMode", "expiryPolicy"))
    assert all("expiry" not in leg for leg in saved["legs"])
    live, _ = resolve_exit_schedule(
        saved,
        entry_at=datetime(2026, 9, 29, 13, tzinfo=UTC),
        choice=ExitChoice(kind="intraday", hours=7),
        options=chain("BTC", datetime(2026, 9, 30, 12, tzinfo=UTC)),
    )
    validate_materialized_definition(saved, live)


def test_agent_calculator_uses_the_same_resolver_as_selection() -> None:
    entry = datetime(2026, 9, 29, 13, tzinfo=UTC)
    options = chain("BTC", datetime(2026, 9, 30, 12, tzinfo=UTC))
    tool = object.__new__(AutomationStrategyTools)
    tool.strategy_references = {"S01": ("saved-1", 7)}
    tool.application_data = SimpleNamespace(selection_context=lambda *_args: ([
        {"version": 7, "enabled_for_ai": True, "definition_json": template()}
    ], {}))
    tool.runtime_data = SimpleNamespace(request_sync=lambda *_args: {
        "snapshot": {"id": "snapshot-1", "market_json": {
            "executionOptionContext": {"underlying": "BTC", "options": options}
        }}
    })
    tool.user_id = "user-1"
    tool.agent_run_id = "run-1"
    tool.market_snapshot_id = "snapshot-1"
    tool.asset = "BTC"
    choice = {"kind": "intraday", "hours": 7}
    preview = json.loads(tool.calculate_exit_time("S01", entry.isoformat(), choice))
    _, committed_schedule = tool._resolve_selection("saved-1", 7, entry, ExitChoice.model_validate(choice))
    assert preview == {"valid": True, "schedule": committed_schedule}
