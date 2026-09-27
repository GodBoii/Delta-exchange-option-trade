"""Copy every recorded software run into the owner ledger and verify the counts.

Idempotent: run IDs are the ledger key, so a rerun updates rows instead of adding them.
Only values the software recorded are copied; nothing is fetched from Delta.

    python -m scripts.backfill_owner_ledger            # import and verify
    python -m scripts.backfill_owner_ledger --verify   # verify only
"""

import argparse
import asyncio
import json
import os
import sys

from psycopg_pool import AsyncConnectionPool

from app.owner_ledger import OwnerLedger


async def run(database_url: str, verify_only: bool) -> dict:
    async with AsyncConnectionPool(database_url, min_size=1, max_size=2, open=False) as pool:
        await pool.open()
        ledger = OwnerLedger(pool)
        report = await ledger.verify() if verify_only else await ledger.backfill()
    report.setdefault("verified", report["missing"] == 0 and report["mismatched"] == 0)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database-url", default=os.getenv("LOCAL_DATABASE_URL"))
    parser.add_argument("--verify", action="store_true", help="check counts without importing")
    args = parser.parse_args()
    if not args.database_url:
        raise SystemExit("LOCAL_DATABASE_URL is required")
    if os.name == "nt":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    report = asyncio.run(run(args.database_url, args.verify))
    print(json.dumps(report, indent=2, default=str))
    if not report["verified"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
