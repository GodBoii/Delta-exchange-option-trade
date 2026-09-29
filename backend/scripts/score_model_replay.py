"""Fetch actual Delta candles and report paired historical replay comparisons."""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean
from zoneinfo import ZoneInfo

import httpx

IST = ZoneInfo("Asia/Kolkata")
# Declared before outcome inspection. These are spot-price proxies, not option P&L.
THRESHOLDS = {"BTC": 0.25, "ETH": 0.50}
HORIZONS = (1, 3, 6)


def fetch_candles(symbol: str, start: int, end: int) -> list[dict]:
    candles = {}
    with httpx.Client(timeout=30) as client:
        for cursor in range(start // 60 * 60, end, 900 * 60):
            response = client.get(
                "https://api.india.delta.exchange/v2/history/candles",
                params={"symbol": symbol, "resolution": "1m", "start": cursor, "end": min(cursor + 900 * 60, end)},
            )
            response.raise_for_status()
            data = response.json()
            if data.get("success") is not True or not isinstance(data.get("result"), list):
                raise ValueError(f"Invalid candle response for {symbol}")
            for candle in data["result"]:
                if int(candle["time"]) + 60 <= end:
                    candles[int(candle["time"])] = candle
    return [candles[t] for t in sorted(candles)]


def price_outcome(candles: list[dict], as_of: int, hours: int, cutoff: int, threshold: float) -> dict:
    """Enter at next complete minute open, never at a pre-decision candle close."""
    entry = (as_of // 60 + 1) * 60
    exit_time = entry + hours * 3600
    if exit_time > cutoff:
        return {"status": "immature"}
    rows = [row for row in candles if entry <= int(row["time"]) < exit_time]
    expected = hours * 60
    if len(rows) != expected or any(int(row["time"]) != entry + i * 60 for i, row in enumerate(rows)):
        return {"status": "missing_candles", "count": len(rows), "expected": expected}
    price = float(rows[0]["open"])
    if price <= 0:
        raise ValueError("Nonpositive entry price")
    change = (float(rows[-1]["close"]) / price - 1) * 100
    excursion = max(abs(float(row[key]) / price - 1) * 100 for row in rows for key in ("high", "low"))
    direction = "bullish" if change > threshold else "bearish" if change < -threshold else "sideways"
    return {
        "status": "complete",
        "entry": entry,
        "exit": exit_time,
        "entry_price": price,
        "exit_price": rows[-1]["close"],
        "return_percent": change,
        "max_excursion_percent": excursion,
        "direction": direction,
        "range_held": excursion <= threshold * 2,
    }


def regime_label(report: str | None) -> str:
    """Conservative label of the declared regime heading, not an inferred forecast."""
    match = re.search(r"##\s*Market regime\s*(.*?)(?=\n## |\Z)", report or "", flags=re.I | re.S)
    if not match:
        return "unscored"
    # Restrict to the leading statement. Invalidation and scenario text are not predictions.
    lead = match.group(1).strip().split("\n\n", 1)[0][:240].lower()
    if "sideways" in lead or "range-bound" in lead or "range bound" in lead:
        return "sideways"
    bullish = bool(re.search(r"bullish|uptrend|upside breakout", lead))
    bearish = bool(re.search(r"bearish|downtrend|downside breakout", lead))
    if bullish == bearish:
        return "unscored"
    return "bullish" if bullish else "bearish"


def selection(tools: list[dict], catalog_tools: list[dict]) -> dict:
    catalog = {}
    for call in catalog_tools:
        if (call.get("tool_name") or call.get("name")) == "show_available_strategy":
            value = call.get("result")
            data = json.loads(value) if isinstance(value, str) else value
            catalog = {s["strategyRef"]: s for s in (data or {}).get("strategies", [])}
            break
    candidates = [t for t in tools if (t.get("tool_name") or t.get("name")) == "select_strategy_and_time"]
    if not candidates:
        return {"strategy": "no selection call", "activation_time": None, "attempts": 0}
    call = candidates[-1]
    args = call.get("tool_args", call.get("args")) or {}
    if isinstance(args, str):
        args = json.loads(args)
    strategy = catalog.get(args.get("strategy_ref"), {})
    return {
        "strategy": strategy.get("name", args.get("strategy_ref", "unknown")),
        "activation_time": args.get("activation_time"),
        "attempts": len(candidates),
        "exit_choice": args.get("exit_choice"),
        "reasoning_summary": args.get("reasoning_summary"),
        "supporting_signals": args.get("supporting_signals"),
        "invalidation_signals": args.get("invalidation_signals"),
    }


def compare(cases_document: dict, results: dict, market: dict) -> list[dict]:
    cutoff = int(datetime.fromisoformat(cases_document["window"]["end"]).timestamp())
    rows = []
    for case in cases_document["cases"]:
        result = results.get(case["id"], {"status": "missing"})
        original = case["original"]
        as_of = int(case["captured_at"] / 1000) if case.get("captured_at") else case["created_at"]
        truth = {
            str(h): price_outcome(market[case["asset"]], as_of, h, cutoff, THRESHOLDS[case["asset"]]) for h in HORIZONS
        }
        baseline_label = regime_label(original.get("report"))
        gpt_label = regime_label(result.get("report")) if result["status"] == "completed" else "unscored"
        rows.append(
            {
                "case_id": case["id"],
                "asset": case["asset"],
                "baseline_model": original["model"],
                "time_ist": datetime.fromtimestamp(as_of, IST).isoformat(),
                "gpt_status": result["status"],
                "baseline_regime": baseline_label,
                "gpt_regime": gpt_label,
                "baseline_selection": selection(original["tools"], original["tools"]),
                "gpt_selection": selection(result.get("tools", []), original["tools"]),
                "baseline_seconds": original["metrics"].get("duration"),
                "gpt_seconds": result.get("duration_seconds"),
                "outcomes": truth,
                "gpt_exact_calls": sum(t["exact_match"] for t in result.get("tools", [])),
                "gpt_unavailable_calls": sum(not t["exact_match"] for t in result.get("tools", [])),
                "gpt_cost_usd": sum(u.get("cost", 0) or 0 for u in result.get("usage", [])),
            }
        )
    return rows


def proxy_correct(label: str, outcome: dict) -> bool | None:
    if label == "unscored" or outcome["status"] != "complete":
        return None
    return label == outcome["direction"] and (label != "sideways" or outcome["range_held"])


def write_report(document: dict, rows: list[dict], output: Path) -> None:
    groups = defaultdict(list)
    for row in rows:
        groups[row["baseline_model"]].append(row)
    window = document["window"]
    start = datetime.fromisoformat(window["start"]).astimezone(IST).isoformat()
    end = datetime.fromisoformat(window["end"]).astimezone(IST).isoformat()
    lines = [
        "# GPT-6.1 Sol historical market replay",
        "",
        f"Window: {start} through {end}.",
        "",
        f"{len(rows)} decision sessions. GPT setting: medium. Exact original prompts and stored PNG charts.",
        "",
        "## What this test measures",
        "",
        "Recorded tool results are returned only for identical names and arguments. New arguments return an "
        "unavailable result. Scheduling never reaches a trading service. Intended selections below are attempts, "
        "not executed trades. This strict replay constrains GPT when it chooses a different strategy, exit or time.",
        "",
        "Tool schemas were reconstructed from Git because sessions do not store their original schemas. "
        "Where recorded calls proved deployment preceded Git, matching current definitions supplied the schema. "
        "This is a disclosed approximation, not a claim of identical original tool definitions.",
        "",
        "Regime proxy scoring uses only the leading Market regime statement. Missing or mixed statements remain "
        "unscored. This is a future-price check of a present-regime claim, not a calibrated forecast accuracy metric.",
        "",
        "Actual market data: Delta Exchange BTCUSD and ETHUSD one-minute OHLC. Entry is the next minute open after "
        "snapshot capture. Fixed horizons are 1, 3 and 6 hours. BTC neutral threshold is 0.25%; ETH is 0.50%. "
        "A sideways proxy also requires every high/low excursion to stay within twice that threshold. "
        "Only full horizons completed by the export cutoff count. Missing candles are excluded.",
        "",
        "These thresholds were set before fetching outcome candles. They are arbitrary evaluation definitions; "
        "different thresholds can change rankings. Original analyses used Binance Spot USDT; outcome prices use "
        "Delta USD perpetuals, so venue basis can affect small moves.",
        "",
        "Options returns, fills, fees, slippage, time decay and implied volatility are not inferred from underlying "
        "candles. No option-profit winner can be established by this test. Only stored decision sessions are "
        "replayed; reports without corresponding saved sessions are excluded. Historical model latency includes "
        "the original agent and tools; GPT latency includes API and replay overhead, so timing is not controlled.",
        "",
        "## Paired regime checks",
        "",
        "Each row compares GPT with one baseline on the same timestamps. DeepSeek and MiMo were used on "
        "different periods; their totals are not a direct head-to-head comparison.",
        "",
        "| Baseline | Horizon | Paired scored cases | Baseline matches | GPT matches |",
        "|---|---:|---:|---:|---:|",
    ]
    for model, subset in groups.items():
        for h in HORIZONS:
            pairs = [
                (
                    proxy_correct(row["baseline_regime"], row["outcomes"][str(h)]),
                    proxy_correct(row["gpt_regime"], row["outcomes"][str(h)]),
                )
                for row in subset
            ]
            pairs = [(a, b) for a, b in pairs if a is not None and b is not None]
            lines.append(f"| {model} | {h}h | {len(pairs)} | {sum(a for a, _ in pairs)} | {sum(b for _, b in pairs)} |")
    completed = [r for r in rows if r["gpt_status"] == "completed"]
    lines.extend(
        [
            "",
            f"GPT completed {len(completed)}/{len(rows)} cases. Recorded tool matches: "
            f"{sum(r['gpt_exact_calls'] for r in rows)}. Unavailable calls: "
            f"{sum(r['gpt_unavailable_calls'] for r in rows)}. "
            f"Reported GPT cost: ${sum(r['gpt_cost_usd'] for r in rows):.4f}.",
            "",
        ]
    )
    if completed:
        lines.append(
            f"Mean GPT duration: {mean(r['gpt_seconds'] for r in completed):.1f}s. "
            f"Mean original duration: {mean(r['baseline_seconds'] for r in completed if r['baseline_seconds']):.1f}s."
        )
    lines.extend(
        [
            "",
            "## Decisions and actual moves",
            "",
            "| Time IST | Asset | Baseline | Original strategy | GPT intended strategy | "
            "Regime original / GPT | 1h return | 3h return | 6h return |",
            "|---|---|---|---|---|---|---:|---:|---:|",
        ]
    )
    for row in rows:
        moves = [
            f"{row['outcomes'][str(h)]['return_percent']:+.3f}%"
            if row["outcomes"][str(h)]["status"] == "complete"
            else row["outcomes"][str(h)]["status"]
            for h in HORIZONS
        ]
        model = "MiMo" if "mimo" in row["baseline_model"] else "DeepSeek"
        lines.append(
            f"| {row['time_ist']} | {row['asset']} | {model} | "
            f"{row['baseline_selection']['strategy']} | {row['gpt_selection']['strategy']} | "
            f"{row['baseline_regime']} / {row['gpt_regime']} | {' | '.join(moves)} |"
        )
    lines.extend(["", "## Per-case evidence", ""])
    for row in rows:
        case = next(c for c in document["cases"] if c["id"] == row["case_id"])
        lines.extend(
            [
                f"### {row['time_ist']} {row['asset']} {row['case_id']}",
                "",
                f"Baseline model: {row['baseline_model']}. GPT status: {row['gpt_status']}.",
                "",
                f"Original intended entry: {row['baseline_selection']['activation_time']}. "
                f"GPT intended entry: {row['gpt_selection']['activation_time']}.",
                "",
                "Original written analysis:",
                "",
                case["original"].get("report") or "No report.",
                "",
                "GPT written analysis:",
                "",
                row.get("gpt_report") or "No completed report.",
                "",
            ]
        )
    output.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cases", type=Path)
    parser.add_argument("results", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    document = json.loads(args.cases.read_text(encoding="utf-8"))
    args.output.mkdir(parents=True, exist_ok=True)
    market_path = args.output / "actual-market.json"
    if market_path.exists():
        market = json.loads(market_path.read_text())
    else:
        start = int(datetime.fromisoformat(document["window"]["start"]).timestamp())
        end = int(datetime.fromisoformat(document["window"]["end"]).timestamp())
        market = {asset: fetch_candles(asset + "USD", start, end) for asset in THRESHOLDS}
        market_path.write_text(json.dumps(market), encoding="utf-8")
    results = {path.stem: json.loads(path.read_text()) for path in args.results.glob("*.json")}
    rows = compare(document, results, market)
    for row in rows:
        row["gpt_report"] = results.get(row["case_id"], {}).get("report")
    (args.output / "comparison.json").write_text(json.dumps(rows), encoding="utf-8")
    with (args.output / "comparison.csv").open("w", newline="", encoding="utf-8") as file:
        columns = [
            "case_id",
            "asset",
            "baseline_model",
            "time_ist",
            "gpt_status",
            "baseline_regime",
            "gpt_regime",
            "baseline_seconds",
            "gpt_seconds",
            "gpt_exact_calls",
            "gpt_unavailable_calls",
            "gpt_cost_usd",
        ]
        writer = csv.DictWriter(file, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    write_report(document, rows, args.output / "report.md")
    print(json.dumps({"cases": len(rows), "candles": {k: len(v) for k, v in market.items()}}))


if __name__ == "__main__":
    main()
