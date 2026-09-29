"""Pure strategy-unit payoff, Greek estimates and advisory book-fill calculations."""

import math
from datetime import datetime
from decimal import Decimal, InvalidOperation
from functools import reduce
from typing import Any

from app.automation_schedule import IST
from app.models import StrategyDefinition
from app.strategy import resolve_leg, validate_entry_policy


def number(value: Any) -> Decimal:
    if value is None or isinstance(value, bool):
        raise ValueError("Required numerical option evidence is unavailable")
    try:
        result = Decimal(str(value))
    except InvalidOperation as error:
        raise ValueError("Invalid option evidence") from error
    if not result.is_finite():
        raise ValueError("Non-finite option evidence")
    return result


def resolve_contracts(definition: dict, options: list[dict]) -> list[dict]:
    parsed = StrategyDefinition.model_validate(definition)
    result = []
    for leg in parsed.legs:
        # Listed contract expiry dates are defined in IST, not the entry's timezone.
        chain = [
            {
                "contract_type": o["type"],
                "strike_price": o["strike"],
                "spot_price": o.get("spot"),
                "symbol": o["symbol"],
                "product_id": o.get("productId", 0),
                "mark_price": o.get("mark"),
                "quotes": {"best_bid": o.get("bestBid"), "best_ask": o.get("bestAsk")},
            }
            for o in options
            if datetime.fromisoformat(str(o["expiry"]).replace("Z", "+00:00")).astimezone(IST).date() == leg.expiry
        ]
        result.append(resolve_leg(leg, chain))
    validate_entry_policy(parsed, result)
    return result


def fill_estimate(
    book: dict,
    side: str,
    quantity: Decimal,
    reference: Decimal,
    limit_price: Decimal | None = None,
) -> dict:
    levels = []
    for row in book.get("sell" if side == "buy" else "buy") or []:
        try:
            price, size = number(row.get("price")), number(row.get("size"))
        except ValueError:
            continue
        if price >= 0 and size > 0:
            if limit_price is not None and (
                (side == "buy" and price > limit_price) or (side == "sell" and price < limit_price)
            ):
                continue
            levels.append((price, size))
    levels.sort(reverse=side == "sell")
    remaining, cost = quantity, Decimal(0)
    for price, size in levels:
        used = min(size, remaining)
        cost += used * price
        remaining -= used
        if remaining == 0:
            break
    average = cost / quantity if remaining == 0 else None
    return {
        "requestedContracts": int(quantity),
        "filledContracts": float(quantity - remaining),
        "estimatedPremium": float(average) if average is not None else None,
        "slippagePercent": float((average - reference) / reference * 100 * (1 if side == "buy" else -1))
        if average is not None and reference > 0
        else None,
    }


def greek_unit_checks(row: dict, now: int) -> dict[str, bool]:
    """Check Delta's live per-point vega/per-day theta scaling against Greek identities.

    Do not price an option. Inconsistent scaling leaves those estimates unknown.
    Delta/gamma definitions are documented in Delta's Greeks schema.
    """
    try:
        spot, sigma, gamma, vega, theta = (
            float(number(row.get(k))) for k in ("spot", "impliedVolatility", "gamma", "vega", "theta")
        )
        expiry = int(datetime.fromisoformat(row["expiry"].replace("Z", "+00:00")).timestamp() * 1000)
        years = (expiry - now) / (365 * 86_400_000)
        expected_vega = gamma * spot * spot * sigma * years / 100
        expected_theta = -gamma * spot * spot * sigma * sigma / (2 * 365)
        return {
            "vega": expected_vega > 0 and abs(vega / expected_vega - 1) < 0.25,
            "theta": expected_theta < 0 and abs(theta / expected_theta - 1) < 0.25,
        }
    except (ValueError, KeyError, TypeError, ZeroDivisionError):
        return {"vega": False, "theta": False}


def build_preview(definition: dict, resolved: list[dict], quotes: list[dict], now: int) -> dict:
    divisor = reduce(math.gcd, (int(leg["lots"]) for leg in resolved))
    by_symbol = {q["symbol"]: q for q in quotes}
    terms = []
    evidence = []
    credit = Decimal(0)
    fees: Decimal | None = Decimal(0)
    aggregates: dict[str, Decimal | None] = {k: Decimal(0) for k in ("delta", "gamma", "vega", "theta")}
    spot = None
    for leg in resolved:
        row = by_symbol.get(leg["productSymbol"])
        if row is None or not 0 <= now - int(row.get("observedAt", 0)) <= 30_000:
            raise ValueError("Fresh selected-contract evidence is unavailable")
        if row["type"] != ("call_options" if leg["optionType"] == "call" else "put_options"):
            raise ValueError("Selected option type mismatch")
        bid, ask = number(row.get("bestBid")), number(row.get("bestAsk"))
        if not 0 <= bid <= ask or ask <= 0:
            raise ValueError("Invalid executable option quote")
        quantity = Decimal(int(leg["lots"]) // divisor)
        multiplier, strike = number(row.get("contractValue")), number(row.get("strike"))
        if multiplier <= 0 or strike != number(leg["strike"]):
            raise ValueError("Selected contract metadata mismatch")
        weight = quantity * multiplier * (1 if leg["position"] == "buy" else -1)
        price = ask if leg["position"] == "buy" else bid
        if price <= 0:
            raise ValueError("Selected contract has no positive executable quote")
        credit -= weight * price
        terms.append((strike, weight, leg["optionType"]))
        spot = number(row.get("spot"))
        checks = greek_unit_checks(row, now)
        for key in aggregates:
            value = row.get(key)
            verified = checks.get(key, True)
            if value is None or not verified:
                aggregates[key] = None
            elif aggregates[key] is not None:
                aggregates[key] += number(value) * weight
        rate = row.get("takerFeeRate")
        if rate is None or fees is None:
            fees = None
        else:
            fees += min(spot * abs(weight) * number(rate), price * abs(weight) * Decimal("0.035"))
        evidence.append(
            {
                "symbol": row["symbol"],
                "position": leg["position"],
                "orderType": leg["orderType"],
                **({"limitPrice": leg["limitPrice"]} if leg.get("limitPrice") else {}),
                "contracts": int(quantity),
                "strike": float(strike),
                "premium": float(price),
                "multiplier": float(multiplier),
                "ivPercent": float(number(row["impliedVolatility"]) * 100)
                if row.get("impliedVolatility") is not None
                else None,
                "spreadPercent": float((ask - bid) / ((ask + bid) / 2) * 100),
                "bidSizeContracts": row.get("bidSize"),
                "askSizeContracts": row.get("askSize"),
                "observedAt": row["observedAt"],
                "fill": fill_estimate(
                    row.get("depth") or {},
                    leg["position"],
                    quantity,
                    price,
                    number(leg["limitPrice"]) if leg.get("limitPrice") else None,
                ),
            }
        )

    def payoff(at: Decimal) -> Decimal:
        return credit + sum(
            (
                weight * max(Decimal(0), at - strike if kind == "call" else strike - at)
                for strike, weight, kind in terms
            ),
            Decimal(0),
        )

    knots = sorted({Decimal(0), *(strike for strike, _, _ in terms)})
    values = [payoff(at) for at in knots]
    slope = sum((weight for _, weight, kind in terms if kind == "call"), Decimal(0))
    roots = set()
    for left, right in zip(knots, knots[1:], strict=False):
        a, b = payoff(left), payoff(right)
        if a == 0:
            roots.add(left)
        if a * b < 0:
            roots.add(left - a * (right - left) / (b - a))
    if values[-1] == 0:
        roots.add(knots[-1])
    if slope and knots[-1] - values[-1] / slope > knots[-1]:
        roots.add(knots[-1] - values[-1] / slope)
    scenarios = []
    if spot is not None:
        for percent in (-1, -0.5, 0.5, 1):
            move = spot * Decimal(str(percent)) / 100
            d, g = aggregates["delta"], aggregates["gamma"]
            scenarios.append(
                {
                    "spotChangePercent": percent,
                    "estimatedValueChangeUsd": float(d * move + g * move * move / 2)
                    if d is not None and g is not None
                    else None,
                }
            )
    for points in (-5, 5):
        scenarios.append(
            {
                "ivChangePoints": points,
                "estimatedValueChangeUsd": float(aggregates["vega"] * points)
                if aggregates["vega"] is not None
                else None,
            }
        )
    scenarios.append(
        {
            "elapsedHours": 1,
            "estimatedValueChangeUsd": float(aggregates["theta"] / 24) if aggregates["theta"] is not None else None,
        }
    )
    return {
        "unit": "one normalized saved-leg ratio; account sizing happens at entry",
        "legs": evidence,
        "netCreditUsd": float(credit),
        "entryFeesUsdExcludingTax": float(fees) if fees is not None else None,
        "expiryPayoff": {
            "breakevens": [float(r) for r in sorted(roots)],
            "maximumLossUsd": "unbounded" if slope < 0 else float(max(Decimal(0), -min(values))),
            "maximumProfitUsd": "unbounded" if slope > 0 else float(max(Decimal(0), max(values))),
            "basis": "gross, at expiry; software stops do not bound payoff",
        },
        "greeks": {k: float(v) if v is not None else None for k, v in aggregates.items()},
        "localEstimates": {
            "basis": "separate small changes, not forecasts; USD change from current value",
            "scenarios": scenarios,
        },
        "liquidity": "advisory; normalized unit only",
    }
