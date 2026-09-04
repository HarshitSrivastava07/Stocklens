"""
StockLens — Full Data Seeder (complete, schema-correct)
=======================================================
Populates all tables needed for the stock detail page:
  - ml_scores, signals, intrinsic_values, financial_ratios,
    realtime_quotes (live Yahoo prices), ml_cluster_results

Schema facts verified from db_models.py:
  - financial_ratios: PK=id (uuid), UniqueConstraint(nse_symbol, as_of_date),
                      NOT NULL: as_of_date, computed_ok has default=True
  - intrinsic_values: PK=nse_symbol
  - ml_scores:        PK=nse_symbol
  - signals:          PK=nse_symbol

Run from stocklens root:
    python seed_all.py
"""
import asyncio
import logging
import os
import random
import sys
import time
from datetime import date, datetime, timezone, timedelta

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("seed_all")

for pkg, pip_name in [("psycopg2", "psycopg2-binary"), ("httpx", "httpx")]:
    try:
        __import__(pkg)
    except ImportError:
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install", pip_name])

import psycopg2
import psycopg2.extras
import httpx

DB_URL = os.environ.get(
    "DATABASE_SYNC_URL", "postgresql://postgres:password@localhost:5432/stocklens"
)

SIGNAL_VALUES = {
    "GREEN":  "POTENTIALLY_UNDERVALUED",
    "YELLOW": "REVIEW_REQUIRED",
    "RED":    "POSSIBLY_OVERVALUED",
    "GREY":   "INSUFFICIENT_DATA",
}
SIGNAL_LABELS = {
    "GREEN":  "Potentially Undervalued",
    "YELLOW": "Review Required / Watch",
    "RED":    "Possibly Overvalued / Risk",
    "GREY":   "Insufficient Data",
}
CLUSTER_LABELS = [
    "QUALITY_COMPOUNDER", "DEEP_VALUE", "GARP",
    "CYCLICAL_RECOVERY", "HIGH_GROWTH_EXPENSIVE", "VALUE_TRAP",
    "MOMENTUM_TRAP", "DISTRESSED", "LOW_LIQUIDITY",
]

# Yahoo Finance auth
YAHOO_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "application/json",
    "Accept-Language": "en-US,en;q=0.9",
}
# Per-currency price sanity bounds
PRICE_BOUNDS = {
    "INR": (0.5, 250_000), "USD": (0.01, 100_000), "JPY": (1, 2_000_000),
    "GBP": (0.01, 100_000), "EUR": (0.01, 100_000),
    "HKD": (0.01, 500_000), "AUD": (0.01, 100_000),
}


def validate_price(ltp, currency, symbol):
    if ltp is None or ltp <= 0:
        return False
    lo, hi = PRICE_BOUNDS.get(currency, (0.001, 10_000_000))
    return lo <= ltp <= hi


# ─── Step 1: Seed ml_scores + signals (GREY placeholder) ─────
def step1_seed_ml_and_signals(conn):
    cur = conn.cursor()
    cur.execute("SELECT nse_symbol FROM stocks WHERE is_active = TRUE ORDER BY nse_symbol")
    symbols = [r[0] for r in cur.fetchall()]
    log.info(f"  Seeding ml_scores + signals for {len(symbols)} stocks...")

    for sym in symbols:
        r = random.Random(hash(sym))
        risk   = int(r.uniform(10, 85))
        rlevel = "LOW" if risk <= 33 else "MEDIUM" if risk <= 66 else "HIGH"
        fund   = int(r.uniform(20, 90))
        growth = int(r.uniform(15, 95))
        conf   = r.choice(["HIGH", "HIGH", "MEDIUM", "MEDIUM", "MEDIUM", "LOW"])
        cluster = r.choice(CLUSTER_LABELS)

        # ml_scores (PK = nse_symbol)
        cur.execute("DELETE FROM ml_scores WHERE nse_symbol = %s", (sym,))
        cur.execute("""
            INSERT INTO ml_scores
                (nse_symbol, risk_score, risk_level, valuation_confidence,
                 fundamental_score, growth_outlook_score,
                 risk_drivers, confidence_factors)
            VALUES (%s, %s, %s, %s, %s, %s, '[]'::jsonb, '[]'::jsonb)
        """, (sym, risk, rlevel, conf, fund, growth))

        # ml_cluster_results (PK=id uuid NOT NULL, unique on nse_symbol + run_date)
        cur.execute("""
            INSERT INTO ml_cluster_results
                (id, nse_symbol, run_date, cluster_label, cluster_id,
                 features_used, feature_values, algorithm)
            VALUES (gen_random_uuid(), %s, CURRENT_DATE, %s, %s,
                    '[]'::jsonb, '{}'::jsonb, 'KMEANS')
            ON CONFLICT (nse_symbol, run_date) DO UPDATE
                SET cluster_label = EXCLUDED.cluster_label,
                    cluster_id    = EXCLUDED.cluster_id
        """, (sym, cluster, r.randint(0, 8)))

        # signals (PK = nse_symbol) — GREY placeholder until live prices arrive
        cur.execute("DELETE FROM signals WHERE nse_symbol = %s", (sym,))
        cur.execute("""
            INSERT INTO signals
                (nse_symbol, signal, signal_color, signal_label, main_reason,
                 fundamental_score, growth_score, risk_score, valuation_confidence,
                 conditions, blocking_flags, data_freshness)
            VALUES (%s, 'INSUFFICIENT_DATA', 'GREY', %s, %s,
                    %s, %s, %s, %s,
                    '{}'::jsonb, '["Awaiting live price data"]'::jsonb, 'MOCK')
        """, (sym, SIGNAL_LABELS["GREY"], "Awaiting live price data",
              fund, growth, risk, conf))

    log.info(f"  ✅ {len(symbols)} ml_scores + signals seeded (GREY)")
    cur.close()


# ─── Step 2: Fetch live Yahoo prices → realtime_quotes ────────
async def _get_crumb(client):
    try:
        await client.get(
            "https://finance.yahoo.com/",
            headers={**YAHOO_HEADERS, "Accept": "text/html"},
            timeout=15,
        )
        r = await client.get(
            "https://query1.finance.yahoo.com/v1/test/getcrumb",
            headers={**YAHOO_HEADERS, "Accept": "text/plain"},
            timeout=15,
        )
        if r.status_code == 200 and r.text:
            return r.text.strip()
    except Exception as e:
        log.error(f"Crumb error: {e}")
    return None


async def _fetch_one(client, meta, crumb, sem):
    yt = meta["yahoo_ticker"]
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{yt}?interval=1d&range=5d&crumb={crumb}"
    async with sem:
        try:
            r = await client.get(url, headers=YAHOO_HEADERS, timeout=15)
            if r.status_code == 429:
                await asyncio.sleep(5)
                r = await client.get(url, headers=YAHOO_HEADERS, timeout=15)
            if r.status_code != 200 or not r.text.strip():
                return None
            data    = r.json()
            results = data.get("chart", {}).get("result", [])
            if not results:
                return None
            m   = results[0].get("meta", {})
            ltp = m.get("regularMarketPrice")
            if not ltp or ltp <= 0:
                return None
            prev = m.get("previousClose") or ltp
            return {
                "nse_symbol":   meta["nse_symbol"],
                "yahoo_ticker": yt,
                "currency":     meta["currency"],
                "ltp":          round(float(ltp), 4),
                "open":         round(float(m.get("regularMarketOpen",  ltp) or ltp), 4),
                "high":         round(float(m.get("regularMarketDayHigh", ltp) or ltp), 4),
                "low":          round(float(m.get("regularMarketDayLow",  ltp) or ltp), 4),
                "close":        round(float(prev), 4),
                "volume":       int(m.get("regularMarketVolume", 0) or 0),
                "change_abs":   round(float(ltp - prev), 4),
                "change_pct":   round(float((ltp - prev) / prev * 100) if prev else 0, 4),
                "week_52_high": round(float(m.get("fiftyTwoWeekHigh", ltp) or ltp), 4),
                "week_52_low":  round(float(m.get("fiftyTwoWeekLow",  ltp) or ltp), 4),
                "market_cap":   float(m.get("marketCap", 0) or 0),
            }
        except Exception as e:
            log.debug(f"  Error {yt}: {e}")
            return None


async def step2_fetch_prices(conn):
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cur.execute("""
        SELECT nse_symbol, yahoo_ticker, currency, exchange
        FROM   stocks
        WHERE  yahoo_ticker IS NOT NULL AND is_active = TRUE
        ORDER  BY exchange, nse_symbol
    """)
    stocks = [dict(r) for r in cur.fetchall()]
    log.info(f"  Fetching prices for {len(stocks)} tickers from Yahoo Finance...")

    sem = asyncio.Semaphore(8)
    async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
        crumb = await _get_crumb(client)
        if not crumb:
            log.error("  Could not get Yahoo crumb — skipping price fetch")
            cur.close()
            return {}
        log.info(f"  Yahoo crumb: {crumb!r}")
        ticks = await asyncio.gather(*[_fetch_one(client, s, crumb, sem) for s in stocks])

    prices = {}   # nse_symbol → ltp (validated)
    written = 0
    for tick in ticks:
        if not tick:
            continue
        if not validate_price(tick["ltp"], tick["currency"], tick["nse_symbol"]):
            continue
        cur.execute("""
            INSERT INTO realtime_quotes
                (nse_symbol, ltp, open, high, low, close, volume,
                 change_abs, change_pct, week_52_high, week_52_low,
                 market_cap, data_source, is_stale, last_updated)
            VALUES (%(sym)s, %(ltp)s, %(open)s, %(high)s, %(low)s, %(close)s,
                    %(vol)s, %(ca)s, %(cp)s, %(w52h)s, %(w52l)s, %(mc)s,
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
            "sym":  tick["nse_symbol"], "ltp":  tick["ltp"],
            "open": tick["open"],       "high": tick["high"],
            "low":  tick["low"],        "close": tick["close"],
            "vol":  tick["volume"],     "ca":   tick["change_abs"],
            "cp":   tick["change_pct"], "w52h": tick["week_52_high"],
            "w52l": tick["week_52_low"],"mc":   tick["market_cap"],
        })
        prices[tick["nse_symbol"]] = tick["ltp"]
        written += 1

    log.info(f"  ✅ {written}/{len(stocks)} live prices written to realtime_quotes")
    cur.close()
    return prices


# ─── Step 3: Compute IV + ratios + update signals with real data
def step3_seed_iv_ratios_signals(conn, live_prices: dict):
    cur = conn.cursor()
    cur.execute("""
        SELECT s.nse_symbol, s.currency,
               COALESCE(q.ltp, NULL) as real_ltp
        FROM   stocks s
        LEFT   JOIN realtime_quotes q
               ON s.nse_symbol = q.nse_symbol AND q.data_source = 'YAHOO'
        WHERE  s.is_active = TRUE
        ORDER  BY s.nse_symbol
    """)
    stocks = cur.fetchall()
    today  = date.today()
    log.info(f"  Computing IV + ratios + signals for {len(stocks)} stocks...")

    iv_ok  = 0
    rat_ok = 0

    for sym, currency, db_ltp in stocks:
        r   = random.Random(hash(sym))
        # psycopg2 returns Decimal for NUMERIC columns — must cast to float for arithmetic
        cmp = float(db_ltp) if db_ltp is not None else None
        has_real_price = cmp is not None and cmp > 0

        conf    = r.choice(["HIGH", "HIGH", "MEDIUM", "MEDIUM", "MEDIUM", "LOW"])
        cluster = r.choice(CLUSTER_LABELS)

        # ── Intrinsic values ──────────────────────────────────
        iv_base    = cmp * r.uniform(0.7, 1.6)   if has_real_price else None
        iv_bear    = iv_base * 0.75               if iv_base else None
        iv_bull    = iv_base * 1.3                if iv_base else None
        iv_blended = (iv_bear * 0.25 + iv_base * 0.50 + iv_bull * 0.25) if iv_base else None
        upside_pct = ((iv_blended - cmp) / cmp * 100) if (iv_blended and cmp and cmp > 0) else None
        mos        = ((iv_blended - cmp) / iv_blended)  if (iv_blended and cmp and iv_blended > 0) else None

        cur.execute("DELETE FROM intrinsic_values WHERE nse_symbol = %s", (sym,))
        cur.execute("""
            INSERT INTO intrinsic_values
                (nse_symbol, cmp, iv_bear, iv_base, iv_bull, iv_blended,
                 upside_pct, margin_of_safety, primary_model, valuation_confidence)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'DCF', %s)
        """, (
            sym,
            round(cmp, 2)        if cmp        else None,
            round(iv_bear, 2)    if iv_bear    else None,
            round(iv_base, 2)    if iv_base    else None,
            round(iv_bull, 2)    if iv_bull    else None,
            round(iv_blended, 2) if iv_blended else None,
            round(upside_pct, 4) if upside_pct is not None else None,
            round(mos, 4)        if mos        is not None else None,
            conf,
        ))
        iv_ok += 1

        # ── Financial ratios (unique on nse_symbol + as_of_date) ─
        # Delete old row for today then insert fresh — avoids ON CONFLICT complexity
        cur.execute("""
            DELETE FROM financial_ratios
            WHERE nse_symbol = %s AND as_of_date = %s
        """, (sym, today))
        cur.execute("""
            INSERT INTO financial_ratios
                (id, nse_symbol, as_of_date, computed_ok,
                 pe, pb, ps, ev_ebitda,
                 roe, roce, roa,
                 ebitda_margin, net_margin,
                 revenue_cagr_3y, pat_cagr_3y,
                 debt_equity, interest_coverage, cfo_pat,
                 data_completeness, error_fields)
            VALUES
                (gen_random_uuid(), %s, %s, TRUE,
                 %s, %s, %s, %s,
                 %s, %s, %s,
                 %s, %s,
                 %s, %s,
                 %s, %s, %s,
                 %s, '[]'::jsonb)
        """, (
            sym, today,
            round(r.uniform(12,  45), 1),   # pe
            round(r.uniform(1.5,  8), 2),   # pb
            round(r.uniform(1,   10), 2),   # ps
            round(r.uniform(8,   25), 1),   # ev_ebitda
            round(r.uniform(0.10, 0.35), 4),# roe
            round(r.uniform(0.12, 0.28), 4),# roce
            round(r.uniform(0.05, 0.15), 4),# roa
            round(r.uniform(0.12, 0.35), 4),# ebitda_margin
            round(r.uniform(0.06, 0.20), 4),# net_margin
            round(r.uniform(0.08, 0.22), 4),# revenue_cagr_3y
            round(r.uniform(0.06, 0.25), 4),# pat_cagr_3y
            round(r.uniform(0,    1.5),  2), # debt_equity
            round(r.uniform(3,   20),    1), # interest_coverage
            round(r.uniform(0.7,  1.5),  2), # cfo_pat
            round(r.uniform(0.65, 0.95), 2), # data_completeness
        ))
        rat_ok += 1

        # ── Update signals with real upside / MoS ────────────
        if has_real_price and upside_pct is not None:
            sig = "GREEN" if upside_pct > 20 else "YELLOW" if upside_pct > 0 else "RED"
            cur.execute("""
                UPDATE signals
                SET signal           = %s,
                    signal_color     = %s,
                    signal_label     = %s,
                    main_reason      = %s,
                    upside_pct       = %s,
                    margin_of_safety = %s,
                    data_freshness   = 'LIVE',
                    blocking_flags   = '[]'::jsonb,
                    updated_at       = NOW()
                WHERE nse_symbol = %s
            """, (
                SIGNAL_VALUES[sig], sig, SIGNAL_LABELS[sig],
                f"Upside {upside_pct:+.1f}% based on DCF model vs CMP {currency} {cmp:,.0f}",
                round(upside_pct, 4),
                round(mos, 4) if mos is not None else None,
                sym,
            ))

        # ── Financial results (4 quarters) ───────────────────
        rev_base = (cmp * r.uniform(80, 500)) if cmp else r.uniform(500, 50000)
        for q in range(4):
            period_end = today - timedelta(days=91 * q)
            growth     = 1 + r.uniform(0.05, 0.20)
            cur.execute("""
                INSERT INTO financial_results
                    (id, nse_symbol, period_end, period_type,
                     revenue, ebitda, pat, eps,
                     data_source, is_verified, confidence_level, has_exceptional)
                VALUES
                    (gen_random_uuid(), %s, %s, 'Q',
                     %s, %s, %s, %s,
                     'DEV_SEED', TRUE, 'HIGH', FALSE)
                ON CONFLICT (nse_symbol, period_type, period_end) DO NOTHING
            """, (
                sym, period_end,
                round(rev_base * (growth ** q), 2),
                round(rev_base * 0.22 * (growth ** q), 2),
                round(rev_base * 0.12 * (growth ** q), 2),
                round(rev_base * 0.12 / r.uniform(50, 500), 2),
            ))

    log.info(f"  ✅ {iv_ok} intrinsic values  |  {rat_ok} ratio rows inserted")
    cur.close()


def main():
    log.info("=== StockLens Full Data Seeder ===")
    conn = psycopg2.connect(DB_URL)
    conn.autocommit = True

    log.info("\n── Step 1: ML scores + signals (GREY) ──────────────────")
    step1_seed_ml_and_signals(conn)

    log.info("\n── Step 2: Live Yahoo Finance prices ───────────────────")
    t0 = time.time()
    live_prices = asyncio.run(step2_fetch_prices(conn))
    log.info(f"  Prices fetched in {time.time()-t0:.1f}s — {len(live_prices)} valid")

    log.info("\n── Step 3: Intrinsic values + ratios + signals update ──")
    step3_seed_iv_ratios_signals(conn, live_prices)

    conn.close()

    log.info("\n" + "=" * 60)
    log.info("✅ Full seed complete!")
    log.info(f"   {len(live_prices)}/320 live prices from Yahoo Finance")
    log.info("   Signals upgraded GREEN/YELLOW/RED for stocks with real prices")
    log.info("   GREY for stocks where Yahoo returned no data")
    log.info("")
    log.info("Refresh dashboard → http://localhost:3000")
    log.info("Keep prices live (every 60s):")
    log.info("  python apps\\worker\\yahoo_price_fetcher.py")


if __name__ == "__main__":
    main()
