import asyncio
import os
import asyncpg
from dotenv import load_dotenv

async def check():
    load_dotenv()
    db_url = os.environ.get("DATABASE_SYNC_URL", "postgresql://postgres:password@localhost:5432/stocklens")
    db_url = db_url.replace("postgresql://", "").replace("postgresql+psycopg2://", "")
    
    conn = await asyncpg.connect(f"postgresql://{db_url}")
    print("=== DATABASE COUNTS ===")
    
    tables = [
        "stocks",
        "financial_results",
        "financial_ratios",
        "intrinsic_values",
        "signals",
        "ml_scores",
        "price_candles_daily"
    ]
    
    for table in tables:
        count = await conn.fetchval(f"SELECT COUNT(*) FROM {table}")
        print(f"{table:<20} : {count}")
    
    await conn.close()

if __name__ == "__main__":
    asyncio.run(check())
