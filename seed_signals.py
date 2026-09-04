"""
StockLens — Signal & Score Seeder (mock financials only)
=========================================================
Populates: intrinsic_values, ml_scores, signals

IMPORTANT:
  - Does NOT touch realtime_quotes — prices come from fetch_live_prices.py
  - Does NOT write CMP/ltp — those come from Yahoo Finance only
  - intrinsic_values.cmp is LEFT AS NULL here; fetch_live_prices.py
    fills it from Yahoo Finance

Schema confirmed from db_models.py (SQLAlchemy 2.0):
  - All 4 tables: PK = nse_symbol (not id)
  - Python-side `default=` ≠ DB server_default → must supply in raw SQL
  - NOT NULL without server_default:
      ml_scores: risk_drivers (JSONB []), confidence_factors (JSONB [])
      signals:   signal, signal_color, signal_label,
                 conditions (JSONB {}), blocking_flags (JSONB []), data_freshness

Run from stocklens root:
    python seed_signals.py
    (run fetch_live_prices.py AFTER this to get real prices)
"""
import os
import random
import sys

try:
    import psycopg2
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "psycopg2-binary"])
    import psycopg2

DB_URL = os.environ.get(
    "DATABASE_SYNC_URL", "postgresql://postgres:password@localhost:5432/stocklens"
)

SIGNAL_LABELS = {
    "GREEN":  "Potentially Undervalued",
    "YELLOW": "Review Required / Watch",
    "RED":    "Possibly Overvalued / Risk",
    "GREY":   "Insufficient Data",
}


def main():
    print("Connecting to database...")
    conn = psycopg2.connect(DB_URL)
    conn.autocommit = True
    cur = conn.cursor()

    cur.execute(
        "SELECT nse_symbol FROM stocks WHERE is_active = TRUE ORDER BY nse_symbol"
    )
    stocks = [r[0] for r in cur.fetchall()]
    print(f"Found {len(stocks)} active stocks. Seeding signals/scores (no prices)...")

    processed = 0
    for symbol in stocks:
        try:
            r = random.Random(hash(symbol))

            # ── ML Scores (PK = nse_symbol) ─────────────────────────
            # NOT NULL (no server_default): risk_drivers, confidence_factors
            risk_score  = int(r.uniform(10, 85))
            risk_level  = "LOW" if risk_score <= 33 else "MEDIUM" if risk_score <= 66 else "HIGH"
            fund_score  = int(r.uniform(20, 90))
            growth_score = int(r.uniform(15, 95))
            confidence = r.choice(["HIGH", "HIGH", "MEDIUM", "MEDIUM", "MEDIUM", "LOW"])

            cur.execute("DELETE FROM ml_scores WHERE nse_symbol = %s", (symbol,))
            cur.execute(
                """
                INSERT INTO ml_scores
                    (nse_symbol, risk_score, risk_level, valuation_confidence,
                     fundamental_score, growth_outlook_score,
                     risk_drivers, confidence_factors)
                VALUES (%s, %s, %s, %s, %s, %s, '[]'::jsonb, '[]'::jsonb)
                """,
                (symbol, risk_score, risk_level, confidence, fund_score, growth_score),
            )

            # ── Intrinsic Value (PK = nse_symbol) ────────────────────
            # NOTE: cmp is intentionally left NULL here.
            #       Run fetch_live_prices.py to fill it with real market price.
            #       Upside % and MoS are also left NULL until real CMP arrives.
            iv_model = r.choice(["DCF", "PE_RELATIVE", "EV_EBITDA", "REVERSE_DCF"])

            cur.execute("DELETE FROM intrinsic_values WHERE nse_symbol = %s", (symbol,))
            cur.execute(
                """
                INSERT INTO intrinsic_values
                    (nse_symbol, primary_model, valuation_confidence)
                VALUES (%s, %s, %s)
                """,
                (symbol, iv_model, confidence),
            )

            # ── Signal (PK = nse_symbol) ─────────────────────────────
            # Without real CMP we can only assign GREY (insufficient data)
            # fetch_live_prices.py will recompute signals with real prices later
            cur.execute("DELETE FROM signals WHERE nse_symbol = %s", (symbol,))
            cur.execute(
                """
                INSERT INTO signals
                    (nse_symbol, signal, signal_color, signal_label, main_reason,
                     fundamental_score, growth_score, risk_score, valuation_confidence,
                     conditions, blocking_flags, data_freshness)
                VALUES (%s, 'INSUFFICIENT_DATA', 'GREY', %s, %s, %s, %s, %s, %s,
                        '{}'::jsonb, '["No live price data"]'::jsonb, 'MOCK')
                """,
                (
                    symbol,
                    SIGNAL_LABELS["GREY"],
                    "Awaiting live price data from market feed",
                    fund_score, growth_score, risk_score, confidence,
                ),
            )

            processed += 1
            if processed % 50 == 0:
                print(f"  {processed}/{len(stocks)} done...")

        except Exception as e:
            print(f"  ERROR on {symbol}: {e}")
            continue

    cur.close()
    conn.close()
    print(f"\n✅ Seeded {processed}/{len(stocks)} stocks with scores and GREY signals.")
    print("\n⚠  Prices are NOT set yet. Run next:")
    print("   python fetch_live_prices.py")
    print("\nThis will fetch real market prices from Yahoo Finance and update signals.")


if __name__ == "__main__":
    main()
