"""Durable ten-minute observations, using completed Binance minute candles."""

import math
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from .analysis import annualized_historical_volatility, sideways_probability, vwap

STEP_MS = 600_000


def observation(candles: list[dict[str, Any]], end: int) -> dict[str, Any] | None:
    sample = [c for c in candles if end - 240 * 60_000 <= c["openTime"] < end]
    if len(sample) != 240 or any(
        c["openTime"] != end - (240 - i) * 60_000
        or c.get("closed") is False
        or any(
            not math.isfinite(float(c.get(k, -1))) or float(c.get(k, -1)) < 0
            for k in ("close", "high", "low", "baseVolume", "quoteVolume")
        )
        or c["close"] <= 0
        for i, c in enumerate(sample)
    ):
        return None
    closes = [float(c["close"]) for c in sample]
    return {
        "end": end,
        "sidewaysScore": sideways_probability(closes, vwap(sample)),
        "volatilityAnnualizedPercent": annualized_historical_volatility(closes),
        "volumeBtc": sum(c["baseVolume"] for c in sample[-10:]),
        "volumeUsdt": sum(c["quoteVolume"] for c in sample[-10:]),
    }


class MarketHistory:
    def __init__(self, path: str, symbol: str) -> None:
        self.path = Path(path)
        self.symbol = symbol

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS observations (
                symbol TEXT NOT NULL, end INTEGER NOT NULL, sidewaysScore REAL NOT NULL,
                volatilityAnnualizedPercent REAL NOT NULL, volumeBtc REAL NOT NULL,
                volumeUsdt REAL NOT NULL, PRIMARY KEY(symbol, end))""")

    def save(self, row: dict[str, Any]) -> None:
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute(
                "INSERT OR IGNORE INTO observations VALUES (?, ?, ?, ?, ?, ?)",
                (
                    self.symbol,
                    row["end"],
                    row["sidewaysScore"],
                    row["volatilityAnnualizedPercent"],
                    row["volumeBtc"],
                    row["volumeUsdt"],
                ),
            )

    def read(self, now: int) -> list[dict[str, Any]]:
        with closing(sqlite3.connect(self.path)) as db:
            db.row_factory = sqlite3.Row
            return [
                dict(row)
                for row in db.execute(
                    "SELECT end, sidewaysScore, volatilityAnnualizedPercent, volumeBtc, volumeUsdt "
                    "FROM observations WHERE symbol = ? AND end > ? AND end <= ? ORDER BY end",
                    (self.symbol, now - 50 * 3_600_000, now),
                )
            ]
