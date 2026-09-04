from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from dependencies.db import get_db
from models.db_models import Portfolio, PortfolioHolding
router = APIRouter()

@router.get("")
async def get_portfolio(db: AsyncSession = Depends(get_db)):
    r = await db.execute(select(Portfolio).limit(1))
    portfolio = r.scalar_one_or_none()
    if not portfolio:
        return {"portfolio": None, "holdings": []}
    h = await db.execute(select(PortfolioHolding).where(PortfolioHolding.portfolio_id == portfolio.id))
    holdings = h.scalars().all()
    return {
        "portfolio": {"id": str(portfolio.id), "name": portfolio.name},
        "holdings": [{"id": str(holding.id), "nse_symbol": holding.nse_symbol, "quantity": float(holding.quantity), "avg_cost": float(holding.avg_cost), "buy_date": holding.buy_date.isoformat() if holding.buy_date else None} for holding in holdings]
    }

@router.post("/holdings", status_code=201)
async def add_holding(body: dict, db: AsyncSession = Depends(get_db)):
    r = await db.execute(select(Portfolio).limit(1))
    portfolio = r.scalar_one_or_none()
    if not portfolio:
        portfolio = Portfolio(name="My Portfolio")
        db.add(portfolio)
        await db.flush()
    holding = PortfolioHolding(
        portfolio_id=portfolio.id,
        nse_symbol=body["nse_symbol"].upper().strip(),
        quantity=float(body["quantity"]),
        avg_cost=float(body["avg_cost"]),
    )
    db.add(holding)
    await db.commit()
    return {"id": str(holding.id), "nse_symbol": holding.nse_symbol}

@router.get("/analytics")
async def get_portfolio_analytics():
    return {"analytics": None, "message": "Portfolio analytics computed after data refresh"}
