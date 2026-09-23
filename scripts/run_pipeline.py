#!/usr/bin/env python3
"""
Fetch real market data and compute everything from it.

    python scripts/run_pipeline.py                          # whole universe
    python scripts/run_pipeline.py --symbols RELIANCE,TCS    # a few names
    python scripts/run_pipeline.py --stage valuation         # one stage
    python scripts/run_pipeline.py --years 10 --quotes-only  # just refresh prices

Stages run in dependency order:

    history + filings + quotes  ->  technicals  ->  valuation  ->  signals

Technicals run before valuation because valuation uses the beta they regress
from real price history. Signals run last because they read both.

Nothing here generates data. A symbol the provider has no data for is recorded
as a failure in ``ingest_runs`` and skipped.
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

import asyncpg  # noqa: E402

from services import ingest_service as ing  # noqa: E402
from services.providers.yahoo import YahooProvider  # noqa: E402

STAGES = ("history", "fundamentals", "quotes", "technicals", "valuation", "signals")


def database_url() -> str:
    raw = (
        os.environ.get("DATABASE_SYNC_URL")
        or os.environ.get("DATABASE_URL")
        or "postgresql://postgres:password@localhost:5432/stocklens"
    )
    return raw.replace("postgresql+asyncpg://", "postgresql://").replace(
        "postgresql+psycopg2://", "postgresql://"
    )


def show(stage: str, report) -> None:
    data = report if isinstance(report, dict) else report.as_dict()
    print(
        f"  {stage:<14} requested={data['requested']:<6} ok={data['succeeded']:<6} "
        f"failed={data['failed']:<5} skipped={data['skipped']:<5} "
        f"rows={data['rows_written']:<8} {data['duration_seconds']:.1f}s"
    )
    for symbol, message in list(data.get("errors", {}).items())[:5]:
        print(f"      {symbol}: {message[:110]}")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", help="comma-separated; omit for the whole universe")
    parser.add_argument("--years", type=int, default=10, help="years of price history")
    parser.add_argument("--stage", choices=STAGES, help="run a single stage")
    parser.add_argument("--quotes-only", action="store_true", help="refresh prices only")
    parser.add_argument("--skip-history", action="store_true")
    parser.add_argument("--skip-fundamentals", action="store_true")
    parser.add_argument("--concurrency", type=int, default=6)
    args = parser.parse_args()

    symbols = (
        [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        if args.symbols
        else None
    )

    url = database_url()
    print(f"Database : {url.split('@')[-1]}")
    print(f"Symbols  : {', '.join(symbols) if symbols else 'entire universe'}")
    print()

    pool = await asyncpg.create_pool(url, min_size=2, max_size=8)
    try:
        universe = await ing.load_universe(pool, symbols)
        if not universe:
            print(
                "No tracked stocks found.\n"
                "Run scripts/seed_universe.py first, and make sure each stock has a "
                "yahoo_ticker set."
            )
            return 1
        print(f"{len(universe)} stocks in scope\n")

        async with YahooProvider(rate_per_sec=5.0, burst=10) as provider:
            if args.quotes_only:
                show("quotes", await ing.refresh_quotes(pool, provider, symbols=symbols))
                return 0

            if args.stage:
                runners = {
                    "history": lambda: ing.backfill_price_history(
                        pool, provider, symbols=symbols, years=args.years,
                        concurrency=args.concurrency,
                    ),
                    "fundamentals": lambda: ing.refresh_fundamentals(
                        pool, provider, symbols=symbols, concurrency=args.concurrency
                    ),
                    "quotes": lambda: ing.refresh_quotes(pool, provider, symbols=symbols),
                    "technicals": lambda: ing.run_technicals(pool, symbols=symbols),
                    "valuation": lambda: ing.run_valuation(pool, symbols=symbols),
                    "signals": lambda: ing.run_signals(pool, symbols=symbols),
                }
                show(args.stage, await runners[args.stage]())
                return 0

            if not args.skip_history:
                show("history", await ing.backfill_price_history(
                    pool, provider, symbols=symbols, years=args.years,
                    concurrency=args.concurrency,
                ))
            if not args.skip_fundamentals:
                show("fundamentals", await ing.refresh_fundamentals(
                    pool, provider, symbols=symbols, concurrency=args.concurrency
                ))
            show("quotes", await ing.refresh_quotes(pool, provider, symbols=symbols))

        show("technicals", await ing.run_technicals(pool, symbols=symbols))
        show("valuation", await ing.run_valuation(pool, symbols=symbols))
        show("signals", await ing.run_signals(pool, symbols=symbols))

        print()
        summary = await pool.fetchrow(
            """SELECT
                 (SELECT count(*) FROM intrinsic_values WHERE iv_blended IS NOT NULL) AS valued,
                 (SELECT count(*) FROM intrinsic_values WHERE years_of_history >= 10) AS ten_year,
                 (SELECT count(*) FROM signals WHERE action IS NOT NULL) AS signalled"""
        )
        print(
            f"Result: {summary['valued']} valued "
            f"({summary['ten_year']} with a full 10-year history), "
            f"{summary['signalled']} signalled."
        )
        return 0
    finally:
        await pool.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
