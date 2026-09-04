"""
Scheduler Service — APScheduler jobs for daily data pipeline
All times are in IST (Asia/Kolkata)
"""
import logging
from datetime import datetime

import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

log = logging.getLogger("scheduler")
IST = pytz.timezone("Asia/Kolkata")

_scheduler: AsyncIOScheduler | None = None


def start_scheduler():
    global _scheduler
    _scheduler = AsyncIOScheduler(timezone=IST)

    # ── Market hours jobs ─────────────────────────────────────
    _scheduler.add_job(
        job_pre_market_check, CronTrigger(hour=9, minute=0, timezone=IST),
        id="pre_market_check", replace_existing=True,
    )
    _scheduler.add_job(
        job_eod_snapshot, CronTrigger(hour=15, minute=35, timezone=IST),
        id="eod_snapshot", replace_existing=True,
    )

    # ── Post-market data pipeline ─────────────────────────────
    _scheduler.add_job(
        job_corporate_actions, CronTrigger(hour=16, minute=0, timezone=IST),
        id="corporate_actions", replace_existing=True,
    )
    _scheduler.add_job(
        job_fundamentals_refresh, CronTrigger(hour=17, minute=0, timezone=IST),
        id="fundamentals_refresh", replace_existing=True,
    )
    _scheduler.add_job(
        job_ratio_engine, CronTrigger(hour=18, minute=0, timezone=IST),
        id="ratio_engine", replace_existing=True,
    )
    _scheduler.add_job(
        job_valuation_engine, CronTrigger(hour=19, minute=0, timezone=IST),
        id="valuation_engine", replace_existing=True,
    )
    _scheduler.add_job(
        job_signal_engine, CronTrigger(hour=20, minute=0, timezone=IST),
        id="signal_engine", replace_existing=True,
    )
    _scheduler.add_job(
        job_alert_evaluation, CronTrigger(hour=20, minute=30, timezone=IST),
        id="alert_evaluation", replace_existing=True,
    )
    _scheduler.add_job(
        job_ai_summaries, CronTrigger(hour=21, minute=0, timezone=IST),
        id="ai_summaries", replace_existing=True,
    )

    # ── Weekly jobs ───────────────────────────────────────────
    _scheduler.add_job(
        job_ml_clustering, CronTrigger(day_of_week="sun", hour=23, minute=0, timezone=IST),
        id="ml_clustering", replace_existing=True,
    )
    _scheduler.add_job(
        job_ml_retrain, CronTrigger(day_of_week="sun", hour=2, minute=0, timezone=IST),
        id="ml_retrain", replace_existing=True,
    )

    # ── Cleanup ───────────────────────────────────────────────
    _scheduler.add_job(
        job_cleanup_old_candles, CronTrigger(hour=3, minute=0, timezone=IST),
        id="cleanup", replace_existing=True,
    )

    _scheduler.start()
    log.info("Scheduler started with all jobs")


def stop_scheduler():
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        log.info("Scheduler stopped")


# ─────────────────────────────────────────────────────────────
# Job Implementations
# ─────────────────────────────────────────────────────────────
async def job_pre_market_check():
    log.info("PRE-MARKET CHECK: Verifying data feed availability")
    # TODO: ping Upstox API, verify Redis connectivity, log status


async def job_eod_snapshot():
    log.info("EOD SNAPSHOT: Capturing closing prices from realtime_quotes → price_candles_daily")
    # TODO: copy ltp from realtime_quotes to price_candles_daily for today


async def job_corporate_actions():
    log.info("CORPORATE ACTIONS: Fetching from BSE XML")
    # TODO: fetch BSE corporate actions XML, parse, store


async def job_fundamentals_refresh():
    log.info("FUNDAMENTALS REFRESH: Fetching BSE quarterly results XML")
    # TODO: download BSE XML feeds, parse, upsert financial_results


async def job_ratio_engine():
    log.info("RATIO ENGINE: Computing all ratios for all stocks")
    from services.ratio_service import run_ratio_engine
    await run_ratio_engine()


async def job_valuation_engine():
    log.info("VALUATION ENGINE: Running sector-specific models")
    from services.valuation_service import run_valuation_engine
    await run_valuation_engine()


async def job_signal_engine():
    log.info("SIGNAL ENGINE: Updating Green/Yellow/Red/Grey signals")
    from services.signal_service import run_signal_engine
    await run_signal_engine()


async def job_alert_evaluation():
    log.info("ALERTS: Evaluating all user alert conditions")
    from services.alert_service import run_alert_evaluation
    await run_alert_evaluation()


async def job_ai_summaries():
    log.info("AI SUMMARIES: Refreshing stale AI reports where data changed")
    # Only refresh if data_version changed since last generation
    # TODO: query ai_reports where expires_at < now(), regenerate in background


async def job_ml_clustering():
    log.info("ML CLUSTERING: Running KMeans + DBSCAN clustering")
    # TODO: load features, run clustering, write cluster_results


async def job_ml_retrain():
    log.info("ML RETRAIN: Retraining XGBoost risk + LightGBM confidence models")
    # TODO: load historical data, retrain models, version models


async def job_cleanup_old_candles():
    log.info("CLEANUP: Removing 1-minute candles older than 30 days")
    # TODO: DELETE FROM price_candles_1m WHERE ts < NOW() - INTERVAL '30 days'


# ─────────────────────────────────────────────────────────────
# Manual trigger helper (used by admin API)
# ─────────────────────────────────────────────────────────────
JOB_MAP = {
    "pre_market_check": job_pre_market_check,
    "eod_snapshot": job_eod_snapshot,
    "fundamentals_refresh": job_fundamentals_refresh,
    "ratio_engine": job_ratio_engine,
    "valuation_engine": job_valuation_engine,
    "signal_engine": job_signal_engine,
    "alert_evaluation": job_alert_evaluation,
    "ai_summaries": job_ai_summaries,
    "ml_clustering": job_ml_clustering,
    "ml_retrain": job_ml_retrain,
    "cleanup": job_cleanup_old_candles,
}


async def trigger_job(job_name: str):
    fn = JOB_MAP.get(job_name)
    if not fn:
        raise ValueError(f"Unknown job: {job_name}")
    await fn()
