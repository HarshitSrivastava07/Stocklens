from fastapi import APIRouter, Depends, Body
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from dependencies.db import get_db
from models.db_models import Watchlist, Stock
import uuid

router = APIRouter()

@router.get("")
async def get_watchlist(db: AsyncSession = Depends(get_db)):
    r = await db.execute(select(Watchlist).order_by(Watchlist.added_at.desc()))
    items = r.scalars().all()
    return {"watchlist": [{"id": str(w.id), "nse_symbol": w.nse_symbol, "notes": w.notes, "added_at": w.added_at.isoformat()} for w in items]}

@router.post("/items", status_code=201)
async def add_to_watchlist(body: dict = Body(...), db: AsyncSession = Depends(get_db)):
    symbol = body.get("nse_symbol", "").upper().strip()
    notes = str(body.get("notes", "") or "")[:500]
    w = Watchlist(nse_symbol=symbol, notes=notes)
    db.add(w)
    await db.commit()
    return {"id": str(w.id), "nse_symbol": symbol}

@router.delete("/items/{item_id}", status_code=204)
async def remove_from_watchlist(item_id: str, db: AsyncSession = Depends(get_db)):
    await db.execute(delete(Watchlist).where(Watchlist.id == uuid.UUID(item_id)))
    await db.commit()
