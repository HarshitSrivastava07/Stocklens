"""
StockLens — Live Price Fetcher (Yahoo Finance v8 with Cookie+Crumb Auth)
=========================================================================
Yahoo Finance now requires a session cookie + crumb token for all API calls.
Without it, every request returns an empty body → parse error.

Auth flow (same as modern yfinance internals):
  1. GET https://yahoo.com → collect session cookies
  2. GET https://query1.finance.yahoo.com/v1/test/getcrumb → get crumb string
  3. Pass crumb as query param + cookies on every chart API call

Run from stocklens root:
    python fetch_live_prices.py

Requires: pip install httpx psycopg2-binary
"""
import asyncio
import logging
import sys
import time
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("live_price_fetcher")

# ── Dependency checks ─────────────────────────────────────────
for pkg, pip_name in [("httpx", "httpx"), ("psycopg2", "psycopg2-binary")]:
    try:
        __import__(pkg)
    except ImportError:
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install", pip_name])

import httpx
import psycopg2
import psycopg2.extras

# ── Config ────────────────────────────────────────────────────
DB_URL      = "postgresql://postgres:password@localhost:5432/stocklens"
CONCURRENCY = 8          # conservative — Yahoo bans on high concurrency
TIMEOUT     = 15         # seconds per request
DELAY_AFTER_BATCH = 1.5  # seconds between batches of CONCURRENCY

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
}

# Price sanity bounds per currency
PRICE_BOUNDS = {
    "INR": (0.5,   250_000),
    "USD": (0.01,  100_000),
    "JPY": (1,   2_000_000),
    "GBP": (0.01,  100_000),
    "EUR": (0.01,  100_000),
    "HKD": (0.01,  500_000),
    "AUD": (0.01,  100_000),
    "SGD": (0.01,  100_000),
}


def validate_price(ltp: float, currency: str, symbol: str) -> bool:
    if not ltp or ltp <= 0:
        log.warning(f"  VALIDATION FAIL [{symbol}]: price={ltp} <= 0")
        return False
    lo, hi = PRICE_BOUNDS.get(currency, (0.001, 10_000_000))
    if not (lo <= ltp <= hi):
        log.warning(f"  VALIDATION FAIL [{symbol}]: {currency} {ltp:.4f} outside [{lo}, {hi}]")
        return False
    return True


async def get_yahoo_crumb(client: httpx.AsyncClient) -> str | None:
    """
    Obtain Yahoo Finance session crumb.
    Step 1: hit finance.yahoo.com to set cookies.
    Step 2: call getcrumb endpoint → returns a short token string.
    """
    try:
        # Step 1: warm up cookies
        log.info("Authenticating with Yahoo Finance (cookie + crumb)…")
        await client.get(
            "https://finance.yahoo.com/",
            headers={**HEADERS, "Accept": "text/html"},
            timeout=TIMEOUT,
        )
        # Step 2: get crumb
        r = await client.get(
            "https://query1.finance.yahoo.com/v1/test/getcrumb",
            headers={**HEADERS, "Accept": "text/plain"},
            timeout=TIMEOUT,
        )
        if r.status_code == 200 and r.text and r.text != "":
            crumb = r.text.strip()
            log.info(f"Got crumb: {crumb!r}")
            return crumb
        # Fallback: try query2
        r2 = await client.get(
            "https://query2.finance.yahoo.com/v1/test/getcrumb",
            headers={**HEADERS, "Accept": "text/plain"},
            timeout=TIMEOUT,
        )
        if r2.status_code == 200 and r2.text:
            crumb = r2.text.strip()
            log.info(f"Got crumb (q2): {crumb!r}")
            return crumb
        log.error(f"Crumb request failed: {r.status_code} / {r.text!r}")
        return None
    except Exception as e:
        log.error(f"Crumb fetch error: {e}")
        return None


async def fetch_single(
    client: httpx.AsyncClient,
    meta: dict,
    crumb: str,
    semaphore: asyncio.Semaphore,
) -> dict | None:
    """Fetch current price for one ticker via Yahoo Finance v8 chart API."""
    yahoo_ticker = meta["yahoo_ticker"]
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_ticker}"
        f"?interval=1d&range=5d&crumb={crumb}"
    )

    async with semaphore:
        try:
            resp = await client.get(url, headers=HEADERS, timeout=TIMEOUT)

            if resp.status_code == 429:
                log.warning(f"  Rate limited for {yahoo_ticker} — waiting 5s…")
                await asyncio.sleep(5)
                resp = await client.get(url, headers=HEADERS, timeout=TIMEOUT)

            if resp.status_code != 200:
                log.debug(f"  HTTP {resp.status_code} for {yahoo_ticker}")
                return None

            text = resp.text
            if not text or text.strip() == "":
                log.debug(f"  Empty response for {yahoo_ticker}")
                return None

            data       = resp.json()
            result_arr = data.get("chart", {}).get("result", [])
            if not result_arr:
                err = data.get("chart", {}).get("error", {})
                log.debug(f"  No result for {yahoo_ticker}: {err}")
                return None

            chart  = result_arr[0]
            meta_y = chart.get("meta", {})
            ltp    = meta_y.get("regularMarketPrice")

            if not ltp or ltp <= 0:
                return None

            prev_close = (
                meta_y.get("previousClose")
                or meta_y.get("chartPreviousClose")
                or ltp
            )
            change_abs = ltp - prev_close
            change_pct = (change_abs / prev_close * 100) if prev_close else 0.0

            indicators = chart.get("indicators", {}).get("quote", [{}])[0]
            highs   = [h for h in indicators.get("high",   []) if h is not None]
            lows    = [l for l in indicators.get("low",    []) if l is not None]
            opens   = [o for o in indicators.get("open",   []) if o is not None]
            volumes = [v for v in indicators.get("volume", []) if v is not None]

            return {
                "nse_symbol":   meta["nse_symbol"],
                "yahoo_ticker": yahoo_ticker,
                "currency":     meta["currency"],
                "exchange":     meta["exchange"],
                "ltp":          round(float(ltp), 4),
                "open":         round(float(opens[0])   if opens  else ltp, 4),
                "high":         round(float(max(highs)) if highs else ltp, 4),
                "low":          round(float(min(lows))  if lows  else ltp, 4),
                "close":        round(float(prev_close), 4),
                "volume":       int(sum(volumes)) if volumes else 0,
                "change_abs":   round(float(change_abs), 4),
                "change_pct":   round(float(change_pct), 4),
                "week_52_high": round(float(meta_y.get("fiftyTwoWeekHigh", ltp) or ltp), 4),
                "week_52_low":  round(float(meta_y.get("fiftyTwoWeekLow",  ltp) or ltp), 4),
                "market_cap":   float(meta_y.get("marketCap", 0) or 0),
            }

        except Exception as e:
            log.debug(f"  Exception for {yahoo_ticker}: {e}")
            return None


async def fetch_all(stocks: list[dict]) -> list[dict]:
    """Authenticate once, then fetch all tickers with controlled concurrency."""
    semaphore = asyncio.Semaphore(CONCURRENCY)

    # httpx.AsyncClient maintains cookies automatically across requests
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=TIMEOUT,
        http2=False,
    ) as client:
        # Authenticate
        crumb = await get_yahoo_crumb(client)
        if not crumb:
            log.error(
                "FATAL: Could not get Yahoo Finance crumb. "
                "Check internet connection or try again later."
            )
            return []

        log.info(f"Fetching {len(stocks)} tickers (concurrency={CONCURRENCY})…")

        tasks   = [fetch_single(client, s, crumb, semaphore) for s in stocks]
        raw     = await asyncio.gather(*tasks)

    return [r for r in raw if r is not None]


def upsert_prices(cur, ticks: list[dict]) -> tuple[int, int]:
    updated = 0
    failed  = 0

    for tick in ticks:
        sym      = tick["nse_symbol"]
        currency = tick["currency"]
        ltp      = tick["ltp"]

        if not validate_price(ltp, currency, sym):
            failed += 1
            continue

        log.info(
            f"  ✓ {sym:<20} ({tick['yahoo_ticker']:<24}) "
            f"{currency} {ltp:>12,.2f}  Δ{tick['change_pct']:+.2f}%"
        )

        cur.execute("""
            INSERT INTO realtime_quotes
                (nse_symbol, ltp, open, high, low, close, volume,
                 change_abs, change_pct, week_52_high, week_52_low,
                 market_cap, data_source, is_stale, last_updated)
            VALUES (%(sym)s, %(ltp)s, %(open)s, %(high)s, %(low)s, %(close)s,
                    %(volume)s, %(change_abs)s, %(change_pct)s,
                    %(week_52_high)s, %(week_52_low)s, %(market_cap)s,
                    'YAHOO', false, NOW())
            ON CONFLICT (nse_symbol) DO UPDATE
                SET ltp          = EXCLUDED.ltp,
                    open         = EXCLUDED.open,
                    high         = EXCLUDED.high,
                    low          = EXCLUDED.low,
                    close        = EXCLUDED.close,
                    volume       = EXCLUDED.volume,
                    change_abs   = EXCLUDED.change_abs,
                    change_pct   = EXCLUDED.change_pct,
                    week_52_high = EXCLUDED.week_52_high,
                    week_52_low  = EXCLUDED.week_52_low,
                    market_cap   = EXCLUDED.market_cap,
                    data_source  = 'YAHOO',
                    is_stale     = false,
                    last_updated = NOW()
        """, {
            "sym":          sym,
            "ltp":          tick["ltp"],
            "open":         tick["open"],
            "high":         tick["high"],
            "low":          tick["low"],
            "close":        tick["close"],
            "volume":       tick["volume"],
            "change_abs":   tick["change_abs"],
            "change_pct":   tick["change_pct"],
            "week_52_high": tick["week_52_high"],
            "week_52_low":  tick["week_52_low"],
            "market_cap":   tick["market_cap"],
        })

        # Recompute upside/MoS in intrinsic_values from real price
        cur.execute("""
            UPDATE intrinsic_values
            SET cmp              = %(ltp)s,
                upside_pct       = CASE
                    WHEN iv_blended IS NOT NULL AND %(ltp)s > 0
                    THEN ((iv_blended - %(ltp)s) / %(ltp)s * 100)
                    ELSE NULL END,
                margin_of_safety = CASE
                    WHEN iv_blended IS NOT NULL AND iv_blended > 0 AND %(ltp)s > 0
                    THEN ((iv_blended - %(ltp)s) / iv_blended)
                    ELSE NULL END,
                updated_at       = NOW()
            WHERE nse_symbol = %(sym)s
        """, {"ltp": ltp, "sym": sym})

        updated += 1

    return updated, failed


def main():
    log.info("=== StockLens Live Price Fetcher (Yahoo Finance + Cookie Auth) ===")
    log.info("Connecting to database…")

    conn = psycopg2.connect(DB_URL)
    conn.autocommit = True
    cur  = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    cur.execute("""
        SELECT nse_symbol, yahoo_ticker, currency, exchange
        FROM   stocks
        WHERE  yahoo_ticker IS NOT NULL AND is_active = TRUE
        ORDER  BY exchange, nse_symbol
    """)
    stocks = [dict(r) for r in cur.fetchall()]

    cur.execute("SELECT COUNT(*) FROM stocks WHERE yahoo_ticker IS NULL AND is_active = TRUE")
    no_ticker_count = cur.fetchone()[0]

    log.info(f"Found {len(stocks)} stocks with Yahoo ticker.")
    if no_ticker_count:
        log.warning(f"{no_ticker_count} active stocks have no yahoo_ticker — skipped.")

    if not stocks:
        log.error("No stocks found. Run scripts/seed_global_stocks.py first.")
        conn.close()
        return

    t0    = time.time()
    ticks = asyncio.run(fetch_all(stocks))
    elapsed = time.time() - t0

    log.info(f"Received {len(ticks)}/{len(stocks)} responses in {elapsed:.1f}s. Writing to DB…")

    updated, failed_val = upsert_prices(cur, ticks)
    no_data = len(stocks) - len(ticks)

    cur.close()
    conn.close()

    log.info("")
    log.info("=" * 65)
    log.info(f"✅  Written to DB:       {updated}/{len(stocks)}")
    log.info(f"⚠   No Yahoo data:       {no_data}/{len(stocks)}")
    log.info(f"⚠   Validation failures: {failed_val}")
    log.info(f"⏱   Total time:          {elapsed:.1f}s")
    log.info("")
    log.info("Keep prices live (every 60s) in a separate terminal:")
    log.info("  python apps\\worker\\yahoo_price_fetcher.py")
    log.info("")
    log.info("Refresh dashboard → http://localhost:3000")


if __name__ == "__main__":
    main()
