"""
Financial Data CSV/Excel Parser
Accepts files uploaded via admin API and parses them into financial_results table.

Supported formats:
  1. Screener.in Export format (most common — free export)
  2. Custom template (our own column mapping)

Usage:
    python parse_financials.py --file financials.xlsx --symbol RELIANCE
    python parse_financials.py --file screener_export.xlsx --auto  # detect symbol from file
"""
import argparse
import asyncio
import logging
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import pandas as pd
import asyncpg
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("parse_financials")

DB_URL = os.environ.get("DATABASE_SYNC_URL", "postgresql://postgres:password@localhost:5432/stocklens")

# ─────────────────────────────────────────────────────────────
# Screener.in column mappings
# Screener exports annual P&L + Balance Sheet + Cash Flow
# ─────────────────────────────────────────────────────────────
SCREENER_PL_COLS = {
    "Sales": "revenue",
    "Revenue": "revenue",
    "Net Sales": "revenue",
    "Operating Profit": "ebitda",
    "EBITDA": "ebitda",
    "OPM %": "ebitda_margin",
    "OPM": "ebitda_margin",
    "Profit before tax": "pbt",
    "PBT": "pbt",
    "Net Profit": "pat",
    "PAT": "pat",
    "EPS in Rs": "eps",
    "EPS": "eps",
    "Dividend Payout %": "dividend_payout_pct",
}

SCREENER_BS_COLS = {
    "Reserves": "reserves",
    "Equity Capital": "equity_capital",
    "Total Liabilities": "total_assets",
    "Total Assets": "total_assets",
    "Borrowings": "total_debt",
    "Cash Equivalents": "cash_and_equiv",
    "Cash": "cash_and_equiv",
    "Working Capital": "working_capital",
}

SCREENER_CF_COLS = {
    "Cash from Operating Activity": "cfo",
    "Operating Cash Flow": "cfo",
    "Cash from Investing Activity": "capex_raw",
    "Cash from Financing Activity": "cff",
}

MAX_REASONABLE = {
    "revenue": 1e9,     # ₹100 Cr max (in Cr units)
    "pat": 5e8,
    "total_debt": 2e9,
    "eps": 10000,
}


def _safe_num(v) -> Optional[float]:
    """Convert Screener value to float. Handles %, commas, Cr suffixes."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = str(v).strip().replace(",", "")
    # Remove % sign — Screener exports OPM as "18%" or "18.2"
    s = s.replace("%", "")
    # Handle negative in parentheses: (1234) → -1234
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1]
    try:
        return float(s)
    except ValueError:
        return None


def _parse_screener_year(col_name: str) -> Optional[date]:
    """Parse 'Mar 2023' or '2023' from Screener column headers → fiscal year end."""
    col_name = str(col_name).strip()
    # "Mar 2023" format
    m = re.match(r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{4})", col_name, re.I)
    if m:
        month_map = {"jan":1,"feb":2,"mar":3,"apr":4,"may":5,"jun":6,
                     "jul":7,"aug":8,"sep":9,"oct":10,"nov":11,"dec":12}
        month = month_map[m.group(1).lower()]
        year = int(m.group(2))
        import calendar
        last_day = calendar.monthrange(year, month)[1]
        return date(year, month, last_day)
    # Bare year "2023"
    if re.match(r"^\d{4}$", col_name):
        return date(int(col_name), 3, 31)  # assume March year-end
    return None


def validate_row(row: dict, symbol: str) -> list[str]:
    """Return list of validation errors for a financial row."""
    errors = []
    rev = row.get("revenue")
    pat = row.get("pat")
    eps = row.get("eps")
    shares = row.get("shares_outstanding")

    if rev is not None and rev < 0:
        errors.append(f"Negative revenue: {rev} — possible data issue")
    if eps and shares and rev:
        implied_pat = eps * shares / 1e7  # rough check
        if pat and abs(implied_pat - pat) / max(abs(pat), 1) > 0.5:
            errors.append(f"EPS×Shares vs PAT mismatch: {implied_pat:.0f} vs {pat:.0f}")
    for field, limit in MAX_REASONABLE.items():
        val = row.get(field)
        if val is not None and abs(val) > limit:
            errors.append(f"Unusually large {field}: {val} (limit: {limit})")
    return errors


def parse_screener_excel(filepath: str, symbol: str) -> list[dict]:
    """
    Parse Screener.in Excel export into a list of financial_results rows.
    Screener exports: P&L, Balance Sheet, Cash Flow as separate sheets.
    """
    log.info(f"Parsing Screener export: {filepath}")
    xl = pd.ExcelFile(filepath)
    sheets = {s.lower(): s for s in xl.sheet_names}

    def read_sheet(*names) -> Optional[pd.DataFrame]:
        for n in names:
            if n in sheets:
                df = xl.parse(sheets[n], index_col=0)
                # Drop rows where index is NaN
                df = df.dropna(how="all")
                df.index = df.index.map(str).str.strip()
                return df
        return None

    pl_df = read_sheet("profit & loss", "p&l", "profit and loss", "income statement")
    bs_df = read_sheet("balance sheet", "bs", "balance")
    cf_df = read_sheet("cash flow", "cf", "cash flows")

    # Parse year columns
    source_df = pl_df if pl_df is not None else bs_df
    if source_df is None:
        raise ValueError("No P&L or Balance Sheet sheet found")

    year_cols = {}
    for col in source_df.columns:
        d = _parse_screener_year(str(col))
        if d:
            year_cols[col] = d

    if not year_cols:
        raise ValueError("Could not parse any year columns from the spreadsheet")

    rows = []
    for col, period_end in year_cols.items():
        row = {
            "nse_symbol": symbol,
            "period_end": period_end,
            "period_type": "A",  # annual
            "data_source": "SCREENER_EXPORT",
            "is_verified": False,
            "confidence_level": "MEDIUM",
        }

        # P&L
        if pl_df is not None and col in pl_df.columns:
            for screener_row, our_col in SCREENER_PL_COLS.items():
                if screener_row in pl_df.index:
                    val = _safe_num(pl_df.loc[screener_row, col])
                    if val is not None:
                        row[our_col] = val

        # Balance Sheet
        if bs_df is not None and col in bs_df.columns:
            for screener_row, our_col in SCREENER_BS_COLS.items():
                if screener_row in bs_df.index:
                    val = _safe_num(bs_df.loc[screener_row, col])
                    if val is not None:
                        row[our_col] = val

        # Cash Flow
        if cf_df is not None and col in cf_df.columns:
            for screener_row, our_col in SCREENER_CF_COLS.items():
                if screener_row in cf_df.index:
                    val = _safe_num(cf_df.loc[screener_row, col])
                    if val is not None:
                        row[our_col] = val

        # Derive net worth
        if "equity_capital" in row and "reserves" in row:
            row["net_worth"] = (row.get("equity_capital") or 0) + (row.get("reserves") or 0)

        # Derive shares from equity capital and face value
        ec = row.get("equity_capital")
        if ec and ec > 0:
            row["shares_outstanding"] = int(ec * 1e7 / 10)  # assume FV=10, Cr units

        # Free cash flow = CFO + Capex (Capex is negative in Screener)
        cfo = row.get("cfo")
        capex = row.get("capex_raw")
        if cfo is not None and capex is not None:
            row["free_cash_flow"] = cfo + capex  # capex is negative, so this subtracts

        # Validate
        errors = validate_row(row, symbol)
        if errors:
            for e in errors:
                log.warning(f"  [{symbol} {period_end}] {e}")

        rows.append(row)

    log.info(f"Parsed {len(rows)} annual periods for {symbol}")
    return rows


async def write_to_db(rows: list[dict], conn) -> tuple[int, int, int]:
    """Upsert financial_results rows."""
    inserted, updated, skipped = 0, 0, 0

    for row in rows:
        try:
            # Check existing
            existing = await conn.fetchrow(
                "SELECT id FROM financial_results WHERE nse_symbol=$1 AND period_end=$2 AND period_type=$3",
                row["nse_symbol"], row["period_end"], row["period_type"]
            )

            fields = {k: v for k, v in row.items() if k not in ("nse_symbol", "period_end", "period_type")}

            if existing:
                if fields:
                    set_clause = ", ".join(f"{k}=${i+1}" for i, k in enumerate(fields.keys()))
                    values = list(fields.values())
                    values.extend([row["nse_symbol"], row["period_end"], row["period_type"]])
                    await conn.execute(
                        f"UPDATE financial_results SET {set_clause} WHERE nse_symbol=${len(values)-2} AND period_end=${len(values)-1} AND period_type=${len(values)}",
                        *values
                    )
                updated += 1
            else:
                all_fields = {**row}
                cols = ", ".join(all_fields.keys())
                placeholders = ", ".join(f"${i+1}" for i in range(len(all_fields)))
                await conn.execute(
                    f"INSERT INTO financial_results ({cols}) VALUES ({placeholders}) ON CONFLICT (nse_symbol, period_end, period_type) DO NOTHING",
                    *all_fields.values()
                )
                inserted += 1

        except Exception as e:
            log.warning(f"DB write failed for {row.get('nse_symbol')} {row.get('period_end')}: {e}")
            skipped += 1

    return inserted, updated, skipped


async def main():
    parser = argparse.ArgumentParser(description="Parse financial data into StockLens DB")
    parser.add_argument("--file", required=True, help="Path to CSV or Excel file")
    parser.add_argument("--symbol", help="NSE symbol (required unless --auto)")
    parser.add_argument("--auto", action="store_true", help="Detect symbol from filename")
    args = parser.parse_args()

    filepath = Path(args.file)
    if not filepath.exists():
        log.error(f"File not found: {filepath}")
        sys.exit(1)

    symbol = args.symbol
    if not symbol and args.auto:
        # Try to extract symbol from filename: "RELIANCE_financials.xlsx" → "RELIANCE"
        symbol = filepath.stem.split("_")[0].upper()
        log.info(f"Auto-detected symbol: {symbol}")

    if not symbol:
        log.error("--symbol is required (or use --auto)")
        sys.exit(1)

    ext = filepath.suffix.lower()
    if ext not in (".xlsx", ".xls", ".csv"):
        log.error(f"Unsupported file type: {ext}. Use .xlsx, .xls, or .csv")
        sys.exit(1)

    # Parse
    try:
        rows = parse_screener_excel(str(filepath), symbol.strip().upper())
    except Exception as e:
        log.error(f"Parse failed: {e}")
        sys.exit(1)

    if not rows:
        log.error("No data rows parsed")
        sys.exit(1)

    # Write to DB
    log.info(f"Connecting to DB and writing {len(rows)} rows...")
    conn = await asyncpg.connect(DB_URL)
    try:
        inserted, updated, skipped = await write_to_db(rows, conn)
        log.info(f"""
╔══════════════════════════════════════════╗
║     Financials Import Complete!          ║
╠══════════════════════════════════════════╣
║  Symbol:    {symbol:<29}║
║  Periods:   {len(rows):<29}║
║  Inserted:  {inserted:<29}║
║  Updated:   {updated:<29}║
║  Skipped:   {skipped:<29}║
╚══════════════════════════════════════════╝
Next step: Trigger ratio engine and valuation engine from admin panel.
""")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
