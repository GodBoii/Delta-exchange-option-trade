"""Causal, fixed-quantity strike comparisons for offline option research.

Chain observations establish listed strikes, not executable entry prices. Prices
come from completed option-mark bars. This module has no network or order client.
"""

from dataclasses import replace
from decimal import Decimal
from typing import Any

from scripts.option_replay import Candle, Leg, PaperExit, Policy, latest_candle, replay_policy


def shifted_contracts(
    legs: list[Leg],
    snapshots: dict[int, list[dict[str, Any]]],
    *,
    entry_at: int,
    spot: Decimal,
    steps: int,
    max_chain_age_seconds: int = 600,
) -> list[dict[str, Any]] | None:
    """Shift outward from the actual ATM strikes using one known historical chain.

    Only causal observations are eligible. A ten-minute chain is listing evidence;
    its old bid/ask must never be described as the entry-time executable quote.
    """
    if steps not in {0, 1, 2} or not legs or not spot.is_finite() or spot <= 0:
        raise ValueError("Expected legs, positive spot and zero, one or two strike steps")
    entry_ms = entry_at * 1000
    times = [t for t in snapshots if 0 <= entry_ms - t <= max_chain_age_seconds * 1000]
    if not times:
        return None
    timestamp = max(times)
    rows = [
        row
        for row in snapshots[timestamp]
        if 0 <= row["observedAt"] <= entry_ms
        and 0 <= row["receivedAt"] <= entry_ms
        and entry_ms - row["observedAt"] <= (max_chain_age_seconds + 30) * 1000
    ]
    selected = []
    for leg in legs:
        chain = sorted(
            (row for row in rows if row["type"] == f"{leg.kind}_options" and row["expiry"] == leg.expiry),
            key=lambda row: Decimal(str(row["strike"])),
        )
        anchor = next((i for i, row in enumerate(chain) if row["symbol"] == leg.symbol), None)
        if anchor is None:
            return None
        index = anchor + steps * (1 if leg.kind == "call" else -1)
        if not 0 <= index < len(chain):
            return None
        row = chain[index]
        strike = Decimal(str(row["strike"]))
        if steps and (strike <= spot if leg.kind == "call" else strike >= spot):
            return None
        if Decimal(str(row["contractValue"])) != leg.multiplier:
            raise ValueError("Counterfactual contract multiplier differs from original")
        selected.append({**row, "chainSnapshotAt": timestamp})
    return selected


def mark_entry_legs(
    originals: list[Leg],
    selected: list[dict[str, Any]],
    series: dict[str, dict[int, Candle]],
    *,
    entry_at: int,
    emergency_percent: Decimal | None,
) -> list[Leg] | None:
    """Use pre-entry completed marks and preserve original whole-contract sizes."""
    result = []
    for original, row in zip(originals, selected, strict=True):
        bar = latest_candle(series.get(row["symbol"], {}), entry_at)
        if bar is None or bar.close <= 0:
            return None
        stop = None
        if original.side == "sell" and emergency_percent is not None:
            stop = bar.close * (1 + emergency_percent / 100)
        result.append(
            replace(
                original,
                symbol=row["symbol"],
                strike=Decimal(str(row["strike"])),
                entry=bar.close,
                fee_rate=Decimal(str(row["takerFeeRate"])),
                stop_price=stop,
                target_price=None,
            )
        )
    return result


def continuous_replay(
    legs: list[Leg],
    series: dict[str, dict[int, Candle]],
    underlying: dict[int, Candle],
    policy: Policy,
    *,
    entry_at: int,
    intrabar_emergency: bool,
) -> PaperExit | None:
    """Reject gaps before the exit; do not silently skip a possible stop minute."""
    exit_result = replay_policy(
        legs,
        series,
        underlying,
        policy,
        entry_at=entry_at,
        include_brackets=True,
        intrabar_emergency=intrabar_emergency,
    )
    if exit_result is None:
        return None
    expected = range(entry_at // 60 * 60, exit_result.at, 60)
    if any(t not in underlying or any(t not in series.get(leg.symbol, {}) for leg in legs) for t in expected):
        return None
    return exit_result
