"""
Sectors Router — returns sector list with real signal distribution + stock listings
"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, case
from sqlalchemy.orm import selectinload
from dependencies.db import get_db
from models.db_models import SectorClassification, Stock, Signal, IntrinsicValue, MLScores

router = APIRouter()


@router.get("")
async def list_sectors(db: AsyncSession = Depends(get_db)):
    """Aggregate signal counts and avg upside per sector."""
    result = await db.execute(
        select(
            SectorClassification,
            func.count(Stock.nse_symbol).label("total_stocks"),
            func.sum(case((Signal.signal_color == "GREEN", 1), else_=0)).label("green_count"),
            func.sum(case((Signal.signal_color == "YELLOW", 1), else_=0)).label("yellow_count"),
            func.sum(case((Signal.signal_color == "RED", 1), else_=0)).label("red_count"),
            func.sum(case((Signal.signal_color == "GREY", 1), else_=0)).label("grey_count"),
            func.avg(IntrinsicValue.upside_pct).label("avg_upside_pct"),
            func.avg(MLScores.risk_score).label("avg_risk_score"),
        )
        .join(Stock, Stock.sector_id == SectorClassification.id, isouter=True)
        .join(Signal, Signal.nse_symbol == Stock.nse_symbol, isouter=True)
        .join(IntrinsicValue, IntrinsicValue.nse_symbol == Stock.nse_symbol, isouter=True)
        .join(MLScores, MLScores.nse_symbol == Stock.nse_symbol, isouter=True)
        .where(Stock.is_active == True)
        .group_by(SectorClassification.id)
        .order_by(SectorClassification.macro_sector, SectorClassification.sector)
    )
    rows = result.all()

    return {
        "sectors": [
            {
                "id": str(r.SectorClassification.id),
                "macro_sector": r.SectorClassification.macro_sector,
                "sector": r.SectorClassification.sector,
                "industry": r.SectorClassification.industry,
                "total_stocks": r.total_stocks or 0,
                "green_count": r.green_count or 0,
                "yellow_count": r.yellow_count or 0,
                "red_count": r.red_count or 0,
                "grey_count": r.grey_count or 0,
                "avg_upside_pct": round(float(r.avg_upside_pct), 1) if r.avg_upside_pct else None,
                "avg_risk_score": round(float(r.avg_risk_score), 1) if r.avg_risk_score else None,
            }
            for r in rows
        ]
    }


@router.get("/{sector_id}")
async def get_sector(sector_id: str, db: AsyncSession = Depends(get_db)):
    import uuid
    try:
        uid = uuid.UUID(sector_id)
    except ValueError:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="Invalid sector ID")
    r = await db.execute(select(SectorClassification).where(SectorClassification.id == uid))
    sector = r.scalar_one_or_none()
    if not sector:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Sector not found")
    return {
        "id": str(sector.id),
        "macro_sector": sector.macro_sector,
        "sector": sector.sector,
        "industry": sector.industry,
    }


@router.get("/{sector_id}/stocks")
async def get_sector_stocks(sector_id: str, db: AsyncSession = Depends(get_db)):
    import uuid
    try:
        uid = uuid.UUID(sector_id)
    except ValueError:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="Invalid sector ID")

    result = await db.execute(
        select(
            Stock.nse_symbol, Stock.company_name, Stock.market_cap_category,
            Signal.signal_color, Signal.signal_label,
            IntrinsicValue.iv_blended, IntrinsicValue.upside_pct,
            MLScores.risk_score, MLScores.fundamental_score,
        )
        .join(Signal, Signal.nse_symbol == Stock.nse_symbol, isouter=True)
        .join(IntrinsicValue, IntrinsicValue.nse_symbol == Stock.nse_symbol, isouter=True)
        .join(MLScores, MLScores.nse_symbol == Stock.nse_symbol, isouter=True)
        .where(Stock.sector_id == uid, Stock.is_active == True)
        .order_by(IntrinsicValue.upside_pct.desc().nullslast())
    )
    rows = result.all()

    return {
        "stocks": [
            {
                "nse_symbol": r.nse_symbol,
                "company_name": r.company_name,
                "market_cap_category": r.market_cap_category,
                "signal_color": r.signal_color or "GREY",
                "signal_label": r.signal_label or "Insufficient Data",
                "iv_blended": float(r.iv_blended) if r.iv_blended else None,
                "upside_pct": float(r.upside_pct) if r.upside_pct else None,
                "risk_score": r.risk_score,
                "fundamental_score": r.fundamental_score,
            }
            for r in rows
        ]
    }
