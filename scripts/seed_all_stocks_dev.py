"""
Populate Realistic mock/dev data (Financials, Ratios, Intrinsic Value, Signals, ML Scores, and Daily Price History Candles)
for ALL stocks currently in the database.
Uses the stock's current market price (LTP) from the realtime_quotes table as the base price.
"""
import asyncio
import json
import logging
import os
import random
from datetime import date, timedelta
import datetime as _dt
import asyncpg
from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger("seed_all_dev")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

DB_URL = os.environ.get("DATABASE_SYNC_URL", "postgresql://postgres:password@localhost:5432/stocklens")

SIGNALS = ["GREEN", "GREEN", "GREEN", "YELLOW", "YELLOW", "RED", "GREY"]
SIGNAL_LABELS = {
    "GREEN": "Potentially Undervalued",
    "YELLOW": "Review Required / Watch",
    "RED": "Possibly Overvalued / Risk",
    "GREY": "Insufficient Data",
}
CLUSTER_LABELS = [
    "QUALITY_COMPOUNDER", "DEEP_VALUE", "GARP", "CYCLICAL_RECOVERY",
    "HIGH_GROWTH_EXPENSIVE", "VALUE_TRAP", "MOMENTUM_TRAP", "DISTRESSED", "LOW_LIQUIDITY"
]

def rand_price(base: float, spread: float = 0.1) -> float:
    return round(base * (1 + random.uniform(-spread, spread)), 2)

async def run():
    log.info(f"Connecting to database: {DB_URL}")
    conn = await asyncpg.connect(DB_URL)
    try:
        # Fetch all active stocks with their current market prices (LTP)
        rows = await conn.fetch("""
            SELECT s.nse_symbol, s.company_name, s.exchange, s.currency, COALESCE(q.ltp, 100.0) as cmp
            FROM stocks s
            LEFT JOIN realtime_quotes q ON s.nse_symbol = q.nse_symbol
            WHERE s.is_active = TRUE
        """)
        
        log.info(f"Found {len(rows)} active stocks to populate data for.")
        
        success_count = 0
        for row in rows:
            symbol = row["nse_symbol"]
            cmp = float(row["cmp"])
            
            # 1. Financial results (last 4 quarters)
            for q in range(4):
                period_end = date.today() - timedelta(days=90 * q)
                growth = 1 + random.uniform(0.05, 0.20)
                rev_base = cmp * random.uniform(80, 500)
                await conn.execute("""
                    INSERT INTO financial_results
                        (nse_symbol, period_end, period_type, revenue, ebitda, pat, eps,
                         data_source, is_verified, confidence_level)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, TRUE, $9)
                    ON CONFLICT (nse_symbol, period_type, period_end) DO NOTHING
                """,
                    symbol, period_end, "Q",
                    round(rev_base * (growth ** q), 2),
                    round(rev_base * 0.22 * (growth ** q), 2),
                    round(rev_base * 0.12 * (growth ** q), 2),
                    round(rev_base * 0.12 / random.uniform(50, 500), 2),
                    "DEV_SEED", "HIGH",
                )

            # 2. Financial ratios
            await conn.execute("""
                INSERT INTO financial_ratios
                    (nse_symbol, pe, pb, ps, ev_ebitda, roe, roce, roa,
                     ebitda_margin, net_margin, revenue_cagr_3y, pat_cagr_3y,
                     debt_equity, interest_coverage, cfo_pat, data_completeness)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16)
                ON CONFLICT (nse_symbol) DO UPDATE
                    SET pe=$2, pb=$3, ps=$4, ev_ebitda=$5, roe=$6, roce=$7, roa=$8,
                        ebitda_margin=$9, net_margin=$10, revenue_cagr_3y=$11, pat_cagr_3y=$12,
                        debt_equity=$13, interest_coverage=$14, cfo_pat=$15, data_completeness=$16,
                        updated_at=NOW()
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

            # 3. Intrinsic values
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
                    SET iv_bear=$2, iv_base=$3, iv_bull=$4, iv_blended=$5, cmp=$6,
                        upside_pct=$7, margin_of_safety=$8, primary_model=$9, valuation_confidence=$10,
                        updated_at=NOW()
            """,
                symbol,
                round(iv_bear, 2), round(iv_base, 2), round(iv_bull, 2),
                round(iv_blended, 2), round(cmp, 2),
                round(upside, 2),
                round((iv_blended - cmp) / iv_blended, 4) if iv_blended else 0.0,
                "DCF",
                random.choice(["HIGH", "MEDIUM", "MEDIUM", "LOW"]),
            )

            # 4. ML Scores
            risk = random.randint(15, 75)
            cluster = random.choice(CLUSTER_LABELS)
            await conn.execute("""
                INSERT INTO ml_scores
                    (nse_symbol, fundamental_score, growth_outlook_score, risk_score, risk_level,
                     risk_drivers, valuation_confidence)
                VALUES ($1,$2,$3,$4,$5,$6,$7)
                ON CONFLICT (nse_symbol) DO UPDATE
                    SET fundamental_score=$2, growth_outlook_score=$3, risk_score=$4, risk_level=$5,
                        risk_drivers=$6, valuation_confidence=$7, updated_at=NOW()
            """,
                symbol,
                random.randint(40, 92),  # fundamental
                random.randint(35, 88),  # growth
                risk,
                "HIGH" if risk > 65 else "MEDIUM" if risk > 40 else "LOW",
                json.dumps(["High debt", "Margin pressure"] if risk > 60 else ["Cyclical"]),
                "HIGH" if upside > 25 else "MEDIUM" if upside > 10 else "LOW",
            )
            
            # 5. ML Cluster Results
            await conn.execute("""
                INSERT INTO ml_cluster_results
                    (nse_symbol, run_date, cluster_label, cluster_id, algorithm)
                VALUES ($1, $2, $3, $4, 'KMEANS')
                ON CONFLICT (nse_symbol, run_date) DO UPDATE
                    SET cluster_label=$3, cluster_id=$4
            """, symbol, _dt.date.today(), cluster, random.randint(0, 8))

            # 6. Signals
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
                    SET signal=$2, signal_color=$3, signal_label=$4, main_reason=$5, conditions=$6, blocking_flags=$7, updated_at=NOW()
            """,
                symbol,
                SIGNAL_VALUES[signal_color],
                signal_color,
                SIGNAL_LABELS[signal_color],
                f"Blended IV {row['currency']} {iv_blended:.2f} vs CMP {row['currency']} {cmp:.2f} ({upside:+.1f}%)",
                json.dumps({"upside_ok": upside > 15, "risk_ok": risk < 60, "fundamental_ok": True}),
                json.dumps([] if signal_color == "GREEN" else ["Low margin of safety"] if signal_color == "YELLOW" else ["Overvalued"]),
            )

            # 7. Price candles (last 90 trading days)
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

            success_count += 1
            if success_count % 50 == 0:
                log.info(f"Populated data for {success_count} / {len(rows)} stocks...")

        log.info(f"Seeding completed successfully for {success_count} stocks!")

    finally:
        await conn.close()

if __name__ == "__main__":
    asyncio.run(run())
