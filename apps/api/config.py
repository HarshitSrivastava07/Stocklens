"""
StockLens — Application Configuration
All settings loaded from environment variables via .env file
"""
from typing import List
from pydantic_settings import BaseSettings, SettingsConfigDict

import os
from pathlib import Path

# Compute absolute path to project root .env file (2 levels up from apps/api/config.py)
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
ENV_PATH = ROOT_DIR / ".env"

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ENV_PATH),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── App ────────────────────────────────────────────────────
    APP_ENV: str = "development"
    APP_NAME: str = "StockLens"
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000
    LOG_LEVEL: str = "INFO"
    PRODUCTION_MODE: bool = False

    # ── Database ───────────────────────────────────────────────
    DATABASE_URL: str = "postgresql+asyncpg://postgres:password@localhost:5432/stocklens"
    DATABASE_SYNC_URL: str = "postgresql://postgres:password@localhost:5432/stocklens"
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20
    DB_POOL_TIMEOUT: int = 30

    # ── Redis ──────────────────────────────────────────────────
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_PRICE_TTL: int = 10          # seconds — latest price cache
    REDIS_AI_CACHE_TTL: int = 14400    # 4 hours — AI summary cache
    REDIS_RATIO_TTL: int = 3600        # 1 hour — ratio cache

    # ── Upstox ────────────────────────────────────────────────
    UPSTOX_API_KEY: str = ""
    UPSTOX_API_SECRET: str = ""
    UPSTOX_REDIRECT_URI: str = "http://localhost:8000/api/v1/auth/upstox/callback"
    UPSTOX_ACCESS_TOKEN: str = ""
    UPSTOX_REFRESH_TOKEN: str = ""

    # ── Google Gemini AI ──────────────────────────────────────
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL_FAST: str = "gemini-1.5-flash"       # for volume
    GEMINI_MODEL_QUALITY: str = "gemini-1.5-pro"      # for deep analysis
    AI_MAX_OUTPUT_TOKENS: int = 1000
    AI_CACHE_TTL: int = 14400                          # 4 hours

    # ── Scheduler ─────────────────────────────────────────────
    SCHEDULER_ENABLED: bool = True
    TIMEZONE: str = "Asia/Kolkata"

    # ── ML ────────────────────────────────────────────────────
    ML_N_CLUSTERS: int = 9
    ML_MIN_DATA_COMPLETENESS: float = 0.6
    ML_MODELS_DIR: str = "apps/ml/models"

    # ── CORS (local only) ─────────────────────────────────────
    # Restricts to localhost origins — change for production
    CORS_ORIGINS: List[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:3001",
    ]

    # ── Rate Limiting (soft limits for AI — local use) ────────
    AI_RATE_LIMIT_PER_MIN: int = 10
    VALUATION_RATE_LIMIT_PER_MIN: int = 5

    # ── Admin access ──────────────────────────────────────────
    # Guards every mutating and operational endpoint. When empty those
    # endpoints refuse all requests rather than allowing them: an operator who
    # has not set a token has not decided that they should be public.
    ADMIN_API_TOKEN: str = ""

    # ── Feature flags ─────────────────────────────────────────
    # Every engine added on top of the original codebase can be switched off
    # here without touching code or reverting a commit. A flag set to false
    # makes the corresponding stage a no-op and leaves whatever the previous
    # implementation wrote in place, so a bad overnight run can be contained
    # from the environment rather than from a redeploy.
    #
    # scripts/revert_change.py names the flag for any change that has one.
    ENGINE_TECHNICALS_ENABLED: bool = True    # indicator snapshots
    ENGINE_VALUATION_ENABLED: bool = True     # 10-year intrinsic value
    ENGINE_SIGNALS_ENABLED: bool = True       # buy/sell decisions
    ENGINE_REVERSE_DCF_ENABLED: bool = True   # implied-growth solve

    INGEST_HISTORY_ENABLED: bool = True       # daily OHLCV backfill
    INGEST_FUNDAMENTALS_ENABLED: bool = True  # annual/quarterly filings
    INGEST_QUOTES_ENABLED: bool = True        # live price polling

    # Years of price history to retain and request.
    HISTORY_YEARS: int = 10
    # Years of filings to request. Asking wider than needed costs nothing and
    # the provider returns only what it actually has.
    FUNDAMENTALS_YEARS: int = 12
    # Minimum annual filings before a valuation will be published at all.
    VALUATION_MIN_YEARS: int = 4

    # Outbound request budget for the data provider, per second.
    PROVIDER_RATE_PER_SEC: float = 5.0
    PROVIDER_BURST: int = 10
    PROVIDER_CONCURRENCY: int = 6

    # ── Data ──────────────────────────────────────────────────
    # NSE stale data threshold (minutes)
    PRICE_STALE_THRESHOLD_MIN: int = 5
    # Max rows per admin CSV import
    CSV_MAX_ROWS: int = 10000
    # Max file size for uploads (bytes) — 25MB
    MAX_UPLOAD_SIZE_BYTES: int = 26_214_400


# Global settings instance
settings = Settings()
