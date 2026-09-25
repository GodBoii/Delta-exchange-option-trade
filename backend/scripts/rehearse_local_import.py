"""Import a saved Convex snapshot into a disposable Ubuntu staging database."""

import argparse
import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from scripts.import_convex_export import run
from scripts.init_local_db import apply_migrations


def staging_url(writer_url: str, database_name: str) -> str:
    if not database_name.startswith("trade_cognition_stage"):
        raise ValueError("Rehearsal destination must be a trade_cognition_stage database")
    parts = urlsplit(writer_url)
    if parts.path != "/trade_cognition":
        raise ValueError("LOCAL_DATABASE_URL must point to the untouched production database")
    return urlunsplit((parts.scheme, parts.netloc, f"/{database_name}", parts.query, parts.fragment))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("--database", default="trade_cognition_stage")
    args = parser.parse_args()
    writer_url = os.getenv("LOCAL_DATABASE_URL")
    if not writer_url:
        parser.error("LOCAL_DATABASE_URL is required")
    target = staging_url(writer_url, args.database)
    apply_migrations(target)
    counts = run(args.snapshot, target, apply=True, source_paused=True)
    print({"stagingVerified": True, "records": sum(counts.values()), "tables": len(counts)})


if __name__ == "__main__":
    main()
