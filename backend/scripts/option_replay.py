"""Offline option-trade replay mathematics. This module cannot submit orders.

Historical bars are observations available at their end, not executable quotes.
Counterfactual stop replays intentionally report their one-minute limitations.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from app.strategy import strategy_level_metrics

ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class Candle:
    start: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal

    def __post_init__(self) -> None:
        values = (self.open, self.high, self.low, self.close)
        if not all(v.is_finite() and v >= 0 for v in values):
            raise ValueError("Candle prices must be finite and nonnegative")
        if not self.low <= min(self.open, self.close) <= max(self.open, self.close) <= self.high:
            raise ValueError("Candle OHLC values disagree")

    @property
    def end(self) -> int:
        return self.start + 60


@dataclass(frozen=True, slots=True)
class Leg:
    symbol: str
    side: Literal["buy", "sell"]
    kind: Literal["call", "put"]
    strike: Decimal
    contracts: Decimal
    multiplier: Decimal
    entry: Decimal
    expiry: str
    fee_rate: Decimal
    premium_fee_cap: Decimal
    stop_price: Decimal | None = None
    target_price: Decimal | None = None

    def __post_init__(self) -> None:
        for value in (self.strike, self.contracts, self.multiplier):
            if not value.is_finite() or value <= 0:
                raise ValueError("Strike, contracts and multiplier must be positive")
        if self.contracts != self.contracts.to_integral_value():
            raise ValueError("Option contracts must be whole numbers")
        for value in (self.entry, self.fee_rate, self.premium_fee_cap):
            if not value.is_finite() or value < 0:
                raise ValueError("Price and fee inputs must be finite and nonnegative")

    @property
    def units(self) -> Decimal:
        return self.contracts * self.multiplier

    @property
    def direction(self) -> Decimal:
        return Decimal("1") if self.side == "buy" else Decimal("-1")

    def profit(self, price: Decimal) -> Decimal:
        return (price - self.entry) * self.units * self.direction

    def intrinsic(self, spot: Decimal) -> Decimal:
        return max(ZERO, spot - self.strike if self.kind == "call" else self.strike - spot)

    def estimated_close_fee(self, price: Decimal, spot: Decimal) -> Decimal:
        return min(spot * self.units * self.fee_rate, price * self.units * self.premium_fee_cap) * Decimal("1.18")


@dataclass(frozen=True, slots=True)
class Policy:
    risk_mode: str
    risk_basis: str
    stop_percent: Decimal
    target_percent: Decimal
    deadline: int


@dataclass(frozen=True, slots=True)
class PaperExit:
    at: int
    reason: str
    gross: Decimal
    estimated_close_fees: Decimal
    prices: tuple[Decimal, ...]
    observed_intrabar_emergency_crossings: int
    observed_intrabar_combined_crossings: int


def latest_candle(candles: dict[int, Candle], at: int, max_age: int = 120) -> Candle | None:
    """Use only fully completed bars; never leak the unfinished bar's close."""
    start = (at // 60 - 1) * 60
    for key in range(start, start - max_age - 1, -60):
        bar = candles.get(key)
        if bar is not None and 0 <= at - bar.end <= max_age:
            return bar
    return None


def metrics(legs: list[Leg], prices: list[Decimal], policy: Policy) -> dict:
    return strategy_level_metrics(
        [
            {
                "side": leg.side,
                "filled_size": leg.contracts,
                "contract_value": leg.multiplier,
                "entry_price": leg.entry,
                "mark_price": price,
                "strike": leg.strike,
                "option_type": leg.kind,
                "expiry": leg.expiry,
            }
            for leg, price in zip(legs, prices, strict=True)
        ],
        risk_basis=policy.risk_basis,
        stop_percent=policy.stop_percent,
        take_profit_percent=policy.target_percent,
    )


def bracket_trigger(leg: Leg, price: Decimal) -> str | None:
    if leg.stop_price is not None and (
        price <= leg.stop_price if leg.side == "buy" else price >= leg.stop_price
    ):
        return "leg_stop"
    if leg.target_price is not None and (
        price >= leg.target_price if leg.side == "buy" else price <= leg.target_price
    ):
        return "leg_target"
    return None


def replay_policy(
    legs: list[Leg],
    series: dict[str, dict[int, Candle]],
    underlying: dict[int, Candle],
    policy: Policy,
    *,
    entry_at: int,
    include_brackets: bool,
    financial_triggers: bool = True,
) -> PaperExit | None:
    """Replay completed-bar triggers, using the same combined mathematics as production.

    Emergency and combined intrabar crossings are separately counted. Simultaneous
    leg extrema are only bounds; they are never treated as an observed fill.
    Paper exits occur at the observed close, so they assume mark-price liquidity.
    Missing bars never become zero-price quotes or a profitable expiry.
    """
    if not legs or policy.deadline <= entry_at:
        return None
    common = sorted(set.intersection(*(set(series.get(leg.symbol, {})) for leg in legs)))
    emergency_crossings = combined_crossings = 0
    last: PaperExit | None = None
    for start in common:
        bars = [series[leg.symbol][start] for leg in legs]
        at = bars[0].end
        if start < entry_at or at > policy.deadline:
            continue
        spot_bar = latest_candle(underlying, at, max_age=0)
        if spot_bar is None:
            continue
        prices = [bar.close for bar in bars]
        if include_brackets:
            emergency_crossings += sum(
                bracket_trigger(leg, bar.low if leg.side == "buy" else bar.high) == "leg_stop"
                for leg, bar in zip(legs, bars, strict=True)
            )
        combined = metrics(legs, prices, policy)
        # Each option's favorable/adverse extremes need not happen together.
        if policy.risk_mode != "legwise":
            favorable = [bar.high if leg.side == "buy" else bar.low for leg, bar in zip(legs, bars, strict=True)]
            adverse = [bar.low if leg.side == "buy" else bar.high for leg, bar in zip(legs, bars, strict=True)]
            combined_crossings += int(bool(metrics(legs, favorable, policy)["target_triggered"]))
            combined_crossings += int(bool(metrics(legs, adverse, policy)["stop_triggered"]))
        reason = "scheduled_exit"
        if financial_triggers:
            brackets = [bracket_trigger(leg, price) for leg, price in zip(legs, prices, strict=True)]
            if include_brackets and any(brackets):
                reason = "external_leg_exit"
            elif policy.risk_mode != "legwise" and combined["stop_triggered"]:
                reason = "stop_loss"
            elif policy.risk_mode != "legwise" and combined["target_triggered"]:
                reason = "take_profit"
        gross = sum((leg.profit(price) for leg, price in zip(legs, prices, strict=True)), ZERO)
        fees = sum(
            (leg.estimated_close_fee(price, spot_bar.close) for leg, price in zip(legs, prices, strict=True)), ZERO
        )
        last = PaperExit(at, reason, gross, fees, tuple(prices), emergency_crossings, combined_crossings)
        if reason != "scheduled_exit":
            return last
    if last is None or policy.deadline - last.at > 120:
        return None
    return last


def short_pair_breakevens(legs: list[Leg]) -> tuple[Decimal, Decimal] | None:
    """Gross expiry breakevens, not earlier-exit profit boundaries."""
    if len(legs) != 2 or any(leg.side != "sell" for leg in legs):
        return None
    calls = [leg for leg in legs if leg.kind == "call"]
    puts = [leg for leg in legs if leg.kind == "put"]
    if len(calls) != 1 or len(puts) != 1 or calls[0].units != puts[0].units:
        return None
    call, put = calls[0], puts[0]
    if put.strike > call.strike or call.expiry != put.expiry:
        return None
    credit_per_unit = call.entry + put.entry
    return put.strike - credit_per_unit, call.strike + credit_per_unit
