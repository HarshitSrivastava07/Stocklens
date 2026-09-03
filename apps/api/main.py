"""
StockLens API + dashboard.

Reads the same Redis + Postgres that apps/worker writes; it never writes.

  GET /                  -> static/index.html (live quotes dashboard)
  GET /health            -> healthcheck for Railway
  GET /api/stats         -> universe + freshness counters
  GET /api/quotes        -> latest quote per symbol (realtime_quotes JOIN stocks)
  GET /api/quotes/{sym}  -> one symbol
  GET /api/stream        -> Server-Sent Events, forwarded from Redis `ticks:dashboard`
"""
import contextlib
import os
import re
from pathlib import Path

import asyncpg
import redis.asyncio as aioredis
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse

def _clean(raw: str) -> str:
    """Trim whitespace / stray quotes / a pasted `NAME=` prefix from env values."""
    v = (raw or "").strip().strip('"').strip("'").strip()
    m = re.match(r"^[A-Za-z_][A-Za-z0-9_]{2,40}=(?=[a-z]+://)", v)
    if m:
        v = v[m.end():].strip().strip('"').strip("'").strip()
    return v


DATABASE_URL = (
    _clean(os.environ.get("DATABASE_URL", ""))
    .replace("postgresql+asyncpg://", "postgresql://")
    .replace("+asyncpg", "")
)
REDIS_URL = _clean(os.environ.get("REDIS_URL", ""))
STATIC_DIR = Path(__file__).parent / "static"


def _check_url(name: str, val: str, schemes: tuple[str, ...]) -> None:
    if not val:
        raise RuntimeError(f"{name} is empty on this service — set it in the Variables tab.")
    if val.startswith("${{") or "${{" in val:
        raise RuntimeError(
            f"{name} is the unresolved Railway template {val!r}. The reference did "
            f"not resolve — set {name} to the LITERAL connection string from the "
            f"Postgres/Redis service instead (no ${{{{ }}}})."
        )
    if not val.startswith(schemes):
        raise RuntimeError(
            f"{name} is not a valid URL. Got {val[:18]!r}… (length {len(val)}); "
            f"expected it to start with one of {schemes}."
        )

QUOTES_SQL = """
    SELECT q.nse_symbol,
           s.company_name,
           s.exchange,
           s.currency,
           q.ltp, q.open, q.high, q.low, q.close,
           q.change_abs, q.change_pct, q.volume,
           q.week_52_high, q.week_52_low, q.market_cap,
           q.data_source, q.is_stale, q.last_updated
      FROM realtime_quotes q
      JOIN stocks s ON s.nse_symbol = q.nse_symbol
"""

_NUMERIC = (
    "ltp", "open", "high", "low", "close", "change_abs", "change_pct",
    "week_52_high", "week_52_low", "market_cap",
)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    _check_url("DATABASE_URL", DATABASE_URL, ("postgresql://", "postgres://"))
    app.state.db = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
    app.state.redis = (
        aioredis.from_url(REDIS_URL, decode_responses=True) if REDIS_URL else None
    )
    try:
        yield
    finally:
        await app.state.db.close()
        if app.state.redis is not None:
            await app.state.redis.aclose()


app = FastAPI(title="StockLens", lifespan=lifespan)


def _row_to_quote(r: asyncpg.Record) -> dict:
    d = dict(r)
    lu = d.get("last_updated")
    d["last_updated"] = lu.isoformat() if lu else None
    for k in _NUMERIC:
        if d.get(k) is not None:
            d[k] = float(d[k])
    return d


@app.get("/health")
async def health():
    try:
        await app.state.db.fetchval("SELECT 1")
    except Exception as e:  # pragma: no cover
        raise HTTPException(503, f"db unavailable: {e}")
    return {"status": "ok"}


@app.get("/api/stats")
async def stats():
    async with app.state.db.acquire() as c:
        return {
            "stocks_total": await c.fetchval(
                "SELECT COUNT(*) FROM stocks WHERE is_active"
            ),
            "stocks_tracked": await c.fetchval(
                "SELECT COUNT(*) FROM stocks WHERE yahoo_ticker IS NOT NULL AND is_active"
            ),
            "quotes": await c.fetchval("SELECT COUNT(*) FROM realtime_quotes"),
            "gainers": await c.fetchval(
                "SELECT COUNT(*) FROM realtime_quotes WHERE change_pct > 0"
            ),
            "losers": await c.fetchval(
                "SELECT COUNT(*) FROM realtime_quotes WHERE change_pct < 0"
            ),
            "last_updated": (
                lambda t: t.isoformat() if t else None
            )(await c.fetchval("SELECT MAX(last_updated) FROM realtime_quotes")),
        }


@app.get("/api/quotes")
async def quotes(
    exchange: str | None = None,
    search: str | None = None,
    sort: str = Query("change_pct"),
    order: str = Query("desc"),
    limit: int = Query(500, ge=1, le=2000),
):
    if sort not in {"change_pct", "ltp", "volume", "market_cap", "last_updated", "nse_symbol"}:
        sort = "change_pct"
    order_sql = "ASC" if order.lower() == "asc" else "DESC"

    sql, args, conds = QUOTES_SQL, [], []
    if exchange and exchange.lower() != "all":
        args.append(exchange.upper())
        conds.append(f"s.exchange = ${len(args)}")
    if search:
        args.append(f"%{search}%")
        conds.append(f"(s.company_name ILIKE ${len(args)} OR q.nse_symbol ILIKE ${len(args)})")
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    args.append(limit)
    sql += f" ORDER BY q.{sort} {order_sql} NULLS LAST LIMIT ${len(args)}"

    rows = await app.state.db.fetch(sql, *args)
    return [_row_to_quote(r) for r in rows]


@app.get("/api/quotes/{symbol}")
async def quote(symbol: str):
    r = await app.state.db.fetchrow(QUOTES_SQL + " WHERE q.nse_symbol = $1", symbol.upper())
    if not r:
        raise HTTPException(404, "no quote for that symbol yet")
    return _row_to_quote(r)


@app.get("/api/stream")
async def stream(request: Request):
    if app.state.redis is None:
        raise HTTPException(503, "REDIS_URL not configured on this service")

    async def gen():
        pubsub = app.state.redis.pubsub()
        await pubsub.subscribe("ticks:dashboard")
        try:
            while not await request.is_disconnected():
                msg = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=15
                )
                if msg and msg.get("type") == "message":
                    yield f"data: {msg['data']}\n\n"
                else:
                    yield ": keepalive\n\n"
        finally:
            await pubsub.aclose()

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")
