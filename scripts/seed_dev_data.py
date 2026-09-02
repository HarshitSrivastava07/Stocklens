"""
Development Data Seeder
Seeds realistic mock data for development/testing when real data isn't available.
Only inserts if DB is empty — safe to run multiple times.

Run: python scripts/seed_dev_data.py
"""
import asyncio
import json
import logging
import os
import random
from datetime import date, timedelta
from decimal import Decimal

import asyncpg
from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger("seed_dev")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

DB_URL = os.environ.get("DATABASE_SYNC_URL", "postgresql://postgres:password@localhost:5432/stocklens")

# ── Sample stocks (Nifty50 subset for dev) ────────────────────
SAMPLE_STOCKS = [
    ("RELIANCE",  "Reliance Industries Limited",  "ENERGY",        "LARGE"),
    ("TCS",       "Tata Consultancy Services",    "TECHNOLOGY",    "LARGE"),
    ("INFY",      "Infosys Limited",              "TECHNOLOGY",    "LARGE"),
    ("HDFCBANK",  "HDFC Bank Limited",            "BANKING",       "LARGE"),
    ("ICICIBANK", "ICICI Bank Limited",           "BANKING",       "LARGE"),
    ("BHARTIARTL","Bharti Airtel Limited",        "TELECOM",       "LARGE"),
    ("KOTAKBANK", "Kotak Mahindra Bank",          "BANKING",       "LARGE"),
    ("LT",        "Larsen & Toubro Limited",      "INDUSTRIALS",   "LARGE"),
    ("HINDUNILVR","Hindustan Unilever Limited",   "FMCG",         "LARGE"),
    ("SBIN",      "State Bank of India",          "BANKING",       "LARGE"),
    ("MARUTI",    "Maruti Suzuki India",          "AUTOMOTIVE",    "LARGE"),
    ("SUNPHARMA", "Sun Pharmaceutical Industries","PHARMA",        "LARGE"),
    ("WIPRO",     "Wipro Limited",               "TECHNOLOGY",    "LARGE"),
    ("HCLTECH",   "HCL Technologies Limited",    "TECHNOLOGY",    "LARGE"),
    ("AXISBANK",  "Axis Bank Limited",           "BANKING",       "LARGE"),
    ("BAJFINANCE","Bajaj Finance Limited",        "NBFC",         "LARGE"),
    ("NTPC",      "NTPC Limited",                "ENERGY",        "LARGE"),
    ("ONGC",      "Oil and Natural Gas Corp",    "ENERGY",        "LARGE"),
    ("TATAMOTORS","Tata Motors Limited",          "AUTOMOTIVE",    "LARGE"),
    ("NESTLEIND", "Nestle India Limited",         "FMCG",         "LARGE"),
]

SIGNALS = ["GREEN", "GREEN", "GREEN", "YELLOW", "YELLOW", "RED", "GREY"]
SIGNAL_LABELS = {
    "GREEN": "Potentially Undervalued",
    "YELLOW": "Fairly Valued / Watch",
    "RED": "Possibly Overvalued / Risk",
    "GREY": "Insufficient Data",
}
CLUSTER_LABELS = ["QUALITY_COMPOUNDER", "VALUE_TRAP", "GROWTH_MOMENTUM", "TURNAROUND", "DIVIDEND_YIELD", "CYCLICAL"]


def rand_price(base: float, spread: float = 0.1) -> float:
    return round(base * (1 + random.uniform(-spread, spread)), 2)


async def seed_if_empty(conn) -> bool:
    count = await conn.fetchval("SELECT COUNT(*) FROM stocks")
    if count and count > 0:
        log.info(f"DB already has {count} stocks — skipping seed")
        return False
    return True


async def run():
    conn = await asyncpg.connect(DB_URL)
    try:
        if not await seed_if_empty(conn):
            return

        log.info("Seeding development data...")

        # Insert sector classifications
        sector_ids = {}
        sectors = list(set((row[2], row[2], "Various Industries") for row in SAMPLE_STOCKS))
        for macro, sector, industry in sectors:
            # No unique constraint on (sector,industry), so insert then select
            sid = await conn.fetchval("""
                INSERT INTO sector_classification (macro_sector, sector, industry)
                VALUES ($1, $2, $3)
                ON CONFLICT DO NOTHING
                RETURNING id
            """, macro, sector, industry)
            if sid is None:
                # Row already existed, fetch its id
                sid = await conn.fetchval(
                    "SELECT id FROM sector_classification WHERE sector=$1 AND industry=$2",
                    sector, industry
                )
            sector_ids[sector] = sid

        # Insert stocks
        base_prices = {
            "RELIANCE": 2850, "TCS": 4200, "INFY": 1850, "HDFCBANK": 1650,
            "ICICIBANK": 1280, "BHARTIARTL": 1750, "KOTAKBANK": 1900, "LT": 3600,
            "HINDUNILVR": 2400, "SBIN": 820, "MARUTI": 12500, "SUNPHARMA": 1720,
            "WIPRO": 580, "HCLTECH": 1850, "AXISBANK": 1150, "BAJFINANCE": 7200,
            "NTPC": 380, "ONGC": 290, "TATAMOTORS": 1050, "NESTLEIND": 2350,
        }

        for symbol, company, sector, cap in SAMPLE_STOCKS:
            cmp = base_prices.get(symbol, random.uniform(100, 5000))
            await conn.execute("""
                INSERT INTO stocks
                    (nse_symbol, isin, company_name, market_cap_category, sector_id,
                     is_nifty50, is_nifty500, is_fno, is_active)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, TRUE)
                ON CONFLICT (nse_symbol) DO NOTHING
            """,
                symbol,
                f"IN{symbol}XXXXX01",
                company,
                cap,
                sector_ids.get(sector),
                True,  # all are Nifty50 for dev
                True,
                symbol in ("RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK"),
            )

            # Realtime quotes  — BUG1 FIX: correct column names change_abs, last_updated
            change_abs = rand_price(cmp, 0.02) - cmp
            await conn.execute("""
                INSERT INTO realtime_quotes
                    (nse_symbol, ltp, open, high, low, close, change_abs, change_pct, volume, last_updated)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, NOW())
                ON CONFLICT (nse_symbol) DO UPDATE
                    SET ltp=$2, change_abs=$7, change_pct=$8, last_updated=NOW()
            """,
                symbol, cmp,
                rand_price(cmp, 0.005), rand_price(cmp * 1.01, 0.005),
                rand_price(cmp * 0.99, 0.005), cmp,
                change_abs, (change_abs / cmp) * 100,
                random.randint(100000, 5000000),
            )

            # Financial results (last 4 quarters)
            for q in range(4):
                period_end = date.today() - timedelta(days=90 * q)
                growth = 1 + random.uniform(0.05, 0.20)
                rev_base = cmp * random.uniform(80, 500)
                await conn.execute("""
                    INSERT INTO financial_results
                        (nse_symbol, period_end, period_type, revenue, ebitda, pat, eps,
                         data_source, is_verified, confidence_level)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, TRUE, $9)
                    ON CONFLICT DO NOTHING
                """,
                    symbol, period_end, "Q",
                    round(rev_base * (growth ** q), 2),
                    round(rev_base * 0.22 * (growth ** q), 2),
                    round(rev_base * 0.12 * (growth ** q), 2),
                    round(rev_base * 0.12 / random.uniform(50, 500), 2),
                    "DEV_SEED", "HIGH",
                )

            # Financial ratios
            await conn.execute("""
                INSERT INTO financial_ratios
                    (nse_symbol, pe, pb, ps, ev_ebitda, roe, roce, roa,
                     ebitda_margin, net_margin, revenue_cagr_3y, pat_cagr_3y,
                     debt_equity, interest_coverage, cfo_pat, data_completeness)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16)
                ON CONFLICT (nse_symbol) DO UPDATE
                    SET pe=$2, roe=$6, updated_at=NOW()
            """,
                symbol,
                round(random.uniform(12, 45), 1),   # PE
                round(random.uniform(1.5, 8), 2),   # PB
                round(random.uniform(1, 10), 2),    # PS
                round(random.uniform(8, 25), 1),    # EV/EBITDA
                round(random.uniform(0.10, 0.35), 4), # ROE
                round(random.uniform(0.12, 0.28), 4), # ROCE
                round(random.uniform(0.05, 0.15), 4), # ROA
                round(random.uniform(0.12, 0.35), 4), # EBITDA margin
                round(random.uniform(0.06, 0.20), 4), # Net margin
                round(random.uniform(0.08, 0.22), 4), # Rev CAGR 3Y
                round(random.uniform(0.06, 0.25), 4), # PAT CAGR 3Y
                round(random.uniform(0, 1.5), 2),   # D/E
                round(random.uniform(3, 20), 1),    # ICR
                round(random.uniform(0.7, 1.5), 2), # CFO/PAT
                round(random.uniform(0.65, 0.95), 2), # Data completeness
            )

            # Intrinsic values — some undervalued, some not
            signal_color = random.choice(SIGNALS)
            iv_base = cmp * random.uniform(0.7, 1.6)
            iv_bear = iv_base * 0.75
            iv_bull = iv_base * 1.3
            iv_blended = iv_bear * 0.25 + iv_base * 0.50 + iv_bull * 0.25
            upside = ((iv_blended - cmp) / cmp) * 100

            await conn.execute("""
                INSERT INTO intrinsic_values
                    (nse_symbol, iv_bear, iv_base, iv_bull, iv_blended, cmp,
                     upside_pct, margin_of_safety, primary_model, valuation_confidence)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
                ON CONFLICT (nse_symbol) DO UPDATE
                    SET iv_blended=$5, cmp=$6, upside_pct=$7, updated_at=NOW()
            """,
                symbol,
                round(iv_bear, 2), round(iv_base, 2), round(iv_bull, 2),
                round(iv_blended, 2), round(cmp, 2),
                round(upside, 2),
                round((iv_blended - cmp) / iv_blended, 4),
                "DCF",
                random.choice(["HIGH", "MEDIUM", "MEDIUM", "LOW"]),
            )

            # ML Scores  — BUG3 FIX: cluster_label/cluster_id don't belong in ml_scores
            risk = random.randint(15, 75)
            cluster = random.choice(CLUSTER_LABELS)
            await conn.execute("""
                INSERT INTO ml_scores
                    (nse_symbol, fundamental_score, growth_outlook_score, risk_score, risk_level,
                     risk_drivers, valuation_confidence)
                VALUES ($1,$2,$3,$4,$5,$6,$7)
                ON CONFLICT (nse_symbol) DO UPDATE
                    SET fundamental_score=$2, risk_score=$4, updated_at=NOW()
            """,
                symbol,
                random.randint(40, 92),  # fundamental
                random.randint(35, 88),  # growth
                risk,
                "HIGH" if risk > 65 else "MEDIUM" if risk > 40 else "LOW",
                json.dumps(["High debt", "Margin pressure"] if risk > 60 else ["Cyclical"]),
                "HIGH" if upside > 25 else "MEDIUM" if upside > 10 else "LOW",
            )
            # Write cluster to ml_cluster_results (correct table)
            import datetime as _dt
            await conn.execute("""
                INSERT INTO ml_cluster_results
                    (nse_symbol, run_date, cluster_label, cluster_id, algorithm)
                VALUES ($1, $2, $3, $4, 'KMEANS')
                ON CONFLICT (nse_symbol, run_date) DO UPDATE
                    SET cluster_label=$3, cluster_id=$4
            """, symbol, _dt.date.today(), cluster, random.randint(0, 8))

            # Signal  — BUG2 FIX: `signal` column is NOT NULL, must be provided
            SIGNAL_VALUES = {
                "GREEN": "POTENTIALLY_UNDERVALUED",
                "YELLOW": "REVIEW_REQUIRED",
                "RED": "AVOID_OVERVALUED",
                "GREY": "INSUFFICIENT_DATA",
            }
            await conn.execute("""
                INSERT INTO signals
                    (nse_symbol, signal, signal_color, signal_label, main_reason, conditions, blocking_flags)
                VALUES ($1,$2,$3,$4,$5,$6,$7)
                ON CONFLICT (nse_symbol) DO UPDATE
                    SET signal=$2, signal_color=$3, signal_label=$4, updated_at=NOW()
            """,
                symbol,
                SIGNAL_VALUES[signal_color],
                signal_color,
                SIGNAL_LABELS[signal_color],
                f"Blended IV ₹{iv_blended:.0f} vs CMP ₹{cmp:.0f} ({upside:+.1f}%)",
                json.dumps({"upside_ok": upside > 15, "risk_ok": risk < 60, "fundamental_ok": True}),
                json.dumps([] if signal_color == "GREEN" else ["Low margin of safety"] if signal_color == "YELLOW" else ["Overvalued"]),
            )

            # Price candles (last 90 trading days)
            price = cmp * 0.85
            for i in range(90):
                trade_date = date.today() - timedelta(days=90 - i)
                if trade_date.weekday() >= 5:
                    continue
                daily_return = random.gauss(0.0005, 0.015)
                price *= (1 + daily_return)
                day_high = price * random.uniform(1.002, 1.02)
                day_low = price * random.uniform(0.98, 0.998)
                day_open = random.uniform(day_low, day_high)
                try:
                    await conn.execute("""
                        INSERT INTO price_candles_daily
                            (nse_symbol, trade_date, open, high, low, close, volume)
                        VALUES ($1,$2,$3,$4,$5,$6,$7)
                        ON CONFLICT (nse_symbol, trade_date) DO NOTHING
                    """,
                        symbol, trade_date,
                        round(day_open, 2), round(day_high, 2),
                        round(day_low, 2), round(price, 2),
                        random.randint(50000, 2000000),
                    )
                except Exception:
                    pass

        log.info(f"""
╔════════════════════════════════════════════╗
║  Development Seed Complete!                ║
╠════════════════════════════════════════════╣
║  Stocks seeded:     {len(SAMPLE_STOCKS):<25}║
║  With: prices, ratios, valuations,         ║
║        signals, ML scores, candles         ║
║                                            ║
║  Dashboard should now show data!           ║
║  Visit: http://localhost:3000              ║
╚════════════════════════════════════════════╝
""")

    finally:
        await conn.close()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="Force re-seed even if data exists")
    args = parser.parse_args()
    asyncio.run(run())
