"""Summarize observations using the trading scheduler's session boundaries."""

from datetime import UTC, datetime, timedelta
from typing import Any

from app.automation_schedule import IST, fixed_runs_between

STEP_MS = 600_000


def session_history(payload: dict[str, Any], now: datetime, trigger: str | None = None) -> dict[str, Any]:
    now = now.astimezone(UTC)
    boundaries = fixed_runs_between(now - timedelta(days=3), now)
    matching = [b for b in boundaries if b.trigger == (trigger or boundaries[-1].trigger)]
    if len(matching) < 2:
        matching = [b for b in boundaries if b.trigger == boundaries[-1].trigger]
    start = matching[-2].scheduled_for
    periods = [b for b in boundaries if b.scheduled_for >= start]
    rows = payload.get("observations") or []

    def summary(begin: datetime, end: datetime) -> dict[str, Any]:
        lower, upper = int(begin.timestamp() * 1000), int(end.timestamp() * 1000)
        selected = [r for r in rows if lower < r["end"] <= upper and r["end"] - STEP_MS >= lower]
        count = len(selected)
        expected = max(0, upper // STEP_MS - (lower + STEP_MS - 1) // STEP_MS)
        return {
            "start": begin.astimezone(IST).isoformat(),
            "end": end.astimezone(IST).isoformat(),
            "sampleCount": count,
            "expectedSamples": expected,
            "coveragePercent": round(count * STEP_MS / (upper - lower) * 100, 1) if upper > lower else None,
            "averageSidewaysScorePercent": sum(r["sidewaysScore"] for r in selected) / count if count else None,
            "averageVolatilityAnnualizedPercent": (
                sum(r["volatilityAnnualizedPercent"] for r in selected) / count if count else None
            ),
            "observedTradedVolumeBtc": sum(r["volumeBtc"] for r in selected) if count else None,
            "observedTradedVolumeUsdt": sum(r["volumeUsdt"] for r in selected) if count else None,
            "averageTenMinuteVolumeBtc": sum(r["volumeBtc"] for r in selected) / count if count else None,
        }

    return {
        "available": payload.get("available", False),
        "error": payload.get("error"),
        "asOf": now.astimezone(IST).isoformat(),
        "lastObservationAt": (
            datetime.fromtimestamp(max(r["end"] for r in rows) / 1000, UTC).astimezone(IST).isoformat()
            if rows
            else None
        ),
        "samplingMinutes": 10,
        "method": (
            "Session intervals run from one fixed review opening to the next, including weekends. "
            "Sideways is the mean of sampled 60-minute scores on a 0-100 scale, not a calibrated forecast. "
            "Volatility is the mean of sampled 120-return annualized realized volatility percentages. "
            "Volume sums disjoint completed ten-minute buckets; incomplete coverage is not a full-session total."
        ),
        "recent": {f"last{hours}Hour": summary(now - timedelta(hours=hours), now) for hours in (1, 2)},
        "sessions": [
            {
                "session": period.trigger,
                "partial": i == len(periods) - 1,
                **summary(period.scheduled_for, periods[i + 1].scheduled_for if i + 1 < len(periods) else now),
            }
            for i, period in reversed(list(enumerate(periods)))
            if period.scheduled_for < now
        ],
    }
