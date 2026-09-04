import logging
import random
import json
import csv
import io
import httpx
from datetime import date, timedelta
from fastapi import APIRouter, Depends, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from dependencies.db import get_db, AsyncSessionLocal

router = APIRouter()
log = logging.getLogger("ml_router")

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

async def seed_database_task():
    log.info("Starting optimized background database seeding...")
    
    async with AsyncSessionLocal() as db:
        try:
            # 1. Fetch all active stocks and their LTP
            result = await db.execute(text("""
                SELECT s.nse_symbol, s.company_name, s.exchange, s.currency, COALESCE(q.ltp, 100.0) as cmp
                FROM stocks s
                LEFT JOIN realtime_quotes q ON s.nse_symbol = q.nse_symbol
                WHERE s.is_active = TRUE
            """))
            stocks = result.fetchall()
            
            success_count = 0
            for stock in stocks:
                symbol = stock[0]
                company_name = stock[1]
                exchange = stock[2]
                currency = stock[3]
                cmp = float(stock[4])
                
                # A. Financial Results (last 4 quarters) in one batch
                fin_placeholders = []
                fin_params = {"symbol": symbol}
                for q in range(4):
                    period_end = date.today() - timedelta(days=90 * q)
                    growth = 1 + random.uniform(0.05, 0.20)
                    rev_base = cmp * random.uniform(80, 500)

                    fin_placeholders.append(
                        f"(gen_random_uuid(), :symbol, :period_end_{q}, 'Q', "
                        f":revenue_{q}, :ebitda_{q}, :pat_{q}, :eps_{q}, "
                        f"'DEV_SEED', TRUE, 'HIGH', FALSE)"
                    )
                    fin_params[f"period_end_{q}"] = period_end
                    fin_params[f"revenue_{q}"]    = round(rev_base * (growth ** q), 2)
                    fin_params[f"ebitda_{q}"]     = round(rev_base * 0.22 * (growth ** q), 2)
                    fin_params[f"pat_{q}"]        = round(rev_base * 0.12 * (growth ** q), 2)
                    fin_params[f"eps_{q}"]        = round(rev_base * 0.12 / random.uniform(50, 500), 2)

                fin_query = (
                    "INSERT INTO financial_results "
                    "(id, nse_symbol, period_end, period_type, revenue, ebitda, pat, eps, "
                    "data_source, is_verified, confidence_level, has_exceptional) "
                    f"VALUES {', '.join(fin_placeholders)} "
                    "ON CONFLICT (nse_symbol, period_type, period_end) DO NOTHING"
                )
                await db.execute(text(fin_query), fin_params)

                # B. Financial Ratios
                # Schema: UniqueConstraint(nse_symbol, as_of_date), NOT NULL: as_of_date, computed_ok
                # Use DELETE + INSERT for today to avoid broken ON CONFLICT (nse_symbol) issue
                await db.execute(text("""
                    DELETE FROM financial_ratios
                    WHERE nse_symbol = :symbol AND as_of_date = CURRENT_DATE
                """), {"symbol": symbol})
                await db.execute(text("""
                    INSERT INTO financial_ratios
                        (id, nse_symbol, as_of_date, computed_ok,
                         pe, pb, ps, ev_ebitda, roe, roce, roa,
                         ebitda_margin, net_margin, revenue_cagr_3y, pat_cagr_3y,
                         debt_equity, interest_coverage, cfo_pat,
                         data_completeness, error_fields)
                    VALUES
                        (gen_random_uuid(), :symbol, CURRENT_DATE, TRUE,
                         :pe, :pb, :ps, :ev_ebitda, :roe, :roce, :roa,
                         :ebitda_margin, :net_margin, :revenue_cagr_3y, :pat_cagr_3y,
                         :debt_equity, :interest_coverage, :cfo_pat,
                         :data_completeness, '[]'::jsonb)
                """), {
                    "symbol": symbol,
                    "pe": round(random.uniform(12, 45), 1),
                    "pb": round(random.uniform(1.5, 8), 2),
                    "ps": round(random.uniform(1, 10), 2),
                    "ev_ebitda": round(random.uniform(8, 25), 1),
                    "roe": round(random.uniform(0.10, 0.35), 4),
                    "roce": round(random.uniform(0.12, 0.28), 4),
                    "roa": round(random.uniform(0.05, 0.15), 4),
                    "ebitda_margin": round(random.uniform(0.12, 0.35), 4),
                    "net_margin": round(random.uniform(0.06, 0.20), 4),
                    "revenue_cagr_3y": round(random.uniform(0.08, 0.22), 4),
                    "pat_cagr_3y": round(random.uniform(0.06, 0.25), 4),
                    "debt_equity": round(random.uniform(0, 1.5), 2),
                    "interest_coverage": round(random.uniform(3, 20), 1),
                    "cfo_pat": round(random.uniform(0.7, 1.5), 2),
                    "data_completeness": round(random.uniform(0.65, 0.95), 2),
                })

                # C. Intrinsic Values
                # cmp from COALESCE(q.ltp, 100.0) — real price if Yahoo data exists in DB
                signal_color = random.choice(SIGNALS)
                iv_base    = cmp * random.uniform(0.7, 1.6)
                iv_bear    = iv_base * 0.75
                iv_bull    = iv_base * 1.3
                iv_blended = iv_bear * 0.25 + iv_base * 0.50 + iv_bull * 0.25
                upside     = ((iv_blended - cmp) / cmp) * 100 if cmp > 0 else 0
                mos        = ((iv_blended - cmp) / iv_blended) if iv_blended > 0 else 0
                # Only store real upside/MoS if price is meaningful (not the 100.0 fallback)
                has_real_price = not (99.0 <= cmp <= 101.0)

                await db.execute(text("""
                    INSERT INTO intrinsic_values
                        (nse_symbol, cmp, iv_bear, iv_base, iv_bull, iv_blended,
                         upside_pct, margin_of_safety, primary_model, valuation_confidence)
                    VALUES (:symbol, :cmp, :iv_bear, :iv_base, :iv_bull, :iv_blended,
                            :upside_pct, :mos, 'DCF', :valuation_confidence)
                    ON CONFLICT (nse_symbol) DO UPDATE
                        SET cmp=:cmp, iv_bear=:iv_bear, iv_base=:iv_base,
                            iv_bull=:iv_bull, iv_blended=:iv_blended,
                            upside_pct=:upside_pct, margin_of_safety=:mos,
                            valuation_confidence=:valuation_confidence,
                            updated_at=NOW()
                """), {
                    "symbol":             symbol,
                    "cmp":                round(cmp, 2) if has_real_price else None,
                    "iv_bear":            round(iv_bear, 2),
                    "iv_base":            round(iv_base, 2),
                    "iv_bull":            round(iv_bull, 2),
                    "iv_blended":         round(iv_blended, 2),
                    "upside_pct":         round(upside, 4) if has_real_price else None,
                    "mos":                round(mos, 4)    if has_real_price else None,
                    "valuation_confidence": random.choice(["HIGH", "MEDIUM", "MEDIUM", "LOW"]),
                })

                # D. ML Scores
                risk = random.randint(15, 75)
                cluster = random.choice(CLUSTER_LABELS)
                await db.execute(text("""
                    INSERT INTO ml_scores
                        (nse_symbol, fundamental_score, growth_outlook_score, risk_score, risk_level,
                         risk_drivers, confidence_factors, valuation_confidence)
                    VALUES (:symbol, :fundamental_score, :growth_outlook_score, :risk_score, :risk_level,
                            :risk_drivers::jsonb, '[]'::jsonb, :valuation_confidence)
                    ON CONFLICT (nse_symbol) DO UPDATE
                        SET fundamental_score=:fundamental_score, growth_outlook_score=:growth_outlook_score,
                            risk_score=:risk_score, risk_level=:risk_level, risk_drivers=:risk_drivers::jsonb,
                            valuation_confidence=:valuation_confidence, updated_at=NOW()
                """), {
                    "symbol": symbol,
                    "fundamental_score": random.randint(40, 92),
                    "growth_outlook_score": random.randint(35, 88),
                    "risk_score": risk,
                    "risk_level": "HIGH" if risk > 65 else "MEDIUM" if risk > 40 else "LOW",
                    "risk_drivers": json.dumps(["High debt", "Margin pressure"] if risk > 60 else ["Cyclical"]),
                    "valuation_confidence": "HIGH" if upside > 25 else "MEDIUM" if upside > 10 else "LOW",
                })

                # E. ML Cluster Results
                # Schema: PK=id (uuid, no server_default), NOT NULL: features_used, feature_values
                await db.execute(text("""
                    INSERT INTO ml_cluster_results
                        (id, nse_symbol, run_date, cluster_label, cluster_id,
                         features_used, feature_values, algorithm)
                    VALUES (gen_random_uuid(), :symbol, CURRENT_DATE,
                            :cluster_label, :cluster_id,
                            '[]'::jsonb, '{}'::jsonb, 'KMEANS')
                    ON CONFLICT (nse_symbol, run_date) DO UPDATE
                        SET cluster_label = EXCLUDED.cluster_label,
                            cluster_id    = EXCLUDED.cluster_id
                """), {
                    "symbol":       symbol,
                    "cluster_label": cluster,
                    "cluster_id":   random.randint(0, 8),
                })

                # F. Signals
                SIGNAL_VALUES = {
                    "GREEN": "POTENTIALLY_UNDERVALUED",
                    "YELLOW": "REVIEW_REQUIRED",
                    "RED": "AVOID_OVERVALUED",
                    "GREY": "INSUFFICIENT_DATA",
                }
                await db.execute(text("""
                    INSERT INTO signals
                        (nse_symbol, signal, signal_color, signal_label, main_reason,
                         conditions, blocking_flags, data_freshness)
                    VALUES (:symbol, :signal, :signal_color, :signal_label, :main_reason,
                            :conditions::jsonb, :blocking_flags::jsonb, 'MOCK')
                    ON CONFLICT (nse_symbol) DO UPDATE
                        SET signal=:signal, signal_color=:signal_color, signal_label=:signal_label,
                            main_reason=:main_reason, conditions=:conditions::jsonb,
                            blocking_flags=:blocking_flags::jsonb, data_freshness='MOCK', updated_at=NOW()
                """), {
                    "symbol": symbol,
                    "signal": SIGNAL_VALUES[signal_color],
                    "signal_color": signal_color,
                    "signal_label": SIGNAL_LABELS[signal_color],
                    "main_reason": f"Blended IV {currency} {iv_blended:.2f} vs CMP {currency} {cmp:.2f} ({upside:+.1f}%)",
                    "conditions": json.dumps({"upside_ok": upside > 15, "risk_ok": risk < 60, "fundamental_ok": True}),
                    "blocking_flags": json.dumps([] if signal_color == "GREEN" else ["Low margin of safety"] if signal_color == "YELLOW" else ["Overvalued"]),
                })

                # G. Price Candles (last 90 trading days) in one bulk batch
                candle_placeholders = []
                candle_params = {"symbol": symbol}
                price = cmp * 0.85
                candle_count = 0
                for i in range(90):
                    trade_date = date.today() - timedelta(days=90 - i)
                    if trade_date.weekday() >= 5:
                        continue
                    daily_return = random.gauss(0.0005, 0.015)
                    price *= (1 + daily_return)
                    day_high = price * random.uniform(1.002, 1.02)
                    day_low = price * random.uniform(0.98, 0.998)
                    day_open = random.uniform(day_low, day_high)
                    
                    candle_placeholders.append(f"(gen_random_uuid(), :symbol, :trade_date_{i}, :open_{i}, :high_{i}, :low_{i}, :close_{i}, :volume_{i})")
                    candle_params[f"trade_date_{i}"] = trade_date
                    candle_params[f"open_{i}"] = round(day_open, 2)
                    candle_params[f"high_{i}"] = round(day_high, 2)
                    candle_params[f"low_{i}"] = round(day_low, 2)
                    candle_params[f"close_{i}"] = round(price, 2)
                    candle_params[f"volume_{i}"] = random.randint(50000, 2000000)
                    candle_count += 1
                
                if candle_placeholders:
                    candle_query = f"INSERT INTO price_candles_daily (id, nse_symbol, trade_date, open, high, low, close, volume) VALUES {', '.join(candle_placeholders)} ON CONFLICT (nse_symbol, trade_date) DO NOTHING"
                    await db.execute(text(candle_query), candle_params)

                success_count += 1
                # Commit every 20 stocks to keep it chunked and robust
                if success_count % 20 == 0:
                    await db.commit()
                    log.info(f"Background seeding progress: {success_count} stocks committed...")
            
            await db.commit()
            log.info(f"Background seeding completed successfully for {success_count} stocks.")
        except Exception as e:
            log.error(f"Error in background seed task: {e}")
            await db.rollback()
            try:
                with open("seed_error.txt", "w") as f:
                    import traceback
                    f.write(traceback.format_exc())
            except:
                pass
            return {"status": "error", "message": str(e)}

@router.get("/unlock")
async def unlock_db(db: AsyncSession = Depends(get_db)):
    log.info("Terminating hanging PG backend queries to release table locks...")
    try:
        # Terminate other sessions that are active or idle in transaction
        await db.execute(text("""
            SELECT pg_terminate_backend(pid)
            FROM pg_stat_activity
            WHERE pid <> pg_backend_pid()
              AND (state = 'idle in transaction' OR query LIKE '%INSERT%' OR query LIKE '%financial%')
        """))
        await db.commit()
        return {"status": "success", "message": "Hanging database backends terminated and table locks released."}
    except Exception as e:
        await db.rollback()
        return {"status": "error", "message": str(e)}

@router.get("/run-inference", status_code=202)
async def run_inference(background_tasks: BackgroundTasks):
    log.info("Queuing background database seeding task...")
    background_tasks.add_task(seed_database_task)
    return {"status": "queued", "message": "Database calculation and mock financials population job triggered in background"}


async def import_all_nse_task():
    log.info("Starting download of full NSE equity list from official archives...")
    NSE_EQUITY_LIST_URL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://www.nseindia.com/",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    try:
        async with httpx.AsyncClient(headers=headers, timeout=30, follow_redirects=True) as client:
            await client.get("https://www.nseindia.com/", timeout=10)
            res = await client.get(NSE_EQUITY_LIST_URL)
            res.raise_for_status()
            content = res.text

        rows = list(csv.DictReader(io.StringIO(content)))
        log.info(f"Downloaded {len(rows)} NSE equity records. Bulk inserting...")

        async with AsyncSessionLocal() as db:
            inserted = 0
            for row in rows:
                symbol = (row.get("SYMBOL") or row.get("Symbol") or "").strip().upper()
                name = (row.get("NAME OF COMPANY") or row.get("Company Name") or "").strip()
                isin = (row.get("ISIN NUMBER") or row.get("ISIN") or "").strip()
                series = (row.get("SERIES") or row.get("Series") or "EQ").strip()
                if not symbol or not name or series not in ("EQ", "BE", "BZ", "SM", "ST"):
                    continue
                name = "".join(c for c in name if c.isalnum() or c in " .,&()-/").strip()[:200]
                symbol = "".join(c for c in symbol if c.isalnum() or c in ".-&")[:30]

                await db.execute(text("""
                    INSERT INTO stocks (id, nse_symbol, company_name, isin, instrument_type, face_value, lot_size, is_nifty50, listing_status, is_active, exchange, currency, data_source)
                    VALUES (gen_random_uuid(), :symbol, :name, :isin, 'EQ', 10.0, 1, FALSE, 'ACTIVE', TRUE, 'NSE', 'INR', 'NSE_EQUITY_LIST')
                    ON CONFLICT (nse_symbol) DO NOTHING
                """), {
                    "symbol": symbol,
                    "name": name,
                    "isin": isin[:12] if isin else None
                })
                inserted += 1
                if inserted % 200 == 0:
                    await db.commit()

            await db.commit()
            log.info(f"Successfully imported {inserted} NSE symbols into stocks table.")
    except Exception as e:
        log.warning(f"Failed to fetch live NSE list (bot blocked). Falling back to mock generator: {e}")
        try:
            async with AsyncSessionLocal() as db:
                inserted = 0
                base_names = ["TECH", "BANK", "STEEL", "POWER", "INFRA", "PHARMA", "MOTORS", "CHEM", "ENERGY", "FIN", "FOOD", "RETAIL"]
                for i in range(1, 2701):
                    prefix = random.choice(base_names)
                    symbol = f"{prefix}{i:04d}"
                    name = f"{prefix} India Enterprises {i}"
                    await db.execute(text("""
                        INSERT INTO stocks (id, nse_symbol, company_name, isin, instrument_type, face_value, lot_size, is_nifty50, listing_status, is_active, exchange, currency, data_source)
                        VALUES (gen_random_uuid(), :symbol, :name, 'INE000000000', 'EQ', 10.0, 1, FALSE, 'ACTIVE', TRUE, 'NSE', 'INR', 'MOCK_GENERATOR')
                        ON CONFLICT (nse_symbol) DO NOTHING
                    """), {"symbol": symbol, "name": name})
                    inserted += 1
                    if inserted % 200 == 0:
                        await db.commit()
                await db.commit()
                log.info(f"Successfully generated {inserted} mock NSE symbols for development.")
        except Exception as mock_e:
            log.error(f"Mock generator also failed: {mock_e}")
            try:
                with open("import_error.txt", "w") as f:
                    import traceback
                    f.write(traceback.format_exc())
            except:
                pass



@router.get("/import-all-nse", status_code=202)
async def import_all_nse(background_tasks: BackgroundTasks):
    log.info("Queuing background task to import all ~2,000+ NSE stocks...")
    background_tasks.add_task(import_all_nse_task)
    return {"status": "queued", "message": "Importing all ~2,000+ registered NSE equities from NSE archives in background."}



@router.get("/clusters")
async def get_clusters():
    return {"clusters": [
        "QUALITY_COMPOUNDER", "DEEP_VALUE", "GARP", "CYCLICAL_RECOVERY",
        "HIGH_GROWTH_EXPENSIVE", "VALUE_TRAP", "MOMENTUM_TRAP", "DISTRESSED", "LOW_LIQUIDITY"
    ]}

@router.get("/clusters/{label}")
async def get_cluster(label: str):
    return {"label": label, "stocks": []}

@router.get("/scores/{symbol}")
async def get_scores(symbol: str):
    return {"symbol": symbol.upper(), "scores": None, "message": "ML scores computed after nightly run"}
