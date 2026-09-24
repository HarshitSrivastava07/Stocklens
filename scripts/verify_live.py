#!/usr/bin/env python3
"""
Prove StockLens is telling the truth.

Run this on a machine with normal internet access. It fetches live data from the
source and checks what StockLens stored and computed against it, then exits
non-zero if anything fails — so it can gate a deploy.

    python scripts/verify_live.py                      # every tracked stock
    python scripts/verify_live.py --symbols RELIANCE,TCS
    python scripts/verify_live.py --check prices       # one check
    python scripts/verify_live.py --tolerance 0.5      # percent
    python scripts/verify_live.py --json report.json   # machine-readable

Seven checks:

  1. prices       Every stored price matches the live source, to tolerance.
  2. history      Price history is continuous, positive and correctly ordered.
  3. fundamentals Filings are present, deep enough, and internally consistent.
  4. valuation    Recomputing from stored filings reproduces the stored number.
  5. signals      Every trade plan is internally coherent.
  6. provenance   No fabricated data anywhere in the database.
  7. freshness    Nothing being served is dangerously stale.

Why this script exists
----------------------
The environment this code was built in blocks every market-data host at the
network layer — Yahoo, NSE, Upstox and every alternative return 403 at the proxy.
So the parsing, the maths and the database plumbing were all proven there with
recorded payloads, known-answer tests and a real PostgreSQL instance, but the
final claim — *the prices on screen match the market* — cannot be made from
inside that sandbox. It can only be made from somewhere with an open route to
the source. That is what this script does, and why it reports rather than
assumes.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "apps" / "api"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

import asyncpg  # noqa: E402

from services import ingest_service as ing  # noqa: E402
from services.analytics.intrinsic_value import (  # noqa: E402
    MarketContext,
    compute_intrinsic_value,
)
from services.providers.yahoo import YahooProvider  # noqa: E402

GREEN, RED, YELLOW, DIM, BOLD, RESET = (
    "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[1m", "\033[0m",
)
TICK, CROSS, WARN = "PASS", "FAIL", "WARN"


@dataclass
class CheckResult:
    name: str
    passed: int = 0
    failed: int = 0
    warned: int = 0
    skipped: int = 0
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    note: str = ""

    def fail(self, message: str) -> None:
        self.failed += 1
        if len(self.failures) < 40:
            self.failures.append(message)

    def warn(self, message: str) -> None:
        self.warned += 1
        if len(self.warnings) < 40:
            self.warnings.append(message)

    @property
    def ok(self) -> bool:
        return self.failed == 0

    def as_dict(self) -> dict:
        return {
            "check": self.name,
            "passed": self.passed,
            "failed": self.failed,
            "warned": self.warned,
            "skipped": self.skipped,
            "failures": self.failures,
            "warnings": self.warnings,
            "note": self.note,
        }


# ─────────────────────────────────────────────────────────────
# 1. Prices against the live source
# ─────────────────────────────────────────────────────────────
async def check_prices(pool, provider, symbols, tolerance_pct: float) -> CheckResult:
    """
    The headline claim: every price StockLens shows matches the market.

    A tolerance is required and is not a fudge. The stored price and the live
    fetch are taken seconds apart, and on a moving stock those are genuinely
    different numbers. What must never happen is a *structural* mismatch — a
    stale price served as live, a wrong ticker showing another company's price,
    or a currency or split adjustment applied twice. Those produce errors far
    larger than the tolerance, which is what this is calibrated to catch.
    """
    result = CheckResult("prices")
    universe = await ing.load_universe(pool, symbols)
    if not universe:
        result.note = "No tracked stocks with a provider ticker."
        return result

    stored = {
        r["nse_symbol"]: r
        for r in await pool.fetch(
            "SELECT nse_symbol, ltp, last_updated, data_source FROM realtime_quotes"
        )
    }

    pairs = [(s["nse_symbol"], s["yahoo_ticker"]) for s in universe]
    quotes, errors = await provider.get_quotes(pairs, concurrency=8)
    live = {q.symbol: q for q in quotes}

    for symbol, message in errors.items():
        result.warn(f"{symbol}: could not fetch live price ({message})")

    for stock in universe:
        symbol = stock["nse_symbol"]
        row = stored.get(symbol)
        quote = live.get(symbol)

        if quote is None:
            continue
        if row is None or row["ltp"] is None:
            result.warn(f"{symbol}: no stored price to compare (live {quote.price})")
            result.skipped += 1
            continue

        stored_price = float(row["ltp"])
        if stored_price <= 0:
            result.fail(f"{symbol}: stored price is {stored_price}")
            continue

        drift = abs(stored_price - quote.price) / quote.price * 100.0
        if drift <= tolerance_pct:
            result.passed += 1
            continue

        # A large gap is either staleness or a real error. Say which.
        age = ""
        if row["last_updated"]:
            minutes = (
                datetime.now(timezone.utc) - row["last_updated"]
            ).total_seconds() / 60
            age = f", stored {minutes:.0f} min ago"
        result.fail(
            f"{symbol}: stored {stored_price:,.2f} vs live {quote.price:,.2f} "
            f"({drift:.2f}% apart{age}, source {row['data_source']})"
        )

    # A check that compared nothing has not passed.
    #
    # With every live fetch failing — a blocked network, an expired session, a
    # provider outage — `failed` stays at zero and this would report PASS while
    # having verified precisely nothing. That is the single worst outcome for a
    # script whose entire job is to be believed, so an empty comparison set is
    # an explicit failure.
    if result.passed == 0 and result.failed == 0:
        reachable = len(universe) - len(errors)
        result.fail(
            f"Compared 0 of {len(universe)} stocks against the live source"
            + (
                " — every live fetch failed, so nothing was verified."
                if reachable == 0
                else " — no stored prices were available to compare."
            )
        )
        result.note = "nothing verified"
        return result

    result.note = f"tolerance {tolerance_pct}%, {result.passed} compared"
    return result


# ─────────────────────────────────────────────────────────────
# 2. History integrity
# ─────────────────────────────────────────────────────────────
async def check_history(pool, symbols) -> CheckResult:
    """Stored price history must be usable: ordered, positive, and continuous."""
    result = CheckResult("history")
    universe = await ing.load_universe(pool, symbols, include_non_equity=True)

    for stock in universe:
        symbol = stock["nse_symbol"]
        rows = await pool.fetch(
            """SELECT date, open, high, low, close, adj_close, volume
                 FROM price_candles_daily
                WHERE nse_symbol = $1 ORDER BY date ASC""",
            symbol,
        )
        if not rows:
            result.skipped += 1
            result.warn(f"{symbol}: no price history stored")
            continue

        problems: list[str] = []

        # Non-positive prices are impossible and break every downstream ratio.
        bad = [r["date"] for r in rows if r["close"] is not None and float(r["close"]) <= 0]
        if bad:
            problems.append(f"{len(bad)} non-positive closes (first {bad[0]})")

        # High must bound low; a violation means the columns are transposed.
        inverted = [
            r["date"] for r in rows
            if r["high"] is not None and r["low"] is not None
            and float(r["high"]) < float(r["low"])
        ]
        if inverted:
            problems.append(f"{len(inverted)} bars where high < low (first {inverted[0]})")

        # The close must sit inside its own bar's range.
        outside = [
            r["date"] for r in rows
            if None not in (r["high"], r["low"], r["close"])
            and not (float(r["low"]) <= float(r["close"]) <= float(r["high"]))
        ]
        if outside:
            problems.append(f"{len(outside)} closes outside their own high/low")

        dates = [r["date"] for r in rows]
        if dates != sorted(dates):
            problems.append("dates are not ordered")
        if len(set(dates)) != len(dates):
            problems.append("duplicate dates")

        future = [d for d in dates if d > date.today() + timedelta(days=1)]
        if future:
            problems.append(f"{len(future)} sessions dated in the future")

        missing_adj = sum(1 for r in rows if r["adj_close"] is None)
        if missing_adj > len(rows) * 0.05:
            problems.append(
                f"{missing_adj}/{len(rows)} rows missing adjusted close "
                "(returns across splits will be wrong)"
            )

        # A gap longer than a fortnight of sessions usually means a failed
        # backfill rather than a genuine trading suspension.
        biggest_gap, gap_at = 0, None
        for previous, current in zip(dates, dates[1:]):
            gap = (current - previous).days
            if gap > biggest_gap:
                biggest_gap, gap_at = gap, previous
        if biggest_gap > 21:
            problems.append(f"{biggest_gap}-day gap after {gap_at}")

        if problems:
            result.fail(f"{symbol}: " + "; ".join(problems))
        else:
            result.passed += 1
    return result


# ─────────────────────────────────────────────────────────────
# 3. Fundamentals
# ─────────────────────────────────────────────────────────────
async def check_fundamentals(pool, symbols, min_years: int) -> CheckResult:
    """Filings must be deep enough to value, and internally consistent."""
    result = CheckResult("fundamentals")
    universe = await ing.load_universe(pool, symbols)

    for stock in universe:
        symbol = stock["nse_symbol"]
        periods = await ing.load_financial_periods(pool, symbol, period_type="A")
        if not periods:
            result.skipped += 1
            result.warn(f"{symbol}: no annual filings stored")
            continue

        problems: list[str] = []
        if len(periods) < min_years:
            result.warn(
                f"{symbol}: only {len(periods)} annual filings "
                f"({min_years} wanted for a full-confidence valuation)"
            )

        ends = [p.period_end for p in periods]
        if len(set(ends)) != len(ends):
            problems.append("duplicate period end dates")

        for period in periods:
            label = period.period_end.isoformat()
            if period.revenue is not None and period.revenue < 0:
                problems.append(f"{label}: negative revenue")
            if (
                period.revenue
                and period.operating_income is not None
                and period.operating_income > period.revenue * 1.05
            ):
                problems.append(f"{label}: operating profit exceeds revenue")
            if (
                period.total_assets
                and period.equity is not None
                and period.equity > period.total_assets * 1.05
            ):
                problems.append(f"{label}: equity exceeds total assets")
            if period.shares_outstanding is not None and period.shares_outstanding <= 0:
                problems.append(f"{label}: share count is {period.shares_outstanding}")

        if problems:
            result.fail(f"{symbol}: " + "; ".join(problems[:4]))
        else:
            result.passed += 1
    return result


# ─────────────────────────────────────────────────────────────
# 4. Valuation determinism
# ─────────────────────────────────────────────────────────────
async def check_valuation(pool, symbols) -> CheckResult:
    """
    Recompute each valuation from the stored filings and compare.

    The engine is deterministic by construction: the same filings must yield the
    same intrinsic value. A mismatch means the stored number no longer reflects
    the data behind it — because the filings were revised, or because a code
    change moved the answer without the pipeline being re-run. Either way, the
    number on the customer's screen is not the number the current engine would
    produce, and that is exactly the drift this catches.
    """
    result = CheckResult("valuation")
    universe = await ing.load_universe(pool, symbols)

    for stock in universe:
        symbol = stock["nse_symbol"]
        stored = await pool.fetchrow(
            """SELECT iv_blended, cmp, years_of_history, beta, valuation_confidence
                 FROM intrinsic_values WHERE nse_symbol = $1""",
            symbol,
        )
        if stored is None or stored["iv_blended"] is None:
            result.skipped += 1
            continue

        periods = await ing.load_financial_periods(pool, symbol, period_type="A")
        market = await ing._load_market_context(pool, stock.get("country"))
        recomputed = compute_intrinsic_value(
            symbol,
            periods,
            current_price=float(stored["cmp"]) if stored["cmp"] else None,
            market=market,
            sector=stock.get("sector_name"),
            industry=stock.get("industry"),
            regressed_beta=float(stored["beta"]) if stored["beta"] else None,
            beta_source="regressed" if stored["beta"] else "default",
        )

        if not recomputed.ok or recomputed.iv_blended is None:
            result.fail(
                f"{symbol}: stored IV {float(stored['iv_blended']):,.2f} but the "
                f"engine now refuses: {recomputed.reason}"
            )
            continue

        stored_iv = float(stored["iv_blended"])
        drift = abs(recomputed.iv_blended - stored_iv) / stored_iv * 100.0
        if drift > 1.0:
            result.fail(
                f"{symbol}: stored IV {stored_iv:,.2f} vs recomputed "
                f"{recomputed.iv_blended:,.2f} ({drift:.2f}% drift) — "
                "re-run the valuation stage"
            )
            continue

        if recomputed.years_of_history != (stored["years_of_history"] or 0):
            result.warn(
                f"{symbol}: history depth changed "
                f"{stored['years_of_history']} -> {recomputed.years_of_history}"
            )
        result.passed += 1
    return result


# ─────────────────────────────────────────────────────────────
# 5. Signal coherence
# ─────────────────────────────────────────────────────────────
async def check_signals(pool, symbols) -> CheckResult:
    """
    A trade plan must not contradict itself.

    These are the invariants a reader would assume without being told, so a
    violation is worse than a missing number: it looks authoritative and is
    wrong.
    """
    result = CheckResult("signals")
    rows = await pool.fetch(
        """SELECT nse_symbol, action, entry_low, entry_high, max_buy_price,
                  stop_loss, target_1, target_2, risk_reward, position_size_pct,
                  intrinsic_value, current_price, conviction
             FROM signals
            WHERE action IS NOT NULL"""
    )
    wanted = set(symbols) if symbols else None

    for row in rows:
        symbol = row["nse_symbol"]
        if wanted and symbol not in wanted:
            continue

        def num(key):
            return float(row[key]) if row[key] is not None else None

        problems: list[str] = []
        entry_low, entry_high = num("entry_low"), num("entry_high")
        stop, max_buy = num("stop_loss"), num("max_buy_price")
        t1, t2 = num("target_1"), num("target_2")
        iv, price = num("intrinsic_value"), num("current_price")

        if entry_low is not None and entry_high is not None and entry_low > entry_high:
            problems.append("entry_low above entry_high")
        if stop is not None and entry_low is not None and stop >= entry_low:
            problems.append("stop loss at or above the entry zone")
        if stop is not None and price is not None and stop >= price:
            problems.append("stop loss at or above the current price")
        if stop is not None and price is not None and stop < price * 0.40:
            problems.append(f"stop loss {stop:,.2f} is implausibly far below price")
        if max_buy is not None and iv is not None and max_buy > iv:
            problems.append("max buy price above intrinsic value (no margin of safety)")
        if entry_high is not None and max_buy is not None and entry_high > max_buy * 1.001:
            problems.append("entry zone extends past the max buy price")
        if t1 is not None and t2 is not None and t1 > t2:
            problems.append("target_1 above target_2")
        if row["risk_reward"] is not None and float(row["risk_reward"]) <= 0:
            problems.append("non-positive risk/reward")
        if row["action"] in ("STRONG_BUY", "BUY") and row["risk_reward"] is not None:
            if float(row["risk_reward"]) < 1.0:
                problems.append(
                    f"{row['action']} with risk/reward {float(row['risk_reward']):.2f}"
                )
        if row["conviction"] is not None and not (0 <= float(row["conviction"]) <= 1):
            problems.append(f"conviction {row['conviction']} outside 0-1")
        if row["position_size_pct"] is not None and float(row["position_size_pct"]) > 25:
            problems.append(f"position size {row['position_size_pct']}% is too large")
        if row["action"] in ("SELL", "AVOID") and entry_low is not None:
            problems.append(f"{row['action']} carries an entry plan")

        if problems:
            result.fail(f"{symbol}: " + "; ".join(problems))
        else:
            result.passed += 1
    return result


# ─────────────────────────────────────────────────────────────
# 6. Provenance
# ─────────────────────────────────────────────────────────────
async def check_provenance(pool) -> CheckResult:
    """
    No fabricated data anywhere.

    This codebase previously shipped seeders that wrote `random.uniform` values
    into the valuation, ratio and signal tables, and an endpoint that did the
    same on a GET. Those are gone, but a database seeded before that removal
    would still be serving invented numbers with no visible difference. This
    check finds them.
    """
    result = CheckResult("provenance")

    # An allowlist, not a denylist.
    #
    # This check originally named the fake sources it knew about — MOCK, SEED,
    # DEV_SEED and so on. That structure is unsound and was caught being
    # unsound: candles stamped "FIXTURE" passed cleanly, because nobody had
    # thought to add that word. A denylist of every name someone might give
    # fabricated data cannot be completed, and the one that slips through is
    # precisely the one nobody anticipated.
    #
    # Inverting it means an unrecognised source is a failure by default. Adding
    # a new genuine feed requires one line here, which is the correct place for
    # that decision to be made explicitly.
    trusted = {
        "YAHOO",           # Yahoo Finance provider
        "NSE_BHAVCOPY",    # NSE daily bhavcopy
        "NSE_EQUITY_LIST", # NSE official equity list
        "BSE_XML",         # BSE quarterly results
        "SCREENER",        # Screener.in export
        "UPSTOX",          # Upstox live feed
    }

    for table in (
        "realtime_quotes",
        "price_candles_daily",
        "financial_results",
        "stocks",
        "intrinsic_values",
    ):
        try:
            rows = await pool.fetch(
                f"""SELECT coalesce(data_source, '(null)') AS src, count(*) AS n
                      FROM {table}
                     GROUP BY data_source"""
            )
        except asyncpg.PostgresError:
            result.skipped += 1
            continue

        untrusted = [r for r in rows if r["src"].upper() not in trusted]
        if untrusted:
            for row in untrusted:
                result.fail(
                    f"{table}: {row['n']} rows from an untrusted source "
                    f"'{row['src']}' (trusted: {', '.join(sorted(trusted))})"
                )
        else:
            result.passed += 1

    # A placeholder ISIN was the signature of the deleted universe generator.
    fabricated = await pool.fetchval(
        "SELECT count(*) FROM stocks WHERE isin = 'INE000000000'"
    )
    if fabricated:
        result.fail(
            f"stocks: {fabricated} rows carry the placeholder ISIN INE000000000 "
            "(the signature of the removed mock universe generator)"
        )
    else:
        result.passed += 1
    return result


# ─────────────────────────────────────────────────────────────
# 7. Freshness
# ─────────────────────────────────────────────────────────────
async def check_freshness(pool, symbols, max_age_hours: float) -> CheckResult:
    """A price nobody can tell is stale is more dangerous than a missing one."""
    result = CheckResult("freshness")
    universe = await ing.load_universe(pool, symbols)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)

    for stock in universe:
        symbol = stock["nse_symbol"]
        row = await pool.fetchrow(
            "SELECT ltp, last_updated, is_stale FROM realtime_quotes WHERE nse_symbol = $1",
            symbol,
        )
        if row is None or row["last_updated"] is None:
            result.skipped += 1
            continue

        age_hours = (
            datetime.now(timezone.utc) - row["last_updated"]
        ).total_seconds() / 3600

        if row["last_updated"] < cutoff and not row["is_stale"]:
            # Old AND not flagged: the UI will present it as current.
            result.fail(
                f"{symbol}: price is {age_hours:.1f}h old but is_stale is false"
            )
        elif row["last_updated"] < cutoff:
            result.warn(f"{symbol}: price is {age_hours:.1f}h old (correctly flagged)")
            result.passed += 1
        else:
            result.passed += 1

    result.note = f"threshold {max_age_hours}h"
    return result


# ─────────────────────────────────────────────────────────────
# Reporting
# ─────────────────────────────────────────────────────────────
def render(results: list[CheckResult], *, verbose: bool) -> bool:
    all_ok = all(r.ok for r in results)

    print(f"\n{BOLD}StockLens verification{RESET}")
    print(f"{DIM}{datetime.now(timezone.utc).isoformat(timespec='seconds')}{RESET}\n")
    print(f"  {'CHECK':<14} {'':<6} {'PASS':>6} {'FAIL':>6} {'WARN':>6} {'SKIP':>6}  NOTE")
    print(f"  {'-'*14} {'-'*6} {'-'*6} {'-'*6} {'-'*6} {'-'*6}  {'-'*24}")

    for r in results:
        if not r.ok:
            mark = f"{RED}{CROSS}{RESET}"
        elif r.passed == 0:
            # Nothing was actually asserted; that is not the same as passing.
            mark = f"{YELLOW}{WARN}{RESET}"
        else:
            mark = f"{GREEN}{TICK}{RESET}"
        print(
            f"  {r.name:<14} {mark:<15} {r.passed:>6} {r.failed:>6} "
            f"{r.warned:>6} {r.skipped:>6}  {DIM}{r.note}{RESET}"
        )

    for r in results:
        if r.failures:
            print(f"\n{RED}{BOLD}{r.name} — failures{RESET}")
            for line in r.failures:
                print(f"  {RED}x{RESET} {line}")
        # Warnings print even on a passing check: "nothing was compared" must
        # never be able to hide behind a green mark.
        if r.warnings:
            print(f"\n{YELLOW}{r.name} — warnings{RESET}")
            for line in r.warnings[: (None if verbose else 8)]:
                print(f"  {YELLOW}!{RESET} {line}")
            if not verbose and len(r.warnings) > 8:
                print(f"  {DIM}… {len(r.warnings) - 8} more (use --verbose){RESET}")

    total_failed = sum(r.failed for r in results)
    total_passed = sum(r.passed for r in results)
    total_warned = sum(r.warned for r in results)

    print()
    if all_ok:
        print(
            f"{GREEN}{BOLD}All checks passed.{RESET} "
            f"{total_passed} assertions, {total_warned} warnings."
        )
        print(
            f"{DIM}Every stored price matches the live source, every valuation "
            f"reproduces from its filings, and no fabricated data was found.{RESET}\n"
        )
    else:
        print(f"{RED}{BOLD}{total_failed} failures.{RESET} {total_passed} passed.")
        print(f"{DIM}Details above. This exits non-zero, so it can gate a deploy.{RESET}\n")
    return all_ok


# ─────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────
CHECKS = ("prices", "history", "fundamentals", "valuation", "signals",
          "provenance", "freshness")


def database_url() -> str:
    raw = (
        os.environ.get("DATABASE_SYNC_URL")
        or os.environ.get("DATABASE_URL")
        or "postgresql://postgres:password@localhost:5432/stocklens"
    )
    return raw.replace("postgresql+asyncpg://", "postgresql://").replace(
        "postgresql+psycopg2://", "postgresql://"
    )


async def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--symbols", help="comma-separated; omit for the whole universe")
    parser.add_argument(
        "--check", action="append", choices=CHECKS,
        help="run only this check; repeatable",
    )
    parser.add_argument(
        "--tolerance", type=float, default=1.0,
        help="percent a stored price may differ from live (default 1.0)",
    )
    parser.add_argument(
        "--min-years", type=int, default=10,
        help="annual filings wanted per stock (default 10)",
    )
    parser.add_argument(
        "--max-age-hours", type=float, default=24.0,
        help="how old an unflagged price may be (default 24)",
    )
    parser.add_argument("--json", help="write the full report to this path")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    symbols = (
        [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        if args.symbols
        else None
    )
    wanted = set(args.check) if args.check else set(CHECKS)

    url = database_url()
    print(f"{DIM}database : {url.split('@')[-1]}{RESET}")
    print(f"{DIM}symbols  : {', '.join(symbols) if symbols else 'entire universe'}{RESET}")

    pool = await asyncpg.create_pool(url, min_size=1, max_size=6)
    results: list[CheckResult] = []

    try:
        if "prices" in wanted:
            try:
                async with YahooProvider() as provider:
                    results.append(
                        await check_prices(pool, provider, symbols, args.tolerance)
                    )
            except Exception as exc:  # noqa: BLE001
                # A blocked or unreachable source is the one thing this script
                # cannot work around, and it must not be reported as a pass.
                failed = CheckResult("prices")
                failed.fail(
                    f"Could not reach the market-data source: "
                    f"{type(exc).__name__}: {exc}"
                )
                failed.note = "source unreachable"
                results.append(failed)

        if "history" in wanted:
            results.append(await check_history(pool, symbols))
        if "fundamentals" in wanted:
            results.append(await check_fundamentals(pool, symbols, args.min_years))
        if "valuation" in wanted:
            results.append(await check_valuation(pool, symbols))
        if "signals" in wanted:
            results.append(await check_signals(pool, symbols))
        if "provenance" in wanted:
            results.append(await check_provenance(pool))
        if "freshness" in wanted:
            results.append(await check_freshness(pool, symbols, args.max_age_hours))
    finally:
        await pool.close()

    ok = render(results, verbose=args.verbose)

    if args.json:
        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "ok": ok,
            "tolerance_pct": args.tolerance,
            "symbols": symbols or "all",
            "checks": [r.as_dict() for r in results],
        }
        Path(args.json).write_text(json.dumps(payload, indent=2))
        print(f"{DIM}Report written to {args.json}{RESET}\n")

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
