"""
1-Minute Candle Builder
Aggregates real-time ticks into OHLCV candles and writes to PostgreSQL.
"""
import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

import asyncpg
import os

log = logging.getLogger("candle_builder")

# Candle state: symbol -> current open candle
_candles: dict[str, dict] = {}
_candle_lock = asyncio.Lock()


class CandleBuilder:
    """Stateful 1-minute candle aggregator."""

    def __init__(self, redis_client):
        self.redis = redis_client
        self._db_pool: Optional[asyncpg.Pool] = None
        self._ensure_pool_task = None

    async def _get_pool(self) -> asyncpg.Pool:
        if self._db_pool is None:
            db_url = os.environ["DATABASE_URL"].replace("+asyncpg", "")
            self._db_pool = await asyncpg.create_pool(
                db_url,
                min_size=2,
                max_size=5,
                command_timeout=10,
            )
        return self._db_pool

    def _candle_minute_key(self, ts: datetime) -> str:
        """Round timestamp down to minute boundary."""
        return ts.strftime("%Y-%m-%dT%H:%M:00")

    async def on_tick(self, symbol: str, tick: dict):
        """Process incoming tick — build or extend current candle."""
        ltp = tick.get("ltp")
        volume = tick.get("volume", 0)
        if not ltp:
            return

        now = datetime.now(timezone.utc)
        minute_key = self._candle_minute_key(now)

        async with _candle_lock:
            current = _candles.get(symbol)

            if current is None or current["minute"] != minute_key:
                # Close previous candle and write to DB
                if current is not None:
                    await self._write_candle(symbol, current)

                # Start new candle
                _candles[symbol] = {
                    "minute": minute_key,
                    "ts": now,
                    "open": ltp,
                    "high": ltp,
                    "low": ltp,
                    "close": ltp,
                    "volume": volume,
                    "tick_count": 1,
                }
            else:
                # Update existing candle
                c = _candles[symbol]
                c["high"] = max(c["high"], ltp)
                c["low"] = min(c["low"], ltp)
                c["close"] = ltp
                c["volume"] = volume  # Upstox gives cumulative volume
                c["tick_count"] += 1

    async def _write_candle(self, symbol: str, candle: dict):
        """Write completed candle to PostgreSQL."""
        try:
            pool = await self._get_pool()
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO price_candles_1m
                        (nse_symbol, ts, open, high, low, close, volume)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    ON CONFLICT DO NOTHING
                    """,
                    symbol,
                    candle["ts"],
                    candle["open"],
                    candle["high"],
                    candle["low"],
                    candle["close"],
                    candle["volume"],
                )
            log.debug(f"Candle written: {symbol} @ {candle['minute']}")
        except Exception as e:
            log.error(f"Failed to write candle {symbol}: {e}")
