"""
StockLens — Yahoo Finance Price Poller
Polls real-time (15-min delayed) prices for global stocks every 60 seconds.

Flow:
  PostgreSQL stocks (where yahoo_ticker IS NOT NULL)
  → yfinance batch download (50 tickers / call)
  → PostgreSQL realtime_quotes UPSERT
  → Redis latest:{symbol} SET + ticks:dashboard PUBLISH

Run standalone:
    python apps/worker/yahoo_price_fetcher.py

Or imported by upstox_ws.py as a concurrent asyncio task.
"""
import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import asyncpg
import redis.asyncio as aioredis
import yfinance as yf
from dotenv import load_dotenv

_ROOT_ENV = Path(__file__).resolve().parent.parent.parent / ".env"
load_dotenv(dotenv_path=_ROOT_ENV, override=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
log = logging.getLogger("yahoo_poller")

REDIS_URL = os.environ["REDIS_URL"]
DB_URL = os.environ.get("DATABASE_SYNC_URL", "postgresql://postgres:password@localhost:5432/stocklens")

POLL_INTERVAL    = 60    # seconds between full polls
BATCH_SIZE       = 50    # Yahoo Finance handles ~50 tickers per call efficiently
REDIS_TTL        = 120   # seconds — expire key if not updated
MAX_WORKERS      = 4     # concurrent batches


class YahooPriceFetcher:
    """
    Fetches real-time prices from Yahoo Finance for all global stocks
    (those that have a yahoo_ticker set) and writes them to Redis + DB.
    """

    def __init__(self, redis_client: aioredis.Redis, db_pool: asyncpg.Pool):
        self.redis = redis_client
        self.db = db_pool
        self.running = False
        self._symbols: list[dict] = []   # [{nse_symbol, yahoo_ticker, currency, exchange}]
        self._headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept":          "application/json",
            "Accept-Language": "en-US,en;q=0.9",
        }

    async def load_symbols(self):
        """Load all stocks with a yahoo_ticker from the database."""
        rows = await self.db.fetch(
            """SELECT nse_symbol, yahoo_ticker, currency, exchange, country
               FROM stocks
               WHERE yahoo_ticker IS NOT NULL AND is_active = TRUE
               ORDER BY exchange, nse_symbol"""
        )
        self._symbols = [dict(r) for r in rows]
        log.info(f"Loaded {len(self._symbols)} yahoo-trackable symbols")

    def _make_batches(self) -> list[list[dict]]:
        """Split symbol list into BATCH_SIZE chunks."""
        return [
            self._symbols[i:i + BATCH_SIZE]
            for i in range(0, len(self._symbols), BATCH_SIZE)
        ]

    async def _get_crumb(self, client) -> str | None:
        """Obtain Yahoo Finance session crumb. Must be called once per session."""
        try:
            # Warm up cookies
            await client.get(
                "https://finance.yahoo.com/",
                headers={**self._headers, "Accept": "text/html"},
                timeout=10,
            )
            r = await client.get(
                "https://query1.finance.yahoo.com/v1/test/getcrumb",
                headers={**self._headers, "Accept": "text/plain"},
                timeout=10,
            )
            if r.status_code == 200 and r.text:
                crumb = r.text.strip()
                log.info(f"Yahoo crumb acquired: {crumb!r}")
                return crumb
        except Exception as e:
            log.error(f"Crumb fetch error: {e}")
        return None

    async def _fetch_single(self, client, meta: dict, crumb: str) -> dict | None:
        """Fetch price for a single ticker via Yahoo Finance v8 chart API."""
        yahoo_ticker = meta["yahoo_ticker"]
        url = (
            f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_ticker}"
            f"?interval=1d&range=5d&crumb={crumb}"
        )

        try:
            response = await client.get(url, headers=self._headers, timeout=10)
            if response.status_code == 429:
                log.warning(f"Rate limited for {yahoo_ticker} — retrying after 5s")
                await asyncio.sleep(5)
                response = await client.get(url, headers=self._headers, timeout=10)
            if response.status_code != 200:
                log.debug(f"Yahoo API error for {yahoo_ticker}: {response.status_code}")
                return None

            text = response.text
            if not text or text.strip() == "":
                log.debug(f"Empty response for {yahoo_ticker}")
                return None

            data = response.json()
            result = data.get("chart", {}).get("result", [])
            if not result:
                return None

            chart_data = result[0]
            meta_info = chart_data.get("meta", {})
            ltp = meta_info.get("regularMarketPrice")

            if not ltp or ltp <= 0:
                return None

            # Yahoo's v8 chart `meta` often returns previousClose/regularMarketOpen
            # as null — chartPreviousClose is reliable; fall back to the most
            # recent day in the indicator arrays, then to ltp.
            indicators = chart_data.get("indicators", {}).get("quote", [{}])[0]

            def _last(key):
                vals = [v for v in indicators.get(key, []) if v is not None]
                return vals[-1] if vals else None

            prev_close = (
                meta_info.get("previousClose")
                or meta_info.get("chartPreviousClose")
                or ltp
            )
            change_abs = ltp - prev_close
            change_pct = (change_abs / prev_close) * 100 if prev_close else 0.0

            open_price = meta_info.get("regularMarketOpen") or _last("open") or ltp
            high_price = meta_info.get("regularMarketDayHigh") or _last("high") or ltp
            low_price = meta_info.get("regularMarketDayLow") or _last("low") or ltp
            vol = meta_info.get("regularMarketVolume") or _last("volume") or 0

            return {
                "symbol":      meta["nse_symbol"],
                "yahoo_ticker": yahoo_ticker,
                "exchange":    meta["exchange"],
                "currency":    meta["currency"],
                "ltp":         round(float(ltp), 4),
                "open":        round(float(open_price), 4),
                "high":        round(float(high_price), 4),
                "low":         round(float(low_price), 4),
                "close":       round(float(prev_close), 4),
                "volume":      int(vol),
                "change_abs":  round(float(change_abs), 4),
                "change_pct":  round(float(change_pct), 4),
                "week_52_high": round(float(meta_info.get("fiftyTwoWeekHigh", ltp)), 4),
                "week_52_low":  round(float(meta_info.get("fiftyTwoWeekLow", ltp)), 4),
                "market_cap":   float(meta_info.get("marketCap", 0) or 0),
                "data_source": "YAHOO",
                "ts":          datetime.now(timezone.utc).isoformat(),
            }
        except Exception as e:
            log.debug(f"Error fetching {yahoo_ticker}: {e}")
            return None

    async def run_once(self):
        """Run one full poll cycle across all symbols concurrently."""
        await self.load_symbols()
        if not self._symbols:
            log.warning("No symbols to poll — run seed_global_stocks.py first")
            return

        import httpx
        log.info(f"Polling {len(self._symbols)} symbols via Yahoo v8 Chart API...")

        now = datetime.now(timezone.utc)
        updates_for_dashboard = []
        db_updates = []

        # Conservative concurrency to avoid Yahoo rate-limiting
        semaphore = asyncio.Semaphore(8)

        async with httpx.AsyncClient(follow_redirects=True) as client:
            # Authenticate once per poll cycle
            crumb = await self._get_crumb(client)
            if not crumb:
                log.error("Could not get Yahoo crumb — skipping this poll cycle")
                return

            async def _guarded_fetch(meta):
                async with semaphore:
                    return await self._fetch_single(client, meta, crumb)

            tasks   = [_guarded_fetch(meta) for meta in self._symbols]
            results = await asyncio.gather(*tasks)

        for tick in results:
            if not tick:
                continue

            tick_json = json.dumps(tick)
            nse_sym = tick["symbol"]

            # Redis: latest price key
            await self.redis.setex(f"latest:{nse_sym}", REDIS_TTL, tick_json)
            # Redis: per-symbol pub/sub channel
            await self.redis.publish(f"ticks:{nse_sym}", tick_json)

            updates_for_dashboard.append(tick)
            db_updates.append((
                nse_sym, tick["ltp"], tick["open"], tick["high"], tick["low"],
                tick["close"], tick["change_abs"], tick["change_pct"],
                tick["volume"], tick["week_52_high"], tick["week_52_low"],
                tick["market_cap"], "YAHOO",
            ))

        # Batch DB upsert
        if db_updates:
            await self.db.executemany(
                """INSERT INTO realtime_quotes
                       (nse_symbol, ltp, open, high, low, close,
                        change_abs, change_pct, volume,
                        week_52_high, week_52_low, market_cap,
                        data_source, is_stale, last_updated)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,FALSE,NOW())
                   ON CONFLICT (nse_symbol) DO UPDATE
                     SET ltp=$2, open=$3, high=$4, low=$5, close=$6,
                         change_abs=$7, change_pct=$8, volume=$9,
                         week_52_high=$10, week_52_low=$11, market_cap=$12,
                         data_source=$13, is_stale=FALSE, last_updated=NOW()""",
                db_updates,
            )

        # Publish batch dashboard update
        if updates_for_dashboard:
            batch_msg = json.dumps({
                "type": "batch",
                "source": "YAHOO",
                "updates": updates_for_dashboard,
                "ts": now.isoformat(),
            })
            await self.redis.publish("ticks:dashboard", batch_msg)

        log.info(f"Poll cycle complete: {len(db_updates)}/{len(self._symbols)} succeeded")

    async def run(self):
        """Continuous polling loop."""
        self.running = True
        log.info(f"Yahoo price poller started — polling every {POLL_INTERVAL}s")

        while self.running:
            start = asyncio.get_event_loop().time()
            try:
                await self.run_once()
            except Exception as e:
                log.error(f"Poll cycle error: {e}")

            elapsed = asyncio.get_event_loop().time() - start
            wait = max(0, POLL_INTERVAL - elapsed)
            log.info(f"Poll cycle done in {elapsed:.1f}s — next poll in {wait:.0f}s")
            await asyncio.sleep(wait)

    def stop(self):
        self.running = False


# ─────────────────────────────────────────────────────────────
# Standalone entry point
# ─────────────────────────────────────────────────────────────
async def create_db_pool() -> asyncpg.Pool:
    db_url = DB_URL.replace("postgresql+asyncpg://", "postgresql://").replace("postgresql+psycopg2://", "postgresql://")
    return await asyncpg.create_pool(db_url, min_size=2, max_size=6)


async def main():
    redis = aioredis.from_url(REDIS_URL, encoding="utf-8", decode_responses=True)
    pool = await create_db_pool()
    fetcher = YahooPriceFetcher(redis, pool)

    try:
        await fetcher.run()
    except (KeyboardInterrupt, asyncio.CancelledError):
        log.info("Shutting down...")
    finally:
        fetcher.stop()
        await redis.aclose()
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
