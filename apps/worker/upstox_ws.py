"""
StockLens — Upstox WebSocket Worker
Real-time market data ingestor for NSE stocks.

Flow:
  Upstox WebSocket → validate tick → Redis latest:{symbol} + Pub/Sub
                  → candle_builder.py → PostgreSQL price_candles_1m
"""
import asyncio
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

import redis.asyncio as aioredis
import upstox_client
import upstox_client.feeder.market_data_feeder
from dotenv import load_dotenv

from candle_builder import CandleBuilder
from token_manager import ensure_valid_token

# ── Load env ──────────────────────────────────────────────────
# .env is at project root (3 levels up from apps/worker/)
_ROOT_ENV = Path(__file__).resolve().parent.parent.parent / ".env"
load_dotenv(dotenv_path=_ROOT_ENV, override=True)


# ── Logging ───────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
log = logging.getLogger("upstox_worker")

# ── Config ────────────────────────────────────────────────────
REDIS_URL = os.environ["REDIS_URL"]
UPSTOX_ACCESS_TOKEN = os.environ.get("UPSTOX_ACCESS_TOKEN", "")
PRICE_STALE_TTL = 30           # seconds — expire Redis key if no update
RECONNECT_BASE_DELAY = 1       # seconds
RECONNECT_MAX_DELAY = 60       # seconds
DASHBOARD_BATCH_INTERVAL = 0.5  # seconds — batch dashboard updates

# Symbols to subscribe — load from DB or config
# For now, load from environment or default to major indices + stocks
SUBSCRIBE_SYMBOLS_FILE = Path(__file__).parent / "subscribed_symbols.json"


async def load_subscribed_symbols() -> list[str]:
    """Load symbols to subscribe from JSON file or return defaults."""
    if SUBSCRIBE_SYMBOLS_FILE.exists():
        with open(SUBSCRIBE_SYMBOLS_FILE) as f:
            return json.load(f)
    # Default: major Nifty 50 symbols (update as you import more stocks)
    return [
        "NSE_EQ|INE467B01029",  # TCS
        "NSE_EQ|INE009A01021",  # Infosys
        "NSE_EQ|INE040A01034",  # HDFC Bank
        "NSE_EQ|INE062A01020",  # SBI
        "NSE_EQ|INE001A01036",  # Reliance
    ]


class UpstoxWorker:
    """
    Connects to Upstox WebSocket, processes ticks,
    writes to Redis, and signals candle builder.
    """

    def __init__(self, redis_client: aioredis.Redis):
        self.redis = redis_client
        self.candle_builder = CandleBuilder(redis_client)
        self.running = False
        self._pending_dashboard_updates: dict[str, dict] = {}
        self._last_dashboard_push = time.monotonic()

    def _validate_tick(self, tick: dict) -> bool:
        """Basic tick sanity check — never store invalid data."""
        try:
            ltp = tick.get("ltp") or tick.get("last_price")
            if ltp is None or float(ltp) <= 0:
                return False
            if not tick.get("instrument_token") and not tick.get("symbol"):
                return False
            return True
        except (ValueError, TypeError):
            return False

    def _normalize_tick(self, raw: dict) -> dict:
        """Normalize Upstox tick format to our standard format."""
        # Upstox v2 market feed format
        symbol = raw.get("symbol", "").replace("NSE_EQ:", "").replace("NSE_INDEX:", "")
        ltp = raw.get("ltp") or raw.get("last_price") or 0.0
        prev_close = raw.get("cp") or raw.get("prev_close_price") or 0.0
        change_abs = ltp - prev_close if prev_close else 0.0
        change_pct = (change_abs / prev_close * 100) if prev_close else 0.0

        return {
            "symbol": symbol,
            "ltp": round(float(ltp), 2),
            "open": round(float(raw.get("open", 0) or 0), 2),
            "high": round(float(raw.get("high", 0) or 0), 2),
            "low": round(float(raw.get("low", 0) or 0), 2),
            "close": round(float(prev_close), 2),
            "volume": int(raw.get("volume", 0) or 0),
            "bid": round(float(raw.get("bid_price", 0) or 0), 2),
            "ask": round(float(raw.get("ask_price", 0) or 0), 2),
            "change_abs": round(change_abs, 2),
            "change_pct": round(change_pct, 4),
            "ts": datetime.utcnow().isoformat(),
        }

    async def _process_tick(self, raw_tick: dict):
        """Process a single market tick."""
        if not self._validate_tick(raw_tick):
            log.debug(f"Invalid tick discarded: {raw_tick}")
            return

        tick = self._normalize_tick(raw_tick)
        symbol = tick["symbol"]
        if not symbol:
            return

        tick_json = json.dumps(tick)

        # 1. Update Redis latest price (expires in PRICE_STALE_TTL seconds)
        await self.redis.setex(
            f"latest:{symbol}",
            PRICE_STALE_TTL,
            tick_json,
        )

        # 2. Publish to symbol-specific Pub/Sub channel
        await self.redis.publish(f"ticks:{symbol}", tick_json)

        # 3. Queue for dashboard batch update
        self._pending_dashboard_updates[symbol] = tick

        # 4. Send to candle builder
        await self.candle_builder.on_tick(symbol, tick)

        # 5. Batch publish dashboard updates every 0.5 seconds
        now = time.monotonic()
        if now - self._last_dashboard_push >= DASHBOARD_BATCH_INTERVAL:
            if self._pending_dashboard_updates:
                batch_msg = json.dumps({
                    "type": "batch",
                    "updates": list(self._pending_dashboard_updates.values()),
                    "ts": datetime.utcnow().isoformat(),
                })
                await self.redis.publish("ticks:dashboard", batch_msg)
                self._pending_dashboard_updates.clear()
                self._last_dashboard_push = now

    async def run(self):
        """Main worker loop with exponential backoff reconnection."""
        self.running = True
        delay = RECONNECT_BASE_DELAY
        symbols = await load_subscribed_symbols()
        log.info(f"Subscribing to {len(symbols)} symbols")

        while self.running:
            try:
                await self._connect_and_stream(symbols)
                delay = RECONNECT_BASE_DELAY  # reset on clean disconnect
            except Exception as e:
                log.error(f"Worker error: {e}. Reconnecting in {delay}s...")
                await asyncio.sleep(delay)
                delay = min(delay * 2, RECONNECT_MAX_DELAY)

    async def _connect_and_stream(self, symbols: list[str]):
        """Connect to Upstox WebSocket and stream data."""
        access_token = await ensure_valid_token()
        if not access_token:
            raise RuntimeError("No valid Upstox access token available")

        # Configure Upstox SDK
        configuration = upstox_client.Configuration()
        configuration.access_token = access_token

        # --- MONKEY PATCH UPSTOX SDK 2.7.0 ---
        # Upstox deprecated the direct wss:// connection (returns 410 Gone).
        # We must call /v2/feed/market-data-feed/authorize first to get the URL.
        import requests
        import ssl
        import websocket
        import threading
        
        def patched_connect(self):
            if self.ws and self.ws.sock:
                return
            
            # Fetch authorized URL
            headers = {'Authorization': self.api_client.configuration.auth_settings().get("OAUTH2")["value"], "Accept": "application/json"}
            try:
                # Upstox completely discontinued V2 market-data-feed. We must use V3.
                resp = requests.get("https://api.upstox.com/v3/feed/market-data-feed/authorize", headers=headers)
                resp.raise_for_status()
                data = resp.json()
                ws_url = data.get("data", {}).get("authorized_redirect_uri")
                if not ws_url:
                    log.error(f"No authorized_redirect_uri found: {data}")
                    return
            except Exception as e:
                log.error(f"Failed to authorize websocket: {e}")
                return

            sslopt = {"cert_reqs": ssl.CERT_NONE, "check_hostname": False}
            self.ws = websocket.WebSocketApp(ws_url,
                                             header=headers,
                                             on_open=self.on_open,
                                             on_message=self.on_message,
                                             on_error=self.on_error,
                                             on_close=self.on_close)
            threading.Thread(target=self.ws.run_forever, kwargs={"sslopt": sslopt}).start()

        upstox_client.feeder.market_data_feeder.MarketDataFeeder.connect = patched_connect
        # -------------------------------------

        # Create market data streamer (v2.7.0: no mode in constructor)
        streamer = upstox_client.MarketDataStreamer(
            upstox_client.ApiClient(configuration),
        )

        # Event handlers registered via explicit calls (v2.7.0 API)
        # These are called synchronously by the streamer thread
        loop = asyncio.get_running_loop()

        def on_message(message):
            try:
                feeds = message.get("feeds", {})
                for instrument_key, feed_data in feeds.items():
                    raw_tick = feed_data.get("ff", {}).get("marketFF", {})
                    raw_tick["symbol"] = (
                        instrument_key.split("|")[-1]
                        if "|" in instrument_key else instrument_key
                    )
                    # Schedule the async processing on the main event loop
                    asyncio.run_coroutine_threadsafe(self._process_tick(raw_tick), loop)
            except Exception as e:
                log.warning(f"Tick processing error: {e}")

        disconnect_event = asyncio.Event()

        def on_open(*args, **kwargs):
            log.info("Upstox WebSocket connected")
            streamer.subscribe(symbols, mode="full")
            log.info(f"Subscribed to {len(symbols)} symbols")

        def on_close(*args, **kwargs):
            log.warning(f"Upstox WebSocket closed: {args}")
            loop.call_soon_threadsafe(disconnect_event.set)

        def on_error(*args, **kwargs):
            log.error(f"Upstox WebSocket error: {args}")
            loop.call_soon_threadsafe(disconnect_event.set)

        streamer.on("message", on_message)
        streamer.on("open",    on_open)
        streamer.on("close",   on_close)
        streamer.on("error",   on_error)

        # Connect is a synchronous non-blocking call in the SDK (spawns a thread),
        # so we run it and then wait for the disconnect event.
        streamer.connect()
        await disconnect_event.wait()


    def stop(self):
        self.running = False
        log.info("Worker stop requested")


# ─────────────────────────────────────────────────────────────
# Entry Point
# ─────────────────────────────────────────────────────────────
async def main():
    redis = aioredis.from_url(REDIS_URL, encoding="utf-8", decode_responses=True)
    worker = UpstoxWorker(redis)

    # Graceful shutdown
    loop = asyncio.get_event_loop()

    def handle_shutdown(*_):
        log.info("Shutdown signal received")
        worker.stop()
        loop.stop()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, handle_shutdown)
        except NotImplementedError:
            pass  # Windows doesn't support add_signal_handler

    # ── Launch Yahoo Finance poller concurrently ───────────────
    try:
        import asyncpg
        from yahoo_price_fetcher import YahooPriceFetcher, create_db_pool
        db_pool = await create_db_pool()
        yahoo_fetcher = YahooPriceFetcher(redis, db_pool)
        log.info("Yahoo Finance price poller enabled for global stocks")

        try:
            await asyncio.gather(
                worker.run(),
                yahoo_fetcher.run(),
            )
        finally:
            yahoo_fetcher.stop()
            await db_pool.close()
    except ImportError as e:
        log.warning(f"Yahoo fetcher not available ({e}) — running Upstox only")
        try:
            await worker.run()
        finally:
            await redis.aclose()
            log.info("Worker stopped cleanly")
        return

    await redis.aclose()
    log.info("Worker stopped cleanly")


if __name__ == "__main__":
    asyncio.run(main())

