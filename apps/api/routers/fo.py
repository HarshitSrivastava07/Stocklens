"""
F&O Router — Futures & Options data for NSE F&O eligible stocks
"""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from dependencies.db import get_db
from models.db_models import Stock
from dependencies.redis import get_redis_client
import json

router = APIRouter()


@router.get("/eligible")
async def list_fo_eligible(
    db: AsyncSession = Depends(get_db),
    limit: int = Query(500, ge=1, le=5000),
):
    """List all F&O eligible NSE stocks."""
    result = await db.execute(
        select(Stock.nse_symbol, Stock.company_name, Stock.market_cap_category)
        .where(Stock.is_fno == True, Stock.is_active == True)
        .order_by(Stock.nse_symbol)
        .limit(limit)
    )
    rows = result.all()
    return {
        "count": len(rows),
        "stocks": [{"nse_symbol": r.nse_symbol, "company_name": r.company_name, "market_cap_category": r.market_cap_category}
                   for r in rows]
    }


@router.get("/oi/{symbol}")
async def get_oi_data(symbol: str, redis=Depends(get_redis_client)):
    """Get Open Interest data from Redis (if worker is streaming it)."""
    symbol = symbol.upper().strip()
    key = f"fo_oi:{symbol}"
    data = await redis.get(key)
    if not data:
        return {
            "symbol": symbol,
            "oi_data": None,
            "message": "OI data not available. Ensure the F&O worker is running."
        }
    return {"symbol": symbol, "oi_data": json.loads(data)}


@router.get("/pcr/{symbol}")
async def get_pcr(symbol: str, redis=Depends(get_redis_client)):
    """Put-Call Ratio from Redis."""
    symbol = symbol.upper().strip()
    key = f"fo_pcr:{symbol}"
    data = await redis.get(key)
    if not data:
        return {
            "symbol": symbol,
            "pcr": None,
            "interpretation": None,
            "message": "PCR data not available."
        }
    pcr_data = json.loads(data)
    pcr = pcr_data.get("pcr")
    interpretation = (
        "Bearish (heavy put buying)" if pcr and pcr < 0.7 else
        "Neutral" if pcr and pcr <= 1.2 else
        "Bullish (heavy call writing)" if pcr else None
    )
    return {"symbol": symbol, "pcr": pcr, "interpretation": interpretation}


@router.get("/iv/{symbol}")
async def get_implied_volatility(symbol: str, redis=Depends(get_redis_client)):
    """Implied Volatility data from Redis."""
    symbol = symbol.upper().strip()
    key = f"fo_iv:{symbol}"
    data = await redis.get(key)
    if not data:
        return {"symbol": symbol, "iv": None, "message": "IV data not available."}
    return {"symbol": symbol, **json.loads(data)}


@router.get("/chain/{symbol}")
async def get_option_chain(
    symbol: str,
    expiry: Optional[str] = Query(None, description="YYYY-MM-DD format"),
    redis=Depends(get_redis_client),
):
    """Full option chain from Redis (populated by F&O worker)."""
    symbol = symbol.upper().strip()
    key = f"fo_chain:{symbol}:{expiry or 'current'}"
    data = await redis.get(key)
    if not data:
        return {
            "symbol": symbol,
            "expiry": expiry,
            "chain": None,
            "message": "Option chain not available. Start the F&O WebSocket worker."
        }
    return {"symbol": symbol, "expiry": expiry, "chain": json.loads(data)}


@router.get("/max-pain/{symbol}")
async def get_max_pain(symbol: str, redis=Depends(get_redis_client)):
    """Max Pain level (option strike where most options expire worthless)."""
    symbol = symbol.upper().strip()
    key = f"fo_chain:{symbol}:current"
    data = await redis.get(key)
    if not data:
        return {"symbol": symbol, "max_pain": None, "message": "Option chain required"}

    try:
        chain = json.loads(data)
        strikes = chain.get("strikes", [])
        if not strikes:
            return {"symbol": symbol, "max_pain": None}

        # Calculate max pain: strike where sum of (strike - option_price) is minimized
        pain = {}
        for strike_data in strikes:
            sp = strike_data.get("strike_price", 0)
            for strike_data2 in strikes:
                sp2 = strike_data2.get("strike_price", 0)
                ce_oi = strike_data2.get("call_oi", 0) or 0
                pe_oi = strike_data2.get("put_oi", 0) or 0
                pain[sp] = pain.get(sp, 0)
                if sp <= sp2:
                    pain[sp] += (sp2 - sp) * ce_oi
                if sp >= sp2:
                    pain[sp] += (sp - sp2) * pe_oi

        max_pain_strike = min(pain, key=lambda k: pain[k]) if pain else None
        return {"symbol": symbol, "max_pain": max_pain_strike, "pain_values": pain}
    except Exception as e:
        return {"symbol": symbol, "max_pain": None, "error": str(e)}
