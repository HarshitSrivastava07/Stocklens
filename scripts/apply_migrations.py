"""Apply the SQL files in supabase/migrations/ in order.

There is no migration runner in this repo and no `psql` requirement — this
script applies each file with asyncpg (already a worker dependency).

Usage (PowerShell):
    $env:DATABASE_SYNC_URL = "postgresql://user:pass@host:port/db"
    python scripts/apply_migrations.py

Each file runs as one implicit transaction, so a failure rolls that file
back cleanly. The files use `IF NOT EXISTS` throughout, so re-running is safe.
"""
import asyncio
import os
import sys
from pathlib import Path

import asyncpg

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "supabase" / "migrations"
ORDER = [
    "001_initial.sql",
    "002_global_stocks.sql",
    "002_pgvector_fo_peers.sql",
]


async def main() -> None:
    db_url = os.environ.get("DATABASE_SYNC_URL") or os.environ.get("DATABASE_URL")
    if not db_url:
        sys.exit("Set DATABASE_SYNC_URL (or DATABASE_URL) to your Postgres connection string.")
    db_url = db_url.replace("+asyncpg", "").replace("+psycopg2", "")

    conn = await asyncpg.connect(db_url)
    try:
        for name in ORDER:
            sql = (MIGRATIONS_DIR / name).read_text(encoding="utf-8")
            print(f"-> applying {name} ({len(sql):,} bytes)")
            await conn.execute(sql)
            print(f"   ok: {name}")
    finally:
        await conn.close()
    print("All migrations applied.")


if __name__ == "__main__":
    asyncio.run(main())
