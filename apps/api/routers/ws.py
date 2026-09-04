"""
WebSocket Router — real-time price streaming to browser
Subscribes to Redis Pub/Sub and fans out to connected clients
"""
import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from redis.asyncio import Redis

from dependencies.redis import get_redis_client

router = APIRouter()
log = logging.getLogger(__name__)


class ConnectionManager:
    """Manages active WebSocket connections per symbol."""

    def __init__(self):
        # symbol -> set of websockets
        self.active: dict[str, set[WebSocket]] = {}

    async def connect(self, symbol: str, ws: WebSocket):
        await ws.accept()
        if symbol not in self.active:
            self.active[symbol] = set()
        self.active[symbol].add(ws)
        log.info(f"WS connected: {symbol}, total={len(self.active[symbol])}")

    def disconnect(self, symbol: str, ws: WebSocket):
        if symbol in self.active:
            self.active[symbol].discard(ws)
            if not self.active[symbol]:
                del self.active[symbol]

    async def broadcast(self, symbol: str, message: dict):
        if symbol not in self.active:
            return
        dead = set()
        for ws in self.active[symbol]:
            try:
                await ws.send_json(message)
            except Exception:
                dead.add(ws)
        for ws in dead:
            self.active[symbol].discard(ws)


manager = ConnectionManager()


@router.websocket("/ws/ticks/{symbol}")
async def websocket_ticks(symbol: str, websocket: WebSocket):
    """
    Real-time tick stream for a symbol.
    Subscribes to Redis Pub/Sub channel ticks:{symbol}
    and pushes updates to the browser.
    """
    symbol = symbol.upper().strip()
    await manager.connect(symbol, websocket)

    redis: Redis = await get_redis_client()

    # Also send latest cached price immediately
    cached = await redis.get(f"latest:{symbol}")
    if cached:
        await websocket.send_json({
            "type": "snapshot",
            "symbol": symbol,
            "data": json.loads(cached),
        })

    # Subscribe to Pub/Sub channel
    pubsub = redis.pubsub()
    await pubsub.subscribe(f"ticks:{symbol}")

    try:
        async for message in pubsub.listen():
            if message["type"] == "message":
                try:
                    tick = json.loads(message["data"])
                    await websocket.send_json({
                        "type": "tick",
                        "symbol": symbol,
                        "data": tick,
                    })
                except Exception as e:
                    log.warning(f"WS send error {symbol}: {e}")
    except WebSocketDisconnect:
        log.info(f"WS disconnected: {symbol}")
    finally:
        manager.disconnect(symbol, websocket)
        await pubsub.unsubscribe(f"ticks:{symbol}")
        await pubsub.aclose()


@router.websocket("/ws/dashboard")
async def websocket_dashboard(websocket: WebSocket):
    """
    Bulk real-time feed for the main dashboard.
    Subscribes to Redis channel 'ticks:dashboard' which receives
    aggregated updates for all active symbols.
    """
    await websocket.accept()
    redis: Redis = await get_redis_client()
    pubsub = redis.pubsub()
    await pubsub.subscribe("ticks:dashboard")

    try:
        async for message in pubsub.listen():
            if message["type"] == "message":
                try:
                    await websocket.send_text(message["data"])
                except WebSocketDisconnect:
                    break
    except WebSocketDisconnect:
        pass
    finally:
        await pubsub.unsubscribe("ticks:dashboard")
        await pubsub.aclose()
        # BUG18 FIX: websocket may already be closed on disconnect
        try:
            await websocket.close()
        except Exception:
            pass
