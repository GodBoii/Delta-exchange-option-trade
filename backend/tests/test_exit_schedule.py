from datetime import UTC, datetime, timedelta

import pytest

from app.default_strategies import default_strategy_definitions, eth_strategy_definitions
from app.exit_schedule import ExitChoice, resolve_exit_schedule, template_from_definition


def chain(asset: str, *expiries: datetime, omit_put_at: datetime | None = None) -> list[dict]:
    center = 84000 if asset == "BTC" else 2700
    step = 200 if asset == "BTC" else 10
    return [
        {
            "symbol": f"{prefix}-{asset}-{strike}-{expiry:%d%m%y}",
            "strike": strike, "spot": center, "expiry": expiry.isoformat(),
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
@pytest.mark.parametrize("hours", [7, 11, 16, 24, 48, 72])
def test_preset_holds_choose_first_contract_covering_exit(asset: str, hours: int) -> None:
    entry = datetime(2026, 9, 29, 13, tzinfo=UTC)  # 18:30 IST
    expiries = [datetime(2026, 9, 30, 12, tzinfo=UTC) + timedelta(days=day) for day in range(5)]
    kind = "intraday" if hours in (7, 11) else "overnight" if hours in (16, 24) else "positional"
    # Sixteen hours from 18:30 IST stays in the same session and is deliberately rejected.
    if hours == 16:
        with pytest.raises(ValueError, match="overnight"):
            resolve_exit_schedule(template(asset), entry_at=entry, choice=ExitChoice(kind=kind, hours=hours), options=chain(asset, *expiries))
        return
    live, detail = resolve_exit_schedule(template(asset), entry_at=entry, choice=ExitChoice(kind=kind, hours=hours), options=chain(asset, *expiries))
    assert detail["durationMinutes"] == hours * 60
    assert all(datetime.fromisoformat(leg["expiry"]).date() == datetime.fromisoformat(detail["contractExpiryIst"]).date() for leg in live["legs"])
    assert datetime.fromisoformat(detail["latestSafeExitUtc"]) >= datetime.fromisoformat(detail["exitUtc"])
    assert live["expiryPolicy"] == "auto"


def test_session_boundary_is_at_1730_ist() -> None:
    entry = datetime(2026, 9, 29, 5, 30, tzinfo=UTC)  # 11:00 IST
    options = chain("BTC", datetime(2026, 9, 30, 12, tzinfo=UTC))
    with pytest.raises(ValueError, match="intraday"):
        resolve_exit_schedule(template(), entry_at=entry, choice=ExitChoice(kind="intraday", hours=7), options=options)
    late = datetime(2026, 9, 29, 12, 1, tzinfo=UTC)  # 17:31 IST
    live, detail = resolve_exit_schedule(template(), entry_at=late, choice=ExitChoice(kind="intraday", hours=7), options=options)
    assert detail["sessionCrossings"] == 0
    assert live["entry"]["strategyType"] == "intraday"


def test_expiry_choices_skip_short_hold_and_incomplete_chain() -> None:
    entry = datetime(2026, 9, 29, 10, 30, tzinfo=UTC)  # 16:00 IST
    today = datetime(2026, 9, 29, 12, tzinfo=UTC)
    tomorrow = today + timedelta(days=1)
    following = today + timedelta(days=2)
    options = chain("BTC", today, tomorrow, following, omit_put_at=tomorrow)
    first, detail = resolve_exit_schedule(template(), entry_at=entry, choice=ExitChoice(kind="expiry", expiry_number=1), options=options)
    assert first["legs"][0]["expiry"] == following.date().isoformat()
    assert detail["durationMinutes"] > 90
    with pytest.raises(ValueError, match="requested listed expiry"):
        resolve_exit_schedule(template(), entry_at=entry, choice=ExitChoice(kind="expiry", expiry_number=2), options=options)


def test_missing_chain_and_uncovered_exit_fail_without_shortening() -> None:
    entry = datetime(2026, 9, 29, 13, tzinfo=UTC)
    with pytest.raises(ValueError, match="no listed options"):
        resolve_exit_schedule(template(), entry_at=entry, choice=ExitChoice(kind="positional", hours=72), options=[])
    with pytest.raises(ValueError, match="full requested hold"):
        resolve_exit_schedule(template(), entry_at=entry, choice=ExitChoice(kind="positional", hours=72), options=chain("BTC", datetime(2026, 9, 30, 12, tzinfo=UTC)))


def test_specific_time_requires_timezone() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        ExitChoice(kind="specific_time", exit_at=datetime(2026, 9, 30))
