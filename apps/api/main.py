"""
StockLens — FastAPI Application Entry Point
Personal NSE Stock Intelligence Platform
"""
import logging
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from dependencies.db import engine, Base
from dependencies.redis import get_redis_client
from config import settings

# Routers
from routers import (
    market,
    stocks,
    valuation,
    ml as ml_router,
    ai as ai_router,
    screener,
    watchlist,
    alerts,
    portfolio,
    sectors,
    backtest,
    admin,
    ws,
    fo,
    auth,
)
from services.scheduler_service import start_scheduler, stop_scheduler

# ─────────────────────────────────────────────────────────────
# Structured logging
# ─────────────────────────────────────────────────────────────
structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.stdlib.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.dev.ConsoleRenderer() if settings.APP_ENV == "development"
        else structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.BoundLogger,
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
)

log = structlog.get_logger()


# ─────────────────────────────────────────────────────────────
# App Lifespan (startup / shutdown)
# ─────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown hooks."""
    log.info("StockLens API starting up", env=settings.APP_ENV)

    # Create DB tables if not exist (migrations should handle this,
    # but this is a safety net for dev mode)
    from sqlalchemy import text
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Apply global stock universe columns migration
        await conn.execute(text("ALTER TABLE stocks DROP CONSTRAINT IF EXISTS stocks_isin_key;"))
        await conn.execute(text("ALTER TABLE stocks ADD COLUMN IF NOT EXISTS exchange VARCHAR(20) NOT NULL DEFAULT 'NSE';"))
        await conn.execute(text("ALTER TABLE stocks ADD COLUMN IF NOT EXISTS currency VARCHAR(5) NOT NULL DEFAULT 'INR';"))
        await conn.execute(text("ALTER TABLE stocks ADD COLUMN IF NOT EXISTS yahoo_ticker VARCHAR(30);"))
        await conn.execute(text("ALTER TABLE stocks ADD COLUMN IF NOT EXISTS country VARCHAR(50) NOT NULL DEFAULT 'India';"))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS idx_stocks_exchange ON stocks(exchange);"))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS idx_stocks_country ON stocks(country);"))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS idx_stocks_yahoo ON stocks(yahoo_ticker);"))

    # Verify Redis connection
    try:
        redis = await get_redis_client()
        await redis.ping()
        log.info("Redis connection OK")
    except Exception as e:
        log.error("Redis connection failed", error=str(e))

    # Start background scheduler
    if settings.SCHEDULER_ENABLED:
        start_scheduler()
        log.info("Background scheduler started")

    yield

    # Shutdown
    log.info("StockLens API shutting down")
    if settings.SCHEDULER_ENABLED:
        stop_scheduler()
    await engine.dispose()


# ─────────────────────────────────────────────────────────────
# FastAPI App
# ─────────────────────────────────────────────────────────────
app = FastAPI(
    title="StockLens API",
    description="NSE Stock Intelligence Platform — Personal Research Tool",
    version="1.0.0",
    docs_url="/docs" if settings.APP_ENV == "development" else None,
    redoc_url="/redoc" if settings.APP_ENV == "development" else None,
    lifespan=lifespan,
)


# ─────────────────────────────────────────────────────────────
# CORS — local only (no wildcard in production)
# ─────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Request-ID"],
)


# ─────────────────────────────────────────────────────────────
# Security Headers Middleware
# ─────────────────────────────────────────────────────────────
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-Request-ID"] = request.headers.get("X-Request-ID", "")
    # Remove server fingerprint
    if "server" in response.headers:
        del response.headers["server"]
    return response


# ─────────────────────────────────────────────────────────────
# Global Error Handler — never expose stack traces
# ─────────────────────────────────────────────────────────────
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    log.error(
        "Unhandled exception",
        path=request.url.path,
        method=request.method,
        error=str(exc),
        error_type=type(exc).__name__,
    )
    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal server error",
            "message": "An unexpected error occurred. Please try again.",
        },
    )


# ─────────────────────────────────────────────────────────────
# Routers
# ─────────────────────────────────────────────────────────────
API_PREFIX = "/api/v1"

app.include_router(market.router,    prefix=f"{API_PREFIX}/market",    tags=["Market Data"])
app.include_router(stocks.router,    prefix=f"{API_PREFIX}/stocks",    tags=["Stocks"])
app.include_router(valuation.router, prefix=f"{API_PREFIX}/valuation", tags=["Valuation"])
app.include_router(ml_router.router, prefix=f"{API_PREFIX}/ml",        tags=["ML"])
app.include_router(ai_router.router, prefix=f"{API_PREFIX}/ai",        tags=["AI"])
app.include_router(screener.router,  prefix=f"{API_PREFIX}/screener",  tags=["Screener"])
app.include_router(watchlist.router, prefix=f"{API_PREFIX}/watchlist", tags=["Watchlist"])
app.include_router(alerts.router,    prefix=f"{API_PREFIX}/alerts",    tags=["Alerts"])
app.include_router(portfolio.router, prefix=f"{API_PREFIX}/portfolio", tags=["Portfolio"])
app.include_router(sectors.router,   prefix=f"{API_PREFIX}/sectors",   tags=["Sectors"])
app.include_router(backtest.router,  prefix=f"{API_PREFIX}/backtest",  tags=["Backtest"])
app.include_router(admin.router,     prefix=f"{API_PREFIX}/admin",     tags=["Admin"])
app.include_router(fo.router,        prefix=f"{API_PREFIX}/fo",        tags=["F&O"])
app.include_router(ws.router,        prefix="/api/v1",                  tags=["WebSocket"])
app.include_router(auth.router,      prefix=f"{API_PREFIX}",           tags=["Auth"])


# ─────────────────────────────────────────────────────────────
# Health Check
# ─────────────────────────────────────────────────────────────
@app.get("/health", tags=["Health"])
async def health_check():
    return {
        "status": "ok",
        "app": "StockLens",
        "version": "1.0.0",
        "env": settings.APP_ENV,
    }


@app.get("/", tags=["Root"])
async def root():
    return {
        "message": "StockLens API",
        "docs": "/docs",
        "health": "/health",
    }
