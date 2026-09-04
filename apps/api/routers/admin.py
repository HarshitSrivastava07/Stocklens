"""
Admin Router — data import, job triggers, audit logs
No auth needed — local-only app, direct access
"""
import os
import uuid
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from dependencies.db import get_db
from models.db_models import DataImportLog, AuditLog, SystemJob, AdminOverride
from config import settings
import logging

router = APIRouter()
log = logging.getLogger("admin")


@router.get("/jobs")
async def list_jobs(db: AsyncSession = Depends(get_db)):
    r = await db.execute(select(SystemJob).order_by(SystemJob.created_at.desc()).limit(50))
    jobs = r.scalars().all()
    return {"jobs": [{"id": str(j.id), "job_name": j.job_name, "status": j.status,
                      "started_at": j.started_at.isoformat() if j.started_at else None,
                      "completed_at": j.completed_at.isoformat() if j.completed_at else None,
                      "duration_ms": j.duration_ms, "stocks_processed": j.stocks_processed,
                      "triggered_by": j.triggered_by} for j in jobs]}


@router.post("/jobs/trigger/{job_name}")
async def trigger_job(job_name: str, db: AsyncSession = Depends(get_db)):
    from services.scheduler_service import trigger_job as _trigger
    from datetime import datetime, timezone
    import asyncio
    valid_jobs = ["pre_market_check", "eod_snapshot", "fundamentals_refresh",
                  "ratio_engine", "valuation_engine", "signal_engine",
                  "alert_evaluation", "ai_summaries", "ml_clustering", "ml_retrain", "cleanup"]
    if job_name not in valid_jobs:
        raise HTTPException(status_code=400, detail=f"Unknown job. Valid: {valid_jobs}")

    job = SystemJob(job_name=job_name, status="RUNNING", triggered_by="MANUAL",
                    started_at=datetime.now(timezone.utc))
    db.add(job)
    await db.commit()
    job_id = job.id

    async def run_and_update():
        start = datetime.now(timezone.utc)
        async with (await get_db().__anext__()) as session:
            try:
                await _trigger(job_name)
                elapsed = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
                await session.execute(
                    __import__("sqlalchemy", fromlist=["text"]).text(
                        "UPDATE system_jobs SET status='COMPLETED', completed_at=NOW(), duration_ms=:ms WHERE id=:id"
                    ),
                    {"ms": elapsed, "id": str(job_id)}
                )
                await session.commit()
            except Exception as e:
                log.error(f"Job {job_name} failed: {e}")
                await session.execute(
                    __import__("sqlalchemy", fromlist=["text"]).text(
                        "UPDATE system_jobs SET status='FAILED', completed_at=NOW() WHERE id=:id"
                    ),
                    {"id": str(job_id)}
                )
                await session.commit()

    asyncio.create_task(run_and_update())
    return {"message": f"Job '{job_name}' triggered", "job_id": str(job_id)}


@router.get("/audit-logs")
async def get_audit_logs(limit: int = 100, db: AsyncSession = Depends(get_db)):
    r = await db.execute(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit))
    logs = r.scalars().all()
    return {"logs": [{"id": str(l.id), "action": l.action, "entity_type": l.entity_type,
                      "status": l.status, "created_at": l.created_at.isoformat()} for l in logs]}


@router.get("/import-logs")
async def get_import_logs(db: AsyncSession = Depends(get_db)):
    r = await db.execute(select(DataImportLog).order_by(DataImportLog.started_at.desc()).limit(50))
    logs = r.scalars().all()
    return {"logs": [{"id": str(l.id), "import_type": l.import_type, "filename": l.filename,
                      "records_total": l.records_total, "records_success": l.records_success,
                      "records_failed": l.records_failed, "status": l.status,
                      "started_at": l.started_at.isoformat() if l.started_at else None} for l in logs]}


@router.post("/financials/upload")
async def upload_financials(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """Upload CSV/Excel with financial data. Validated before processing."""
    # Validate file type
    allowed_types = {"text/csv", "application/vnd.ms-excel",
                     "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
    allowed_exts = {".csv", ".xls", ".xlsx"}

    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in allowed_exts:
        raise HTTPException(status_code=400, detail=f"File type not allowed. Use: {allowed_exts}")

    # Size check (25MB max)
    content = await file.read()
    if len(content) > settings.MAX_UPLOAD_SIZE_BYTES:
        raise HTTPException(status_code=400, detail="File too large. Max 25MB")

    # Log import
    import_log = DataImportLog(
        import_type="FINANCIALS",
        source="CSV_UPLOAD",
        filename=str(uuid.uuid4()) + ext,  # rename to UUID, never use original filename
        status="PENDING",
    )
    db.add(import_log)
    await db.commit()

    # TODO: Parse and validate CSV/Excel in background task
    # Use pandas with strict column validation before any DB writes
    return {
        "import_id": str(import_log.id),
        "filename_saved": import_log.filename,
        "size_bytes": len(content),
        "status": "QUEUED",
        "message": "File queued for processing. Check import logs for status.",
    }


@router.post("/stocks/import")
async def import_stocks(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """Import NSE stock universe from CSV."""
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in {".csv"}:
        raise HTTPException(status_code=400, detail="Only CSV files accepted for stock import")

    content = await file.read()
    if len(content) > settings.MAX_UPLOAD_SIZE_BYTES:
        raise HTTPException(status_code=400, detail="File too large")

    import_log = DataImportLog(
        import_type="STOCK_UNIVERSE",
        source="CSV_UPLOAD",
        filename=str(uuid.uuid4()) + ".csv",
        status="PENDING",
    )
    db.add(import_log)
    await db.commit()

    return {"import_id": str(import_log.id), "status": "QUEUED"}


@router.post("/override/{table_name}/{record_id}")
async def admin_override(
    table_name: str,
    record_id: str,
    body: dict,
    db: AsyncSession = Depends(get_db),
):
    """Manual data correction with full audit trail."""
    allowed_tables = {"financial_results", "financial_ratios", "valuation_assumptions"}
    if table_name not in allowed_tables:
        raise HTTPException(status_code=400, detail=f"Override not allowed for table: {table_name}")

    reason = str(body.get("reason", "")).strip()
    if not reason:
        raise HTTPException(status_code=400, detail="Reason is required for data override")

    override = AdminOverride(
        table_name=table_name,
        record_id=record_id,
        field_name=str(body.get("field_name", "")),
        old_value=str(body.get("old_value", "")),
        new_value=str(body.get("new_value", "")),
        reason=reason[:1000],
    )
    db.add(override)

    # Also log to audit_logs
    audit = AuditLog(action="ADMIN_OVERRIDE", entity_type=table_name, entity_id=record_id,
                     details={"field": body.get("field_name"), "reason": reason})
    db.add(audit)
    await db.commit()

    return {"override_id": str(override.id), "status": "RECORDED"}
