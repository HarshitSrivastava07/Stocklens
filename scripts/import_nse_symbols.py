#!/usr/bin/env python3
"""
NSE Stock Universe Importer
Downloads NSE equity list from official NSE website and seeds the database.

Usage:
    python import_nse_symbols.py
    python import_nse_symbols.py --source bhavcopy  # from local bhavcopy CSV
    python import_nse_symbols.py --file equity_list.csv
"""
import argparse
import asyncio
import csv
import io
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

import asyncpg
import httpx
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("import_nse_symbols")

# NSE equity list URLs (public, no auth required)
NSE_EQUITY_LIST_URL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
NSE_FO_LIST_URL = "https://nsearchives.nseindia.com/content/fo/fo_mktlots.csv"

# ISIN master (sector classification via NSE)
NSE_ISIN_URL = "https://nsearchives.nseindia.com/content/equities/List_of_Securities.csv"

# Headers required by NSE website
NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://www.nseindia.com/",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# Sector classification mapping from NSE industry name → our sectors
INDUSTRY_TO_SECTOR = {
    "Banks": ("Financials", "Banks"),
    "Finance": ("Financials", "NBFCs"),
    "Insurance": ("Financials", "Insurance"),
    "Software & It Services": ("Technology", "IT Services"),
    "Software Products": ("Technology", "Software Products"),
    "Telecommunications": ("Technology", "Telecom"),
    "Consumer Goods": ("Consumer", "FMCG"),
    "Retailing": ("Consumer", "Retail"),
    "Textiles": ("Consumer", "Textiles"),
    "Pharmaceuticals": ("Healthcare", "Pharma"),
    "Healthcare & Medical Services": ("Healthcare", "Hospitals"),
    "Automobiles": ("Auto", "Auto"),
    "Auto Components": ("Auto", "Auto Ancillaries"),
    "Capital Goods": ("Industrials", "Capital Goods"),
    "Infrastructure": ("Industrials", "Infrastructure"),
    "Metals & Mining": ("Materials", "Metals"),
    "Cement & Cement Products": ("Materials", "Cement"),
    "Chemicals": ("Materials", "Chemicals"),
    "Oil Gas & Consumable Fuels": ("Energy", "Oil & Gas"),
    "Power": ("Energy", "Power"),
    "Realty": ("Real Estate", "Realty"),
    "Media & Entertainment": ("Media", "Media"),
    "Construction": ("Industrials", "Construction"),
    "Agricultural Products": ("Agriculture", "Agriculture"),
}

NIFTY50_SYMBOLS = {
    "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "INFY", "BHARTIARTL", "SBIN",
    "HINDUNILVR", "ITC", "LT", "KOTAKBANK", "AXISBANK", "BAJFINANCE", "ASIANPAINT",
    "MARUTI", "TITAN", "SUNPHARMA", "ULTRACEMCO", "M&M", "TECHM", "WIPRO",
    "HCLTECH", "ONGC", "COALINDIA", "NTPC", "JSWSTEEL", "POWERGRID", "BAJAJFINSV",
    "ADANIENT", "ADANIPORTS", "TATAMOTORS", "NESTLEIND", "TATASTEEL", "BPCL",
    "DIVISLAB", "DRREDDY", "CIPLA", "GRASIM", "HINDALCO", "APOLLOHOSP",
    "TATACONSUM", "BRITANNIA", "EICHERMOT", "HERO MOTOCORP", "HDFCLIFE",
    "INDUSINDBK", "SBILIFE", "SHRIRAMFIN", "BAJAJ-AUTO", "BEL",
}


async def fetch_nse_equity_list() -> list[dict]:
    """Download NSE equity list CSV from official website."""
    log.info(f"Downloading equity list from NSE: {NSE_EQUITY_LIST_URL}")
    async with httpx.AsyncClient(headers=NSE_HEADERS, timeout=30, follow_redirects=True) as client:
        # NSE requires session cookie — first hit the main page
        await client.get("https://www.nseindia.com/", timeout=10)
        response = await client.get(NSE_EQUITY_LIST_URL)
        response.raise_for_status()
        content = response.text

    rows = []
    reader = csv.DictReader(io.StringIO(content))
    for row in reader:
        rows.append(row)

    log.info(f"Downloaded {len(rows)} equity records from NSE")
    return rows


def parse_equity_row(row: dict) -> dict | None:
    """Parse a raw NSE equity CSV row into our schema."""
    try:
        symbol = (row.get("SYMBOL") or row.get("Symbol") or "").strip().upper()
        name = (row.get("NAME OF COMPANY") or row.get("Company Name") or row.get("CompanyName") or "").strip()
        isin = (row.get("ISIN NUMBER") or row.get("ISIN") or "").strip()
        series = (row.get("SERIES") or row.get("Series") or "EQ").strip()
        face_value = row.get("FACE VALUE") or row.get("FaceValue") or "1"

        if not symbol or not name:
            return None

        # Only process equity series (EQ, BE, BZ, SM, ST)
        if series not in ("EQ", "BE", "BZ", "SM", "ST"):
            return None

        # Sanitize name — allow only safe characters
        name = "".join(c for c in name if c.isalnum() or c in " .,&()-/").strip()[:200]
        symbol = "".join(c for c in symbol if c.isalnum() or c in ".-&")[:30]

        try:
            fv = float(str(face_value).replace(",", "").strip())
        except ValueError:
            fv = 1.0

        return {
            "nse_symbol": symbol,
            "company_name": name,
            "isin": isin[:12] if isin else None,
            "instrument_type": "EQ",
            "face_value": fv,
            "is_nifty50": symbol in NIFTY50_SYMBOLS,
            "listing_status": "ACTIVE",
            "data_source": "NSE_EQUITY_LIST",
        }
    except Exception as e:
        log.debug(f"Parse error: {e} for row: {row}")
        return None


async def get_or_create_sector(conn, macro_sector: str, sector: str) -> str:
    """Get or create sector classification, return UUID."""
    row = await conn.fetchrow(
        "SELECT id FROM sector_classification WHERE macro_sector=$1 AND sector=$2",
        macro_sector, sector
    )
    if row:
        return str(row["id"])
    result = await conn.fetchrow(
        "INSERT INTO sector_classification (macro_sector, sector) VALUES ($1, $2) RETURNING id",
        macro_sector, sector
    )
    return str(result["id"])


async def import_to_db(stocks: list[dict], conn):
    """Upsert all stocks into the database."""
    inserted, updated, skipped = 0, 0, 0

    for stock in stocks:
        try:
            existing = await conn.fetchrow(
                "SELECT id FROM stocks WHERE nse_symbol=$1", stock["nse_symbol"]
            )
            if existing:
                await conn.execute(
                    """UPDATE stocks SET company_name=$1, isin=$2, face_value=$3,
                       is_nifty50=$4, updated_at=NOW() WHERE nse_symbol=$5""",
                    stock["company_name"], stock.get("isin"), stock.get("face_value"),
                    stock["is_nifty50"], stock["nse_symbol"]
                )
                updated += 1
            else:
                await conn.execute(
                    """INSERT INTO stocks (nse_symbol, company_name, isin, instrument_type,
                       face_value, is_nifty50, listing_status, data_source)
                       VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                       ON CONFLICT (nse_symbol) DO NOTHING""",
                    stock["nse_symbol"], stock["company_name"], stock.get("isin"),
                    stock["instrument_type"], stock.get("face_value"),
                    stock["is_nifty50"], stock["listing_status"], stock["data_source"]
                )
                inserted += 1

        except Exception as e:
            log.warning(f"DB error for {stock['nse_symbol']}: {e}")
            skipped += 1

    return inserted, updated, skipped


async def main():
    parser = argparse.ArgumentParser(description="NSE Symbol Importer")
    parser.add_argument("--file", help="Local CSV file path instead of downloading")
    parser.add_argument("--source", choices=["nse", "bhavcopy", "file"], default="nse")
    args = parser.parse_args()

    db_url = os.environ.get("DATABASE_SYNC_URL", "postgresql://postgres:password@localhost:5432/stocklens")
    # Convert to asyncpg format
    db_url = db_url.replace("postgresql://", "").replace("postgresql+psycopg2://", "")

    log.info("Connecting to database...")
    try:
        conn = await asyncpg.connect(f"postgresql://{db_url}")
    except Exception as e:
        log.error(f"DB connection failed: {e}")
        sys.exit(1)

    try:
        # Load equity data
        if args.file:
            log.info(f"Reading from file: {args.file}")
            with open(args.file, "r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                raw_rows = list(reader)
        else:
            raw_rows = await fetch_nse_equity_list()

        log.info(f"Parsing {len(raw_rows)} rows...")
        stocks = [parse_equity_row(r) for r in raw_rows]
        stocks = [s for s in stocks if s is not None]
        log.info(f"Valid stocks: {len(stocks)}")

        if not stocks:
            log.error("No valid stocks parsed!")
            sys.exit(1)

        log.info("Importing to database...")
        inserted, updated, skipped = await import_to_db(stocks, conn)
        total = await conn.fetchval("SELECT COUNT(*) FROM stocks WHERE listing_status='ACTIVE'")

        log.info(f"""
╔══════════════════════════════════════════╗
║         NSE Import Complete!             ║
╠══════════════════════════════════════════╣
║  Inserted:  {inserted:<29}║
║  Updated:   {updated:<29}║
║  Skipped:   {skipped:<29}║
║  Total DB:  {total:<29}║
╚══════════════════════════════════════════╝
""")

    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
