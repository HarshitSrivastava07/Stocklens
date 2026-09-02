"""
NSE Bhavcopy Downloader + Processor
Downloads the daily NSE bhavcopy (end-of-day price CSV) and stores in DB.

NSE URL: https://nsearchives.nseindia.com/content/historical/EQUITIES/{year}/{month}/cm{DD}{MON}{YYYY}bhav.csv.zip

Run: python scripts/download_bhavcopy.py
Or called via scheduler at 16:30 IST
"""
import asyncio
import csv
import io
import logging
import os
import zipfile
from datetime import date, timedelta
from typing import Optional

import httpx
import asyncpg
from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger("bhavcopy")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

DB_URL = os.environ.get("DATABASE_SYNC_URL", "postgresql://postgres:password@localhost:5432/stocklens")

NSE_BHAVCOPY_URL = (
    "https://nsearchives.nseindia.com/content/historical/EQUITIES/{year}/{month}/cm{date_str}bhav.csv.zip"
)
NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept": "application/zip, text/csv",
    "Referer": "https://www.nseindia.com/",
}

MONTH_MAP = {
    1: "JAN", 2: "FEB", 3: "MAR", 4: "APR", 5: "MAY", 6: "JUN",
    7: "JUL", 8: "AUG", 9: "SEP", 10: "OCT", 11: "NOV", 12: "DEC",
}


def bhavcopy_url(trade_date: date) -> str:
    d = trade_date
    return NSE_BHAVCOPY_URL.format(
        year=d.year,
        month=MONTH_MAP[d.month],
        date_str=f"{d.day:02d}{MONTH_MAP[d.month]}{d.year}",
    )


async def download_bhavcopy(trade_date: date) -> Optional[list[dict]]:
    """Download and parse NSE bhavcopy CSV for a given date."""
    url = bhavcopy_url(trade_date)
    log.info(f"Downloading bhavcopy: {url}")

    async with httpx.AsyncClient(headers=NSE_HEADERS, timeout=60, follow_redirects=True) as client:
        try:
            resp = await client.get(url)
            if resp.status_code == 404:
                log.warning(f"Bhavcopy not found for {trade_date} (market holiday?)")
                return None
            resp.raise_for_status()
        except httpx.HTTPError as e:
            log.error(f"Download failed: {e}")
            return None

    # Unzip in memory
    try:
        zf = zipfile.ZipFile(io.BytesIO(resp.content))
        csv_name = zf.namelist()[0]
        csv_content = zf.read(csv_name).decode("utf-8", errors="replace")
    except Exception as e:
        log.error(f"Unzip failed: {e}")
        return None

    # Parse CSV
    rows = []
    reader = csv.DictReader(io.StringIO(csv_content))
    for row in reader:
        series = row.get("SERIES", "").strip()
        if series not in ("EQ", "BE", "BZ"):  # Only equity series
            continue

        def safe_float(key: str) -> Optional[float]:
            v = row.get(key, "").strip()
            try:
                return float(v) if v else None
            except ValueError:
                return None

        def safe_int(key: str) -> Optional[int]:
            v = row.get(key, "").strip().replace(",", "")
            try:
                return int(float(v)) if v else None
            except ValueError:
                return None

        rows.append({
            "trade_date": trade_date,
            "nse_symbol": row.get("SYMBOL", "").strip()[:30],
            "isin": row.get("ISIN", "").strip()[:20],
            "series": series,
            "open": safe_float("OPEN"),
            "high": safe_float("HIGH"),
            "low": safe_float("LOW"),
            "close": safe_float("CLOSE"),
            "prev_close": safe_float("PREVCLOSE"),
            "tottrdqty": safe_int("TOTTRDQTY"),
            "tottrdval": safe_float("TOTTRDVAL"),
            "totaltrades": safe_int("TOTALTRADES"),
        })

    log.info(f"Parsed {len(rows)} equity rows for {trade_date}")
    return rows


async def store_bhavcopy(rows: list[dict], conn) -> tuple[int, int]:
    """Upsert bhavcopy rows into nse_bhavcopy_staging and price_candles_daily."""
    inserted_staging, inserted_candles = 0, 0

    for row in rows:
        try:
            # Staging table
            await conn.execute("""
                INSERT INTO nse_bhavcopy_staging
                    (trade_date, nse_symbol, isin, series, open, high, low, close, prev_close, tottrdqty, tottrdval, totaltrades)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
                ON CONFLICT (trade_date, nse_symbol, series) DO NOTHING
            """, row["trade_date"], row["nse_symbol"], row["isin"], row["series"],
                row["open"], row["high"], row["low"], row["close"], row["prev_close"],
                row["tottrdqty"], row["tottrdval"], row["totaltrades"])
            inserted_staging += 1
        except Exception as e:
            log.debug(f"Staging insert skip: {e}")
            continue

        # Only write candle if stock exists in our DB
        try:
            stock_exists = await conn.fetchval(
                "SELECT 1 FROM stocks WHERE nse_symbol=$1", row["nse_symbol"]
            )
            if stock_exists and row["open"] and row["close"]:
                await conn.execute("""
                    INSERT INTO price_candles_daily
                        (nse_symbol, trade_date, open, high, low, close, volume)
                    VALUES ($1,$2,$3,$4,$5,$6,$7)
                    ON CONFLICT (nse_symbol, trade_date) DO UPDATE
                        SET open=$3, high=$4, low=$5, close=$6, volume=$7
                """, row["nse_symbol"], row["trade_date"],
                    row["open"], row["high"], row["low"], row["close"],
                    row["tottrdqty"] or 0)

                # Update realtime_quotes with latest close
                await conn.execute("""
                    INSERT INTO realtime_quotes (nse_symbol, ltp, close, updated_at)
                    VALUES ($1, $2, $2, NOW())
                    ON CONFLICT (nse_symbol) DO UPDATE
                        SET ltp = EXCLUDED.ltp, close = EXCLUDED.close, updated_at = NOW()
                """, row["nse_symbol"], row["close"])
                inserted_candles += 1
        except Exception as e:
            log.debug(f"Candle insert skip for {row['nse_symbol']}: {e}")

    return inserted_staging, inserted_candles


async def run(trade_date: Optional[date] = None, backfill_days: int = 0):
    """Download and store bhavcopy for a date (or backfill N days)."""
    conn = await asyncpg.connect(DB_URL)
    try:
        if backfill_days > 0:
            dates = [date.today() - timedelta(days=i) for i in range(backfill_days)]
            dates.reverse()
        else:
            dates = [trade_date or date.today()]

        for d in dates:
            # Skip weekends
            if d.weekday() >= 5:
                continue
            rows = await download_bhavcopy(d)
            if rows:
                st, ca = await store_bhavcopy(rows, conn)
                log.info(f"{d}: staging={st}, candles={ca}")
            await asyncio.sleep(2)  # polite delay

    finally:
        await conn.close()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="Date in YYYY-MM-DD format (default: today)")
    parser.add_argument("--backfill", type=int, default=0, help="Backfill N trading days")
    args = parser.parse_args()

    td = date.fromisoformat(args.date) if args.date else None
    asyncio.run(run(td, args.backfill))
