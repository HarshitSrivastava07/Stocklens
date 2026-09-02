"""
BSE XML Fundamentals Fetcher
Downloads quarterly financial results from BSE India's public XML feeds.

BSE provides these free endpoints:
- Result XML: https://api.bseindia.com/BseIndiaAPI/api/AnnualResult/w?Scripcode={bse_code}&Type=C
- Corporate actions, board results

This script runs automatically at 17:00 IST daily (via scheduler_service.py).
Also callable manually: python scripts/fetch_bse_data.py --symbol RELIANCE
"""
import asyncio
import csv
import logging
import os
import re
import sys
from datetime import date, datetime
from typing import Optional
import xml.etree.ElementTree as ET

import httpx
import asyncpg
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("bse_fetcher")

DB_URL = os.environ.get("DATABASE_SYNC_URL", "postgresql://postgres:password@localhost:5432/stocklens")

BSE_RESULT_URL = "https://api.bseindia.com/BseIndiaAPI/api/AnnualResult/w"
BSE_SEARCH_URL = "https://api.bseindia.com/BseIndiaAPI/api/ScripSearch/w"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept": "application/json, text/xml",
    "Referer": "https://www.bseindia.com/",
}


async def get_bse_code(symbol: str, conn) -> Optional[str]:
    """Look up BSE code from stocks table (populated during NSE import)."""
    row = await conn.fetchrow(
        "SELECT bse_code FROM stocks WHERE nse_symbol = $1",
        symbol
    )
    return str(row["bse_code"]) if row and row["bse_code"] else None


async def fetch_bse_results_xml(bse_code: str) -> Optional[ET.Element]:
    """Fetch quarterly/annual result XML from BSE."""
    url = f"{BSE_RESULT_URL}?Scripcode={bse_code}&Type=C"
    async with httpx.AsyncClient(headers=HEADERS, timeout=30) as client:
        try:
            resp = await client.get(url)
            if resp.status_code != 200:
                log.warning(f"BSE API returned {resp.status_code} for {bse_code}")
                return None
            # BSE returns JSON with XML embedded or pure XML
            content_type = resp.headers.get("content-type", "")
            if "json" in content_type:
                data = resp.json()
                xml_str = data.get("FinancialResults", data.get("Table", ""))
                if not xml_str:
                    return None
                return ET.fromstring(xml_str)
            else:
                return ET.fromstring(resp.text)
        except Exception as e:
            log.warning(f"BSE XML fetch failed for {bse_code}: {e}")
            return None


def parse_bse_xml(root: ET.Element, nse_symbol: str) -> list[dict]:
    """Parse BSE financial result XML into financial_results rows."""
    rows = []

    # Try multiple XML schemas (BSE changes format occasionally)
    for item in root.iter("FinancialResult"):
        row = _parse_result_item(item, nse_symbol)
        if row:
            rows.append(row)

    if not rows:
        for item in root.iter("Record"):
            row = _parse_result_item(item, nse_symbol)
            if row:
                rows.append(row)

    return rows


def _parse_result_item(item: ET.Element, nse_symbol: str) -> Optional[dict]:
    """Parse a single BSE result XML item."""
    def txt(tag: str) -> Optional[str]:
        el = item.find(tag)
        return el.text.strip() if el is not None and el.text else None

    def num(tag: str) -> Optional[float]:
        v = txt(tag)
        if v is None:
            return None
        v = v.replace(",", "").replace("(", "-").replace(")", "")
        try:
            return float(v)
        except ValueError:
            return None

    period_text = txt("QuarterEnded") or txt("YearEnded") or txt("PeriodEnded")
    if not period_text:
        return None

    # Parse period end date
    try:
        period_end = datetime.strptime(period_text, "%d/%m/%Y").date()
    except ValueError:
        try:
            period_end = datetime.strptime(period_text, "%Y-%m-%d").date()
        except ValueError:
            return None

    # Determine period type
    period_type = "Q" if txt("QuarterEnded") else "A"

    revenue = num("TotalIncome") or num("Revenue") or num("NetSales")
    ebitda = num("EBITDA") or num("OperatingProfit")
    pat = num("ProfitAfterTax") or num("NetProfit")
    eps = num("BasicEPS") or num("DilutedEPS")

    if revenue is None and pat is None:
        return None  # Skip empty rows

    return {
        "nse_symbol": nse_symbol,
        "period_end": period_end,
        "period_type": period_type,
        "revenue": revenue,
        "ebitda": ebitda,
        "pat": pat,
        "eps": eps,
        "data_source": "BSE_XML",
        "is_verified": False,
        "confidence_level": "MEDIUM",
    }


async def upsert_results(rows: list[dict], conn) -> tuple[int, int]:
    inserted, updated = 0, 0
    for row in rows:
        existing = await conn.fetchrow(
            "SELECT id FROM financial_results WHERE nse_symbol=$1 AND period_end=$2 AND period_type=$3",
            row["nse_symbol"], row["period_end"], row["period_type"]
        )
        if existing:
            fields = {k: v for k, v in row.items() if k not in ("nse_symbol", "period_end", "period_type") and v is not None}
            if fields:
                set_clause = ", ".join(f"{k}=${i+1}" for i, k in enumerate(fields))
                vals = list(fields.values()) + [row["nse_symbol"], row["period_end"], row["period_type"]]
                try:
                    await conn.execute(
                        f"UPDATE financial_results SET {set_clause} WHERE nse_symbol=${len(vals)-2} AND period_end=${len(vals)-1} AND period_type=${len(vals)}",
                        *vals
                    )
                    updated += 1
                except Exception as e:
                    log.warning(f"Update failed: {e}")
        else:
            cols = ", ".join(row.keys())
            placeholders = ", ".join(f"${i+1}" for i in range(len(row)))
            try:
                await conn.execute(
                    f"INSERT INTO financial_results ({cols}) VALUES ({placeholders}) ON CONFLICT DO NOTHING",
                    *row.values()
                )
                inserted += 1
            except Exception as e:
                log.warning(f"Insert failed: {e}")
    return inserted, updated


async def process_symbol(symbol: str, conn) -> dict:
    """Fetch and store BSE results for one symbol."""
    bse_code = await get_bse_code(symbol, conn)
    if not bse_code:
        return {"symbol": symbol, "status": "skipped", "reason": "No BSE code"}

    xml_root = await fetch_bse_results_xml(bse_code)
    if xml_root is None:
        return {"symbol": symbol, "status": "failed", "reason": "XML fetch failed"}

    rows = parse_bse_xml(xml_root, symbol)
    if not rows:
        return {"symbol": symbol, "status": "empty", "reason": "No parseable data"}

    ins, upd = await upsert_results(rows, conn)
    return {"symbol": symbol, "status": "ok", "inserted": ins, "updated": upd, "periods": len(rows)}


async def run_batch(symbols: Optional[list[str]] = None, limit: int = 50):
    """Fetch BSE data for all symbols (or a subset)."""
    conn = await asyncpg.connect(DB_URL)
    try:
        if symbols:
            target = symbols
        else:
            # Fetch active stocks that haven't been updated recently
            rows = await conn.fetch("""
                SELECT s.nse_symbol FROM stocks s
                WHERE s.is_active = TRUE AND s.bse_code IS NOT NULL
                AND NOT EXISTS (
                    SELECT 1 FROM financial_results fr
                    WHERE fr.nse_symbol = s.nse_symbol
                    AND fr.data_source = 'BSE_XML'
                    AND fr.period_end >= (CURRENT_DATE - INTERVAL '90 days')
                )
                ORDER BY s.is_nifty50 DESC, s.nse_symbol
                LIMIT $1
            """, limit)
            target = [r["nse_symbol"] for r in rows]

        log.info(f"Fetching BSE data for {len(target)} symbols")
        results = []
        for i, sym in enumerate(target):
            result = await process_symbol(sym, conn)
            results.append(result)
            if (i + 1) % 10 == 0:
                log.info(f"Progress: {i+1}/{len(target)}")
            # Polite delay to avoid rate limiting
            await asyncio.sleep(1.0)

        ok = sum(1 for r in results if r.get("status") == "ok")
        failed = sum(1 for r in results if r.get("status") == "failed")
        log.info(f"BSE batch complete: {ok} ok, {failed} failed, {len(target)-ok-failed} skipped")
        return results

    finally:
        await conn.close()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Fetch BSE fundamental data")
    parser.add_argument("--symbol", help="Single NSE symbol")
    parser.add_argument("--limit", type=int, default=50, help="Max symbols to process")
    args = parser.parse_args()

    symbols = [args.symbol.upper()] if args.symbol else None
    asyncio.run(run_batch(symbols=symbols, limit=args.limit))
