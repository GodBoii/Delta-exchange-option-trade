"""Unlock a restored writer only after manifest and Delta reconciliation."""

import argparse
import os

import psycopg


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest-reviewed", action="store_true")
    parser.add_argument("--delta-reconciled", action="store_true")
    args = parser.parse_args()
    if not args.manifest_reviewed or not args.delta_reconciled:
        parser.error("Both manifest review and Delta reconciliation are required")
    url = os.getenv("LOCAL_DATABASE_URL")
    if not url:
        parser.error("LOCAL_DATABASE_URL is required")
    with psycopg.connect(url) as connection:
        connection.execute("update trade.recovery_gate set pending=false,reason=null,updated_at=now() where key='main'")
    print("Recovery gate cleared. Restart the trading writer to resume its schedulers.")


if __name__ == "__main__":
    main()
