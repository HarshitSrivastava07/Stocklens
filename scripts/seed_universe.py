#!/usr/bin/env python3
"""
Import the stock universe from the exchange's own published list.

    python scripts/seed_universe.py                # NSE equities
    python scripts/seed_universe.py --dry-run      # fetch and report, write nothing

Replaces the deleted dev seeders. Those invented companies; this one imports the
real list and **fails** if it cannot reach it. An empty stock table is a problem
you can see; a fabricated one is a problem you cannot.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "apps" / "api"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from services.universe_service import (  # noqa: E402
    UniverseImportError,
    fetch_nse_equity_list,
    import_nse_equity_list,
    yahoo_ticker_for,
)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="fetch and summarise the list without writing to the database",
    )
    parser.add_argument("--limit", type=int, default=0, help="import only the first N")
    args = parser.parse_args()

    print("Fetching the NSE equity list...")
    try:
        rows = await fetch_nse_equity_list()
    except UniverseImportError as exc:
        print(f"\nFAILED: {exc}", file=sys.stderr)
        print(
            "\nNothing was written. The stock table is unchanged.\n"
            "This is deliberate: a fabricated universe is worse than an empty one.\n"
            "\nIf NSE is blocking this host, download the file in a browser from\n"
            "  https://www.nseindia.com/market-data/securities-available-for-trading\n"
            "and import it with scripts/import_nse_symbols.py instead.",
            file=sys.stderr,
        )
        return 1

    if args.limit:
        rows = rows[: args.limit]

    print(f"Parsed {len(rows)} equities. First five:\n")
    for row in rows[:5]:
        print(
            f"  {row['symbol']:<14} {row['name'][:44]:<46} "
            f"{row['isin'] or '-':<14} -> {yahoo_ticker_for(row['symbol'])}"
        )

    if args.dry_run:
        print("\nDry run — nothing written.")
        return 0

    print("\nWriting to the database...")
    report = await import_nse_equity_list()
    print(
        f"\nDone. imported={report['succeeded']} failed={report['failed']} "
        f"in {report['duration_seconds']:.1f}s"
    )
    if report["errors"]:
        print("\nFirst errors:")
        for symbol, message in list(report["errors"].items())[:10]:
            print(f"  {symbol}: {message}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
