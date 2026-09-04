"""
Market Data Router — real-time quotes, OHLCV candles, market status
Redis-first with DB fallback for stale/unavailable data
"""
import json
from datetime import datetime, timedelta
from typing import Optional

import pytz
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_

from dependencies.db import get_db
from dependencies.redis import get_redis_client
from models.db_models import RealtimeQuote, Stock, PriceCandleDaily
from config import settings

router = APIRouter()
IST = pytz.timezone("Asia/Kolkata")


# ─────────────────────────────────────────────────────────────
# GET /market/status — is market open right now?
# ─────────────────────────────────────────────────────────────
@router.get("/status")
async def get_market_status():
    """Return current market status based on IST time."""
    now_ist = datetime.now(IST)
    weekday = now_ist.weekday()  # 0=Mon, 6=Sun

    # NSE trading hours: 09:15 – 15:30 IST, Mon–Fri
    if weekday >= 5:  # Saturday or Sunday
        return {"status": "CLOSED", "reason": "Weekend", "next_open": "Monday 09:15 IST"}

    market_open = now_ist.replace(hour=9, minute=15, second=0, microsecond=0)
    market_close = now_ist.replace(hour=15, minute=30, second=0, microsecond=0)
    pre_open_start = now_ist.replace(hour=9, minute=0, second=0, microsecond=0)

    if pre_open_start <= now_ist < market_open:
        return {"status": "PRE_OPEN", "opens_at": "09:15 IST"}
    elif market_open <= now_ist <= market_close:
        return {
            "status": "OPEN",
            "opened_at": "09:15 IST",
            "closes_at": "15:30 IST",
            "minutes_remaining": int((market_close - now_ist).total_seconds() / 60),
        }
    elif now_ist > market_close:
        return {"status": "CLOSED", "reason": "Market closed for the day", "closed_at": "15:30 IST"}

    return {"status": "CLOSED"}


# ─────────────────────────────────────────────────────────────
# GET /market/quote/{symbol} — latest price (Redis first)
# ─────────────────────────────────────────────────────────────
@router.get("/quote/{symbol}")
async def get_quote(
    symbol: str,
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis_client),
):
    symbol = symbol.upper().strip()

    # 1. Try Redis first (fast path — only if NOT mock data)
    redis_key = f"latest:{symbol}"
    cached = await redis.get(redis_key)
    if cached:
        data = json.loads(cached)
        # Reject mock/seed data from Redis
        if data.get("data_source") not in ("MOCK", "SEED", None):
            data["data_freshness"] = "FRESH"
            data["source"] = "CACHE"
            data["price_validated"] = True
            return data

    # 2. Fallback to DB
    result = await db.execute(
        select(RealtimeQuote).where(RealtimeQuote.nse_symbol == symbol)
    )
    quote = result.scalar_one_or_none()

    if not quote:
        raise HTTPException(status_code=404, detail=f"No quote data for {symbol}")

    # 3. Detect mock/seed data — do NOT serve it as real price
    if quote.data_source in ("MOCK", "SEED") or not quote.ltp or quote.ltp <= 0:
        return {
            "nse_symbol": symbol,
            "ltp": None,
            "data_freshness": "UNAVAILABLE",
            "is_stale": True,
            "price_validated": False,
            "source": "NONE",
            "message": "Live market data unavailable. Run fetch_live_prices.py to populate.",
            "last_updated": quote.last_updated.isoformat() if quote.last_updated else None,
        }

    # 4. Staleness check
    now = datetime.now(pytz.utc)
    stale_threshold = timedelta(minutes=settings.PRICE_STALE_THRESHOLD_MIN)
    last_updated = quote.last_updated
    if last_updated.tzinfo is None:
        last_updated = last_updated.replace(tzinfo=pytz.utc)
    else:
        last_updated = last_updated.astimezone(pytz.utc)
    is_stale = (now - last_updated) > stale_threshold

    return {
        "nse_symbol": quote.nse_symbol,
        "ltp": float(quote.ltp),
        "open": float(quote.open) if quote.open else None,
        "high": float(quote.high) if quote.high else None,
        "low": float(quote.low) if quote.low else None,
        "close": float(quote.close) if quote.close else None,
        "volume": quote.volume,
        "change_abs": float(quote.change_abs) if quote.change_abs else None,
        "change_pct": float(quote.change_pct) if quote.change_pct else None,
        "week_52_high": float(quote.week_52_high) if quote.week_52_high else None,
        "week_52_low": float(quote.week_52_low) if quote.week_52_low else None,
        "last_updated": quote.last_updated.isoformat(),
        "data_freshness": "STALE" if is_stale else "FRESH",
        "is_stale": is_stale,
        "price_validated": True,
        "source": quote.data_source or "DB",
    }


# ─────────────────────────────────────────────────────────────
# GET /market/candles/{symbol} — OHLCV candles
# ─────────────────────────────────────────────────────────────
@router.get("/candles/{symbol}")
async def get_candles(
    symbol: str,
    interval: str = Query("1D", regex="^(1m|5m|15m|1H|1D)$"),
    from_date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    to_date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    limit: int = Query(200, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
):
    """Return OHLCV candles. Daily candles from price_candles_daily.
    Intraday from price_candles_1m (if interval is 1m/5m/15m/1H)."""
    symbol = symbol.upper().strip()

    if interval == "1D":
        # Daily candles
        query = (
            select(PriceCandleDaily)
            .where(PriceCandleDaily.nse_symbol == symbol)
            .order_by(PriceCandleDaily.date.desc())
            .limit(limit)
        )
        result = await db.execute(query)
        candles = result.scalars().all()

        return {
            "symbol": symbol,
            "interval": interval,
            "candles": [
                {
                    "date": c.date.isoformat(),
                    "open": float(c.open) if c.open else None,
                    "high": float(c.high) if c.high else None,
                    "low": float(c.low) if c.low else None,
                    "close": float(c.close) if c.close else None,
                    "volume": c.volume,
                }
                for c in reversed(candles)
            ],
        }

    # For intraday, query price_candles_1m
    # (simplified — production would query partitioned table)
    return {"symbol": symbol, "interval": interval, "candles": [], "message": "Use 1D for now — intraday requires market hours"}


# ─────────────────────────────────────────────────────────────
# GET /market/indices — Nifty 50, Bank Nifty, etc.
# ─────────────────────────────────────────────────────────────
@router.get("/indices")
async def get_indices(redis=Depends(get_redis_client)):
    """Return major index levels from Redis cache."""
    indices = ["NIFTY50", "BANKNIFTY", "NIFTY500", "NIFTYMIDCAP150", "NIFTYIT"]
    result = []

    for idx in indices:
        cached = await redis.get(f"latest:{idx}")
        if cached:
            data = json.loads(cached)
            result.append({
                "symbol": idx,
                "ltp": data.get("ltp"),
                "change_pct": data.get("change_pct"),
                "change_abs": data.get("change_abs"),
                "data_freshness": "FRESH",
            })
        else:
            result.append({
                "symbol": idx,
                "ltp": None,
                "change_pct": None,
                "change_abs": None,
                "data_freshness": "UNAVAILABLE",
            })

    return {"indices": result}
