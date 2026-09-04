/**
 * WebSocket hook for real-time market data
 * Exponential backoff reconnection, Redis Pub/Sub fan-out
 */
"use client";
import { useEffect, useRef, useCallback } from "react";
import { useMarketStore } from "@/store/market";

const WS_BASE = process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8000";
const MAX_RECONNECT_DELAY = 30_000; // 30 seconds

export function useDashboardWebSocket() {
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectDelay = useRef(1000);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const isMounted = useRef(true);

  const { updateQuotes, setWsConnected } = useMarketStore();

  const connect = useCallback(() => {
    if (!isMounted.current) return;

    try {
      const ws = new WebSocket(`${WS_BASE}/api/v1/ws/dashboard`);
      wsRef.current = ws;

      ws.onopen = () => {
        console.info("[WS] Dashboard connected");
        reconnectDelay.current = 1000; // reset on success
        setWsConnected(true);
      };

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data);
          if (msg.type === "batch" && Array.isArray(msg.updates)) {
            updateQuotes(msg.updates);
          }
        } catch {
          // Non-JSON message — ignore
        }
      };

      ws.onclose = (event) => {
        setWsConnected(false);
        if (!isMounted.current) return;

        console.warn(`[WS] Disconnected (${event.code}). Retrying in ${reconnectDelay.current}ms`);
        reconnectTimer.current = setTimeout(() => {
          reconnectDelay.current = Math.min(reconnectDelay.current * 2, MAX_RECONNECT_DELAY);
          connect();
        }, reconnectDelay.current);
      };

      ws.onerror = () => {
        // WebSocket server not running (live data worker offline) — suppress overlay
        ws.close();
      };
    } catch (error) {
      console.warn("[WS] Failed to connect — live data worker not running:", error);
    }
  }, [updateQuotes, setWsConnected]);

  useEffect(() => {
    isMounted.current = true;
    connect();

    return () => {
      isMounted.current = false;
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
      wsRef.current?.close();
    };
  }, [connect]);
}

/**
 * Subscribe to a single symbol's tick stream
 */
export function useSymbolWebSocket(symbol: string) {
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectDelay = useRef(1000);
  const isMounted = useRef(true);
  const { updateQuote, setWsConnected } = useMarketStore();

  const connect = useCallback(() => {
    if (!isMounted.current || !symbol) return;

    const ws = new WebSocket(`${WS_BASE}/api/v1/ws/ticks/${symbol}`);
    wsRef.current = ws;

    ws.onopen = () => {
      reconnectDelay.current = 1000;
      setWsConnected(true);
    };

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        if (msg.data && msg.symbol === symbol) {
          updateQuote({ ...msg.data, nse_symbol: symbol });
        }
      } catch {}
    };

    ws.onclose = () => {
      setWsConnected(false);
      if (!isMounted.current) return;
      setTimeout(() => {
        reconnectDelay.current = Math.min(reconnectDelay.current * 2, MAX_RECONNECT_DELAY);
        connect();
      }, reconnectDelay.current);
    };

    ws.onerror = () => ws.close();
  }, [symbol, updateQuote, setWsConnected]);

  useEffect(() => {
    isMounted.current = true;
    if (symbol) connect();
    return () => {
      isMounted.current = false;
      wsRef.current?.close();
    };
  }, [symbol, connect]);
}
