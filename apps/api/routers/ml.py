"""
Scores, clusters and pipeline control.

This router previously contained three things that had no business in a product
anyone pays for, all removed here:

  * ``GET /run-inference`` fabricated financials, ratios, intrinsic values,
    signals, ML scores and daily price candles with ``random.uniform`` and wrote
    them into the live database. It was a **GET**, so a crawler, a browser
    prefetch or a shared link could fill production with invented valuations.
  * ``GET /import-all-nse`` fell back, when the NSE download failed, to
    **inventing 2,700 companies** — "TECH0001 India Enterprises 1", all sharing
    the placeholder ISIN ``INE000000000`` — and inserting them as active,
    tradeable equities.
  * ``GET /unlock`` ran ``pg_terminate_backend`` against any connection whose
    query text contained ``INSERT``, unauthenticated. That is an open endpoint
    for killing arbitrary database sessions.

Everything now either reads real stored results or triggers the real pipeline,
and every mutating endpoint requires the admin token.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from dependencies.auth import require_admin
from dependencies.db import get_db

router = APIRouter()
log = logging.getLogger("ml_router")


# ─────────────────────────────────────────────────────────────
# Scores — read from what the engines actually computed
# ─────────────────────────────────────────────────────────────
@router.get("/scores/{symbol}")
async def get_scores(symbol: str, db: AsyncSession = Depends(get_db)):
    """
    The four component scores behind a stock's signal.

    Returns ``computed: false`` with a reason when the pipeline has not yet run
    for this symbol, rather than zeros that look like a real assessment.
    """
    symbol = symbol.upper().strip()
    row = (
        await db.execute(
            text(
                """SELECT value_score, quality_score, momentum_score, risk_score,
                          composite_score, action, conviction, valuation_confidence,
                          trend, computed_at
                     FROM signals
                    WHERE nse_symbol = :symbol"""
            ),
            {"symbol": symbol},
        )
    ).mappings().first()

    if row is None:
        return {
            "symbol": symbol,
            "computed": False,
            "reason": "No signal computed yet for this symbol.",
            "scores": None,
        }

    return {
        "symbol": symbol,
        "computed": True,
        "scores": {
            "value": _as_float(row["value_score"]),
            "quality": _as_float(row["quality_score"]),
            "momentum": _as_float(row["momentum_score"]),
            "risk": _as_float(row["risk_score"]),
            "composite": _as_float(row["composite_score"]),
        },
        "action": row["action"],
        "conviction": _as_float(row["conviction"]),
        "valuation_confidence": row["valuation_confidence"],
        "trend": row["trend"],
        "computed_at": row["computed_at"].isoformat() if row["computed_at"] else None,
    }


@router.get("/clusters")
async def get_clusters(db: AsyncSession = Depends(get_db)):
    """
    Clusters that have actually been computed.

    This used to return a hardcoded list of nine labels whether or not any
    clustering had ever run, so the UI showed nine populated categories over an
    empty table.
    """
    rows = (
        await db.execute(
            text(
                """SELECT cluster_label, COUNT(*) AS members
                     FROM cluster_results
                    GROUP BY cluster_label
                    ORDER BY members DESC"""
            )
        )
    ).mappings().all()

    if not rows:
        return {
            "clusters": [],
            "computed": False,
            "reason": "Clustering has not been run. No cluster results are stored.",
        }
    return {
        "clusters": [
            {"label": r["cluster_label"], "members": r["members"]} for r in rows
        ],
        "computed": True,
    }


@router.get("/clusters/{label}")
async def get_cluster(label: str, db: AsyncSession = Depends(get_db)):
    """Members of one cluster, with their current signal."""
    rows = (
        await db.execute(
            text(
                """SELECT c.nse_symbol, s.company_name, sg.action, sg.composite_score,
                          iv.upside_pct
                     FROM cluster_results c
                     JOIN stocks s  ON s.nse_symbol = c.nse_symbol
                LEFT JOIN signals sg ON sg.nse_symbol = c.nse_symbol
                LEFT JOIN intrinsic_values iv ON iv.nse_symbol = c.nse_symbol
                    WHERE c.cluster_label = :label
                    ORDER BY sg.composite_score DESC NULLS LAST
                    LIMIT 200"""
            ),
            {"label": label},
        )
    ).mappings().all()

    return {
        "label": label,
        "count": len(rows),
        "stocks": [
            {
                "symbol": r["nse_symbol"],
                "name": r["company_name"],
                "action": r["action"],
                "composite_score": _as_float(r["composite_score"]),
                "upside_pct": _as_float(r["upside_pct"]),
            }
            for r in rows
        ],
    }


# ─────────────────────────────────────────────────────────────
# Pipeline control — admin only, POST only
# ─────────────────────────────────────────────────────────────
@router.post("/pipeline/run", status_code=202, dependencies=[Depends(require_admin)])
async def run_pipeline(
    background_tasks: BackgroundTasks,
    symbols: str | None = Query(
        default=None,
        description="Comma-separated symbols. Omit to run the whole universe.",
    ),
    skip_history: bool = Query(default=False),
    skip_fundamentals: bool = Query(default=False),
):
    """
    Run the real ingestion and computation pipeline.

    Fetches live prices, price history and filings from the data provider, then
    computes technicals, intrinsic values and signals from them. Nothing is
    generated; if the provider has no data for a symbol, that symbol is recorded
    as a failure in ``ingest_runs`` and skipped.

    A POST because it mutates, and admin-guarded because it costs real provider
    quota and rewrites every valuation in the database.
    """
    wanted = (
        [s.strip().upper() for s in symbols.split(",") if s.strip()]
        if symbols
        else None
    )

    async def task() -> None:
        import asyncpg

        from services.ingest_service import run_full_pipeline

        database_url = settings.DATABASE_SYNC_URL.replace(
            "postgresql+asyncpg://", "postgresql://"
        ).replace("postgresql+psycopg2://", "postgresql://")
        pool = await asyncpg.create_pool(database_url, min_size=2, max_size=8)
        try:
            reports = await run_full_pipeline(
                pool,
                symbols=wanted,
                years=settings.HISTORY_YEARS,
                skip_history=skip_history or not settings.INGEST_HISTORY_ENABLED,
                skip_fundamentals=skip_fundamentals
                or not settings.INGEST_FUNDAMENTALS_ENABLED,
            )
            log.info("pipeline finished: %s", reports)
        except Exception:
            log.exception("pipeline failed")
        finally:
            await pool.close()

    background_tasks.add_task(task)
    return {
        "status": "queued",
        "symbols": wanted or "all",
        "message": "Pipeline started. Track progress in the ingest_runs table.",
    }


@router.get("/pipeline/runs")
async def pipeline_runs(limit: int = 20, db: AsyncSession = Depends(get_db)):
    """Recent pipeline runs, so a failed overnight job can be diagnosed."""
    limit = max(1, min(limit, 200))
    rows = (
        await db.execute(
            text(
                """SELECT job, status, requested, succeeded, failed, skipped,
                          rows_written, started_at, finished_at, duration_seconds,
                          errors
                     FROM ingest_runs
                    ORDER BY started_at DESC
                    LIMIT :limit"""
            ),
            {"limit": limit},
        )
    ).mappings().all()

    return {
        "runs": [
            {
                "job": r["job"],
                "status": r["status"],
                "requested": r["requested"],
                "succeeded": r["succeeded"],
                "failed": r["failed"],
                "skipped": r["skipped"],
                "rows_written": r["rows_written"],
                "started_at": r["started_at"].isoformat() if r["started_at"] else None,
                "finished_at": (
                    r["finished_at"].isoformat() if r["finished_at"] else None
                ),
                "duration_seconds": _as_float(r["duration_seconds"]),
                "errors": r["errors"],
            }
            for r in rows
        ]
    }


@router.post(
    "/universe/import-nse", status_code=202, dependencies=[Depends(require_admin)]
)
async def import_nse_universe(background_tasks: BackgroundTasks):
    """
    Import the official NSE equity list.

    If the download fails, this **fails**. The previous implementation fell back
    to inventing 2,700 companies with a placeholder ISIN and inserting them as
    active tradeable equities — a fabricated universe is far worse than an empty
    one, because nothing downstream can tell the difference.
    """

    async def task() -> None:
        from services.universe_service import import_nse_equity_list

        try:
            report = await import_nse_equity_list()
            log.info("NSE universe import: %s", report)
        except Exception:
            log.exception(
                "NSE universe import failed. No symbols were invented; "
                "the stock table is unchanged."
            )

    background_tasks.add_task(task)
    return {
        "status": "queued",
        "message": "Importing the NSE equity list. On failure nothing is written.",
    }


@router.get("/health/data")
async def data_health(db: AsyncSession = Depends(get_db)):
    """
    How much of the universe actually has real data behind it.

    Exists so an operator can see coverage at a glance instead of discovering a
    gap when a customer opens an empty stock page.
    """
    row = (
        await db.execute(
            text(
                """SELECT
                     (SELECT count(*) FROM stocks WHERE is_active) AS active_stocks,
                     (SELECT count(DISTINCT nse_symbol) FROM price_candles_daily) AS with_history,
                     (SELECT count(DISTINCT nse_symbol) FROM financial_results
                       WHERE period_type = 'A') AS with_filings,
                     (SELECT count(*) FROM intrinsic_values
                       WHERE iv_blended IS NOT NULL) AS with_valuation,
                     (SELECT count(*) FROM signals
                       WHERE action IS NOT NULL) AS with_signal,
                     (SELECT count(*) FROM realtime_quotes
                       WHERE last_updated > NOW() - INTERVAL '1 day') AS fresh_quotes,
                     (SELECT count(*) FROM intrinsic_values
                       WHERE years_of_history >= 10) AS ten_year_histories"""
            )
        )
    ).mappings().first()

    active = row["active_stocks"] or 0

    def share(n: int | None) -> float | None:
        return None if not active else round((n or 0) / active * 100, 1)

    return {
        "active_stocks": active,
        "coverage": {
            "price_history": {"count": row["with_history"], "pct": share(row["with_history"])},
            "annual_filings": {"count": row["with_filings"], "pct": share(row["with_filings"])},
            "valuation": {"count": row["with_valuation"], "pct": share(row["with_valuation"])},
            "signal": {"count": row["with_signal"], "pct": share(row["with_signal"])},
            "fresh_quotes": {"count": row["fresh_quotes"], "pct": share(row["fresh_quotes"])},
            "full_ten_year_history": {
                "count": row["ten_year_histories"],
                "pct": share(row["ten_year_histories"]),
            },
        },
    }


def _as_float(value) -> float | None:
    return None if value is None else float(value)
