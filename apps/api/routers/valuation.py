from fastapi import APIRouter, Depends, HTTPException, Body
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from dependencies.db import get_db
from models.db_models import ValuationRun, IntrinsicValue, ReverseDcfOutput

router = APIRouter()

@router.get("/{symbol}")
async def get_valuation(symbol: str, db: AsyncSession = Depends(get_db)):
    symbol = symbol.upper().strip()
    r = await db.execute(select(IntrinsicValue).where(IntrinsicValue.nse_symbol == symbol))
    iv = r.scalar_one_or_none()
    if not iv:
        raise HTTPException(status_code=404, detail="Valuation not yet computed")
    return {
        "symbol": symbol,
        "iv_bear": float(iv.iv_bear) if iv.iv_bear else None,
        "iv_base": float(iv.iv_base) if iv.iv_base else None,
        "iv_bull": float(iv.iv_bull) if iv.iv_bull else None,
        "iv_blended": float(iv.iv_blended) if iv.iv_blended else None,
        "cmp": float(iv.cmp) if iv.cmp else None,
        "upside_pct": float(iv.upside_pct) if iv.upside_pct else None,
        "margin_of_safety": float(iv.margin_of_safety) if iv.margin_of_safety else None,
        "primary_model": iv.primary_model,
        "valuation_confidence": iv.valuation_confidence,
        "updated_at": iv.updated_at.isoformat() if iv.updated_at else None,
    }

@router.get("/{symbol}/reverse-dcf")
async def get_reverse_dcf(symbol: str, db: AsyncSession = Depends(get_db)):
    symbol = symbol.upper().strip()
    r = await db.execute(
        select(ReverseDcfOutput).where(ReverseDcfOutput.nse_symbol == symbol)
        .order_by(ReverseDcfOutput.computed_at.desc()).limit(1)
    )
    rdcf = r.scalar_one_or_none()
    if not rdcf:
        return {"symbol": symbol, "data": None, "message": "Reverse DCF not yet computed"}
    return {
        "symbol": symbol,
        "cmp": float(rdcf.cmp) if rdcf.cmp else None,
        "implied_growth_rate": float(rdcf.implied_growth_rate) if rdcf.implied_growth_rate else None,
        "historical_growth_rate": float(rdcf.historical_growth_rate) if rdcf.historical_growth_rate else None,
        "interpretation": rdcf.interpretation,
        "computed_at": rdcf.computed_at.isoformat() if rdcf.computed_at else None,
    }
