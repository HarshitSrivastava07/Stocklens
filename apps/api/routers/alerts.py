from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from dependencies.db import get_db
router = APIRouter()

@router.get("")
async def get_alerts():
    return {"alerts": []}

@router.post("", status_code=201)
async def create_alert(body: dict):
    return {"id": "placeholder", "message": "Alert created"}

@router.patch("/{alert_id}")
async def update_alert(alert_id: str, body: dict):
    return {"id": alert_id, "updated": True}

@router.delete("/{alert_id}", status_code=204)
async def delete_alert(alert_id: str):
    pass
