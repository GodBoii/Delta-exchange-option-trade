"""Numerical market summaries. Raw exchange arrays never belong in agent input."""

import math
from datetime import datetime
from typing import Any


def finite(value: Any, *, positive: bool = False) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (ValueError, TypeError):
        return None
    return result if math.isfinite(result) and (not positive or result > 0) else None


def timestamp_ms(value: Any, now: int) -> int | None:
    value = finite(value, positive=True)
    if value is None:
        return None
    if value > 10_000_000_000_000:
        value /= 1000
    elif value < 10_000_000_000:
        value *= 1000
    return int(value) if value <= now + 5000 else None


def instant(value: Any) -> int | None:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return int(dt.timestamp() * 1000) if dt.utcoffset() is not None else None
    except (ValueError, TypeError, OverflowError):
        return None


def normalize_option(raw: dict[str, Any], product: dict[str, Any], asset: str, now: int) -> dict[str, Any]:
    symbol = str(raw.get("symbol") or "")
    parts = symbol.split("-")
    if len(parts) != 4 or parts[0] not in {"C", "P"} or parts[1] != asset:
        raise ValueError("Option symbol does not match asset")
    if product.get("symbol") != symbol:
        raise ValueError("Option metadata does not match symbol")
    expiry = instant(product.get("settlement_time"))
    strike = finite(raw.get("strike_price"), positive=True)
    multiplier = finite(product.get("contract_value"), positive=True)
    observed = timestamp_ms(raw.get("timestamp"), now)
    if expiry is None or expiry <= now or strike is None or multiplier is None or observed is None:
        raise ValueError("Option metadata or timestamp is invalid")
    quotes = raw.get("quotes") if isinstance(raw.get("quotes"), dict) else {}
    greeks = raw.get("greeks") if isinstance(raw.get("greeks"), dict) else {}
    product_id = finite(product.get("id"), positive=True)
    ticker_id = finite(raw.get("product_id"), positive=True)
    if product_id is None or ticker_id != product_id or not product_id.is_integer():
        raise ValueError("Option product ID is invalid or mismatched")
    iv = finite(raw.get("mark_vol"))
    if iv is not None and not 0 <= iv <= 10:
        iv = None
    result = {
        "symbol": symbol, "type": "call_options" if parts[0] == "C" else "put_options",
        "expiry": product["settlement_time"], "expiryMs": expiry, "strike": strike,
        "spot": finite(raw.get("spot_price"), positive=True), "mark": finite(raw.get("mark_price")),
        "bestBid": finite(quotes.get("best_bid")), "bestAsk": finite(quotes.get("best_ask")),
        "bidSize": finite(quotes.get("bid_size")), "askSize": finite(quotes.get("ask_size")),
        "impliedVolatility": iv, "openInterest": finite(raw.get("oi")),
        "volume": finite(raw.get("volume")), "contractValue": multiplier,
        "observedAt": observed, "receivedAt": now,
        "productId": int(product_id),
        "takerFeeRate": finite(product.get("taker_commission_rate")),
    }
    result.update({key: finite(greeks.get(key)) for key in ("delta", "gamma", "theta", "vega")})
    for key in ("mark", "bestBid", "bestAsk", "bidSize", "askSize", "openInterest", "volume", "takerFeeRate", "gamma"):
        if result[key] is not None and result[key] < 0:
            result[key] = None
    if result["delta"] is not None and not -1 <= result["delta"] <= 1:
        result["delta"] = None
    # Delta mark_vol and quote IVs are fractions, not percentages.
    for side in ("bid", "ask"):
        value = finite(quotes.get(f"{side}_iv"))
        result[f"{side}Iv"] = value if value is not None and 0 <= value <= 10 else None
    return result


def depth_summary(
    bids: dict[float, float], asks: dict[float, float],
    known_bid_floor: float | None = None, known_ask_ceiling: float | None = None,
) -> dict[str, Any]:
    if not bids or not asks:
        return {}
    best_bid, best_ask = max(bids), min(asks)
    if best_bid <= 0 or best_ask <= best_bid:
        return {}
    mid = (best_bid + best_ask) / 2
    bands = {}
    for percent in (0.1, 0.5, 1.0):
        lower, upper = mid * (1 - percent / 100), mid * (1 + percent / 100)
        complete = (known_bid_floor if known_bid_floor is not None else min(bids)) <= lower and (
            known_ask_ceiling if known_ask_ceiling is not None else max(asks)) >= upper
        bid = sum(q for p, q in bids.items() if p >= lower)
        ask = sum(q for p, q in asks.items() if p <= upper)
        bands[str(percent)] = {
            "bidBase": bid, "askBase": ask, "complete": complete,
            "imbalance": (bid - ask) / (bid + ask) if bid + ask else None,
        }
    return {"spreadBps": (best_ask - best_bid) / mid * 10_000, "bands": bands}


def option_overview(options: list[dict[str, Any]], now: int, extra_expiries: set[int] | None = None) -> list[dict]:
    usable = [o for o in options if o["expiryMs"] > now and 0 <= now - o["observedAt"] <= 90_000]
    expiries = sorted({o["expiryMs"] for o in usable})
    if not expiries:
        return []
    selected = {min(expiries, key=lambda e: abs(e - now - days * 86_400_000)) for days in (1, 3, 7, 30)}
    selected.update((extra_expiries or set()) & set(expiries))
    rows = []
    for expiry in sorted(selected):
        chain = [o for o in usable if o["expiryMs"] == expiry]
        spot = next((o["spot"] for o in chain if o.get("spot")), None)
        if spot is None:
            continue
        atm = {}
        for kind in ("call_options", "put_options"):
            side = [o for o in chain if o["type"] == kind]
            if side:
                atm[kind] = min(side, key=lambda o: abs(o["strike"] - spot))
        call, put = atm.get("call_options", {}), atm.get("put_options", {})
        ivs = [o["impliedVolatility"] for o in atm.values() if o.get("impliedVolatility") is not None]
        oi = [o for o in chain if o.get("openInterest") is not None and o["openInterest"] >= 0]
        total_oi = sum(o["openInterest"] for o in oi)
        peak = max(oi, key=lambda o: o["openInterest"]) if oi else None
        spreads = []
        for o in atm.values():
            bid, ask = o.get("bestBid"), o.get("bestAsk")
            if bid is not None and ask is not None and ask > 0 and 0 <= bid <= ask:
                spreads.append((ask - bid) / ((ask + bid) / 2) * 100)
        rows.append({
            "expiry": chain[0]["expiry"], "daysRemaining": round((expiry - now) / 86_400_000, 3),
            "atmIvPercent": sum(ivs) / len(ivs) * 100 if ivs else None,
            "ivScaledMoveToExpiryPercent": sum(ivs) / len(ivs) * math.sqrt(
                (expiry - now) / (365 * 86_400_000)) * 100 if ivs else None,
            "putMinusCallIvPoints": (put["impliedVolatility"] - call["impliedVolatility"]) * 100
            if put.get("impliedVolatility") is not None and call.get("impliedVolatility") is not None
            and put.get("strike") == call.get("strike") else None,
            "oiBase": total_oi if oi else None, "oiCoverage": len(oi) / len(chain),
            "largestOiStrike": peak["strike"] if peak else None,
            "largestOiSharePercent": peak["openInterest"] / total_oi * 100 if peak and total_oi else None,
            "atmSpreadPercent": sum(spreads) / len(spreads) if spreads else None,
            "atmMaximumSpreadPercent": max(spreads) if spreads else None,
            "volume24hBase": sum(o["volume"] for o in chain if o.get("volume") is not None)
            if all(o.get("volume") is not None for o in chain) else None,
            "quotedContracts": len(chain), "atmSymbols": [o["symbol"] for o in atm.values()],
            "observedAt": min(o["observedAt"] for o in chain),
        })
    return rows


class LiquidityWindow:
    """One-second samples, bounded by ten-minute buckets; no tick archive."""

    def __init__(self, start: int) -> None:
        self.start = start
        self.count = 0
        self.spread_total = 0.0
        self.spread_max = 0.0
        self.bands: dict[str, dict[str, float]] = {}

    def add(self, sample: dict[str, Any]) -> None:
        if not sample:
            return
        self.count += 1
        self.spread_total += sample["spreadBps"]
        self.spread_max = max(self.spread_max, sample["spreadBps"])
        for band, row in sample["bands"].items():
            if not row["complete"]:
                continue
            stats = self.bands.setdefault(band, {"samples": 0, "bidTotal": 0, "askTotal": 0,
                                                 "bidMin": math.inf, "askMin": math.inf, "imbalanceTotal": 0})
            stats["samples"] += 1
            for side in ("bid", "ask"):
                stats[f"{side}Total"] += row[f"{side}Base"]
                stats[f"{side}Min"] = min(stats[f"{side}Min"], row[f"{side}Base"])
            stats["imbalanceTotal"] += row["imbalance"] or 0

    def summary(self, end: int) -> dict[str, Any]:
        expected = max(1, (end - self.start) // 1000)
        return {
            "start": self.start, "end": end, "samples": self.count, "expectedSamples": expected,
            "coveragePercent": min(100, self.count / expected * 100),
            "spreadAverageBps": self.spread_total / self.count if self.count else None,
            "spreadMaximumBps": self.spread_max if self.count else None,
            "bands": {band: {
                "samples": s["samples"], "coveragePercent": min(100, s["samples"] / expected * 100),
                "bidAverageBase": s["bidTotal"] / s["samples"], "askAverageBase": s["askTotal"] / s["samples"],
                "bidMinimumBase": s["bidMin"], "askMinimumBase": s["askMin"],
                "imbalanceAverage": s["imbalanceTotal"] / s["samples"],
            } for band, s in self.bands.items()},
        }
