"""
Sector Classification Seed Data
Maps NSE symbols to standardized sector/industry/macro-sector taxonomy.
Auto-builds from NSE's own sector data + manual overrides.

Run: python scripts/seed_sectors.py
"""
import asyncio
import csv
import io
import logging
import os

import httpx
import asyncpg
from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger("seed_sectors")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

DB_URL = os.environ.get("DATABASE_SYNC_URL", "postgresql://postgres:password@localhost:5432/stocklens")

# ── Macro sector groupings (our taxonomy) ─────────────────────
MACRO_GROUPS = {
    "FINANCIAL_SERVICES": ["Banking", "Finance", "Insurance", "NBFC", "Microfinance"],
    "TECHNOLOGY": ["IT", "Information Technology", "Software", "BPM", "Technology"],
    "CONSUMER": ["FMCG", "Retail", "Consumer Durables", "Textiles", "Apparel"],
    "INDUSTRIALS": ["Capital Goods", "Construction", "Engineering", "Infrastructure", "Logistics"],
    "ENERGY": ["Oil & Gas", "Power", "Renewables", "Coal"],
    "HEALTHCARE": ["Pharmaceuticals", "Healthcare", "Hospitals", "Diagnostics"],
    "MATERIALS": ["Metals", "Mining", "Chemicals", "Fertilisers", "Cement", "Plastics"],
    "REAL_ESTATE": ["Real Estate", "Realty", "Housing"],
    "TELECOM": ["Telecommunications", "Media"],
    "AGRICULTURE": ["Agriculture", "Agri", "Sugar", "Tea", "Coffee"],
    "AUTOMOTIVE": ["Automobiles", "Auto Ancillaries", "Tyres"],
    "OTHERS": [],
}

def get_macro_sector(sector: str) -> str:
    for macro, keywords in MACRO_GROUPS.items():
        for kw in keywords:
            if kw.lower() in sector.lower():
                return macro
    return "OTHERS"


# ── NSE Sector Index Constituents URLs ────────────────────────
# NSE provides sector-wise Nifty index constituents as CSV
NIFTY_INDICES = {
    "NIFTY BANK": ("Banking", "Banking"),
    "NIFTY IT": ("IT", "Information Technology"),
    "NIFTY PHARMA": ("Pharmaceuticals", "Healthcare"),
    "NIFTY FMCG": ("FMCG", "Consumer"),
    "NIFTY AUTO": ("Automobiles", "Automotive"),
    "NIFTY REALTY": ("Real Estate", "Real Estate"),
    "NIFTY METAL": ("Metals", "Materials"),
    "NIFTY ENERGY": ("Energy", "Energy"),
    "NIFTY INFRA": ("Infrastructure", "Industrials"),
    "NIFTY MEDIA": ("Media", "Telecom"),
    "NIFTY FINANCIAL SERVICES": ("Financial Services", "Financial Services"),
}

NSE_INDEX_CONSTITUENTS_URL = "https://www.nseindia.com/api/equity-stockIndices?index={index}"


async def fetch_index_constituents(index_name: str) -> list[str]:
    """Fetch stock list for a Nifty sectoral index."""
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
        "Referer": "https://www.nseindia.com/market-data/live-equity-market",
    }
    url = NSE_INDEX_CONSTITUENTS_URL.format(index=index_name.replace(" ", "%20"))
    async with httpx.AsyncClient(headers=headers, timeout=30, follow_redirects=True) as client:
        try:
            resp = await client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                return [item.get("symbol", "") for item in data.get("data", []) if item.get("symbol")]
        except Exception as e:
            log.warning(f"Failed to fetch {index_name}: {e}")
    return []


# ── Static overrides for common stocks ───────────────────────
STATIC_OVERRIDES: dict[str, tuple[str, str, str]] = {
    # symbol: (macro_sector, sector, industry)
    "RELIANCE": ("ENERGY", "Oil & Gas", "Integrated Oil & Gas"),
    "TCS": ("TECHNOLOGY", "IT", "IT Services"),
    "INFY": ("TECHNOLOGY", "IT", "IT Services"),
    "WIPRO": ("TECHNOLOGY", "IT", "IT Services"),
    "HCLTECH": ("TECHNOLOGY", "IT", "IT Services"),
    "TECHM": ("TECHNOLOGY", "IT", "IT Services"),
    "HDFC": ("FINANCIAL_SERVICES", "Finance", "Housing Finance"),
    "HDFCBANK": ("FINANCIAL_SERVICES", "Banking", "Private Banks"),
    "ICICIBANK": ("FINANCIAL_SERVICES", "Banking", "Private Banks"),
    "KOTAKBANK": ("FINANCIAL_SERVICES", "Banking", "Private Banks"),
    "AXISBANK": ("FINANCIAL_SERVICES", "Banking", "Private Banks"),
    "SBIN": ("FINANCIAL_SERVICES", "Banking", "Public Sector Banks"),
    "BAJFINANCE": ("FINANCIAL_SERVICES", "NBFC", "Consumer Finance"),
    "BAJAJFINSV": ("FINANCIAL_SERVICES", "Insurance", "Financial Conglomerate"),
    "LT": ("INDUSTRIALS", "Capital Goods", "Construction & Engineering"),
    "MARUTI": ("AUTOMOTIVE", "Automobiles", "Passenger Vehicles"),
    "TATAMOTORS": ("AUTOMOTIVE", "Automobiles", "Commercial & Passenger Vehicles"),
    "M&M": ("AUTOMOTIVE", "Automobiles", "Farm & Utility Vehicles"),
    "HINDUNILVR": ("CONSUMER", "FMCG", "Personal Products"),
    "NESTLEIND": ("CONSUMER", "FMCG", "Food Products"),
    "BRITANNIA": ("CONSUMER", "FMCG", "Food Products"),
    "SUNPHARMA": ("HEALTHCARE", "Pharmaceuticals", "Specialty Pharma"),
    "DRREDDY": ("HEALTHCARE", "Pharmaceuticals", "Specialty Pharma"),
    "CIPLA": ("HEALTHCARE", "Pharmaceuticals", "Specialty Pharma"),
    "DIVISLAB": ("HEALTHCARE", "Pharmaceuticals", "API & Formulations"),
    "ONGC": ("ENERGY", "Oil & Gas", "Upstream E&P"),
    "IOC": ("ENERGY", "Oil & Gas", "Refining & Marketing"),
    "BPCL": ("ENERGY", "Oil & Gas", "Refining & Marketing"),
    "COALINDIA": ("ENERGY", "Coal", "Coal Mining"),
    "NTPC": ("ENERGY", "Power", "Power Generation"),
    "POWERGRID": ("ENERGY", "Power", "Power Transmission"),
    "ADANIGREEN": ("ENERGY", "Renewables", "Solar & Wind Power"),
    "JSWSTEEL": ("MATERIALS", "Metals", "Steel"),
    "TATASTEEL": ("MATERIALS", "Metals", "Steel"),
    "HINDALCO": ("MATERIALS", "Metals", "Aluminium"),
    "VEDL": ("MATERIALS", "Metals", "Diversified Metals"),
    "BHARTIARTL": ("TELECOM", "Telecommunications", "Mobile & Broadband"),
    "DLF": ("REAL_ESTATE", "Real Estate", "Commercial & Residential"),
    "TITAN": ("CONSUMER", "Consumer Durables", "Jewellery & Watches"),
    "ASIANPAINT": ("MATERIALS", "Chemicals", "Decorative Paints"),
    "PIDILITIND": ("MATERIALS", "Chemicals", "Adhesives & Sealants"),
    "DMART": ("CONSUMER", "Retail", "Organised Retail"),
    "TATACONSUM": ("CONSUMER", "FMCG", "Beverages & Foods"),
    "ITC": ("CONSUMER", "FMCG", "Cigarettes & FMCG"),
    "ADANIENT": ("INDUSTRIALS", "Infrastructure", "Diversified Infrastructure"),
    "ADANIPORTS": ("INDUSTRIALS", "Logistics", "Ports & Logistics"),
    "BAJAJ-AUTO": ("AUTOMOTIVE", "Automobiles", "Two-Wheelers"),
    "HEROMOTOCO": ("AUTOMOTIVE", "Automobiles", "Two-Wheelers"),
    "EICHERMOT": ("AUTOMOTIVE", "Automobiles", "Two-Wheelers"),
    "APOLLOHOSP": ("HEALTHCARE", "Hospitals", "Multi-specialty Hospitals"),
    "GRASIM": ("MATERIALS", "Cement", "Cement & Textiles"),
    "ULTRACEMCO": ("MATERIALS", "Cement", "Cement"),
    "SHREECEM": ("MATERIALS", "Cement", "Cement"),
    "HDFCLIFE": ("FINANCIAL_SERVICES", "Insurance", "Life Insurance"),
    "SBILIFE": ("FINANCIAL_SERVICES", "Insurance", "Life Insurance"),
    "ICICIPRULI": ("FINANCIAL_SERVICES", "Insurance", "Life Insurance"),
    "SBICARD": ("FINANCIAL_SERVICES", "NBFC", "Credit Cards"),
    "INDUSINDBK": ("FINANCIAL_SERVICES", "Banking", "Private Banks"),
    "BANDHANBNK": ("FINANCIAL_SERVICES", "Banking", "Private Banks"),
    "RBLBANK": ("FINANCIAL_SERVICES", "Banking", "Private Banks"),
    "FEDERALBNK": ("FINANCIAL_SERVICES", "Banking", "Private Banks"),
    "CHOLAFIN": ("FINANCIAL_SERVICES", "NBFC", "Vehicle Finance"),
    "MUTHOOTFIN": ("FINANCIAL_SERVICES", "NBFC", "Gold Finance"),
    "PNB": ("FINANCIAL_SERVICES", "Banking", "Public Sector Banks"),
    "BANKBARODA": ("FINANCIAL_SERVICES", "Banking", "Public Sector Banks"),
    "CANBK": ("FINANCIAL_SERVICES", "Banking", "Public Sector Banks"),
    "ZOMATO": ("CONSUMER", "Retail", "Food Delivery & Technology"),
    "PAYTM": ("FINANCIAL_SERVICES", "Finance", "Digital Payments"),
    "NYKAA": ("CONSUMER", "Retail", "Beauty & Personal Care"),
    "POLICYBZR": ("FINANCIAL_SERVICES", "Insurance", "Insurtech"),
    "IRCTC": ("CONSUMER", "Retail", "Travel & Tourism"),
    "HAL": ("INDUSTRIALS", "Capital Goods", "Aerospace & Defence"),
    "BEL": ("INDUSTRIALS", "Capital Goods", "Defence Electronics"),
    "BHEL": ("INDUSTRIALS", "Capital Goods", "Power Equipment"),
    "SIEMENS": ("INDUSTRIALS", "Capital Goods", "Industrial Machinery"),
    "ABB": ("INDUSTRIALS", "Capital Goods", "Power & Automation"),
    "BOSCHLTD": ("AUTOMOTIVE", "Auto Ancillaries", "Auto Components"),
    "MOTHERSON": ("AUTOMOTIVE", "Auto Ancillaries", "Auto Components"),
    "MINDTREE": ("TECHNOLOGY", "IT", "IT Services"),
    "MPHASIS": ("TECHNOLOGY", "IT", "IT Services"),
    "LTIM": ("TECHNOLOGY", "IT", "IT Services"),
    "PERSISTENT": ("TECHNOLOGY", "IT", "IT Services"),
    "COFORGE": ("TECHNOLOGY", "IT", "IT Services"),
    "KPITTECH": ("TECHNOLOGY", "IT", "Automotive Software"),
    "ZENSARTECH": ("TECHNOLOGY", "IT", "IT Services"),
}


async def seed_sectors(conn):
    """Upsert sector_classification rows from static overrides."""
    # First, build unique sectors
    sectors: dict[tuple, None] = {}
    for symbol, (macro, sector, industry) in STATIC_OVERRIDES.items():
        sectors[(macro, sector, industry)] = None

    # Insert sectors
    sector_ids = {}
    for (macro, sector, industry) in sectors:
        row = await conn.fetchrow(
            "SELECT id FROM sector_classification WHERE sector=$1 AND industry=$2",
            sector, industry
        )
        if not row:
            row = await conn.fetchrow("""
                INSERT INTO sector_classification (macro_sector, sector, industry)
                VALUES ($1, $2, $3)
                ON CONFLICT (sector, industry) DO UPDATE
                    SET macro_sector = EXCLUDED.macro_sector
                RETURNING id
            """, macro, sector, industry)
        sector_ids[(macro, sector, industry)] = row["id"]

    # Update stocks
    updated = 0
    for symbol, (macro, sector, industry) in STATIC_OVERRIDES.items():
        sid = sector_ids.get((macro, sector, industry))
        if sid:
            result = await conn.execute(
                "UPDATE stocks SET sector_id=$1 WHERE nse_symbol=$2",
                sid, symbol
            )
            if result == "UPDATE 1":
                updated += 1

    log.info(f"Seeded {len(sector_ids)} sector records, updated {updated} stocks")
    return len(sector_ids), updated


async def run():
    conn = await asyncpg.connect(DB_URL)
    try:
        n_sectors, n_stocks = await seed_sectors(conn)
        log.info(f"""
╔══════════════════════════════════════╗
║   Sector Seed Complete!              ║
╠══════════════════════════════════════╣
║  Sector records: {n_sectors:<20}║
║  Stocks updated: {n_stocks:<20}║
╚══════════════════════════════════════╝
""")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(run())
