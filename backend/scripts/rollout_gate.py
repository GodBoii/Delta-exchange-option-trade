"""Read-only pre-restart check for the trading writer."""

from __future__ import annotations

import argparse
import asyncio
import json
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from psycopg_pool import AsyncConnectionPool

from app.auth import delta_client_for_user
from app.config import get_settings
from app.database import Database


def open_position_count(response: dict[str, Any]) -> int:
    rows = response.get("result")
    if not isinstance(rows, list):
        raise ValueError("Delta position snapshot is unavailable")
    count = 0
    for item in rows:
        if not isinstance(item, dict) or item.get("size") is None:
            raise ValueError("Delta position size is unavailable")
        try:
            size = Decimal(str(item["size"]))
        except (InvalidOperation, TypeError, ValueError) as error:
            raise ValueError("Delta position size is invalid") from error
        if not size.is_finite():
            raise ValueError("Delta position size is invalid")
        count += size != 0
    return count


async def inspect(user_id: str) -> dict[str, Any]:
    settings = get_settings()
    pool = AsyncConnectionPool(settings.database_url, min_size=1, max_size=2, open=False)
    await pool.open(wait=True)
    db = Database(settings, pool)
    client = None
    try:
        client = await delta_client_for_user(db, settings, user_id)
        strategies, analysis, positions = await asyncio.gather(
            db.select(
                "strategies",
                {
                    "select": "id,status",
                    "status": "in.(scheduled,executing_entry,active,executing_exit,attention)",
                },
            ),
            db.select("automation_agent_runs", {"select": "id", "status": "eq.running"}),
            client.positions(),
        )
        position_count = open_position_count(positions)
        open_orders = 0
        cursor = None
        seen: set[str] = set()
        while True:
            response = await client.open_orders(after=cursor)
            page = response.get("result")
            if not isinstance(page, list):
                raise ValueError("Delta open-order snapshot is unavailable")
            open_orders += len(page)
            next_cursor = str((response.get("meta") or {}).get("after") or "")
            if not next_cursor:
                break
            if next_cursor in seen:
                raise ValueError("Delta repeated the open-order cursor")
            seen.add(next_cursor)
            cursor = next_cursor
        return {
            "safeToRestart": not (strategies or analysis or position_count or open_orders),
            "nonterminalStrategies": len(strategies),
            "runningAnalyses": len(analysis),
            "accountPositions": position_count,
            "accountOpenOrders": open_orders,
        }
    finally:
        if client is not None:
            await client.close()
        await db.close()
        await pool.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-id", required=True)
    args = parser.parse_args()
    result = asyncio.run(inspect(str(UUID(args.user_id))))
    print(json.dumps(result))
    if not result["safeToRestart"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
