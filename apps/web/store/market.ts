/**
 * Zustand store for real-time market data
 * Updated by WebSocket connection
 */
import { create } from "zustand";

export interface Quote {
  nse_symbol: string;
  ltp: number | null;
  open: number | null;
  high: number | null;
  low: number | null;
  close: number | null;  // prev close
  volume: number | null;
  change_abs: number | null;
  change_pct: number | null;
  week_52_high: number | null;
  week_52_low: number | null;
  last_updated: string;
  data_freshness: "FRESH" | "STALE" | "UNAVAILABLE";
  is_stale?: boolean;
}

export interface MarketStatus {
  status: "OPEN" | "CLOSED" | "PRE_OPEN" | "POST_CLOSE";
  minutes_remaining?: number;
  reason?: string;
}

interface MarketStore {
  // Quotes keyed by NSE symbol
  quotes: Record<string, Quote>;
  // Market status
  marketStatus: MarketStatus | null;
  // WebSocket connection state
  wsConnected: boolean;

  // Actions
  updateQuote: (quote: Quote) => void;
  updateQuotes: (quotes: Quote[]) => void;
  setMarketStatus: (status: MarketStatus) => void;
  setWsConnected: (connected: boolean) => void;
  getQuote: (symbol: string) => Quote | undefined;
}

export const useMarketStore = create<MarketStore>((set, get) => ({
  quotes: {},
  marketStatus: null,
  wsConnected: false,

  updateQuote: (quote) =>
    set((state) => ({
      quotes: { ...state.quotes, [quote.nse_symbol]: quote },
    })),

  updateQuotes: (quotes) =>
    set((state) => ({
      quotes: {
        ...state.quotes,
        ...Object.fromEntries(quotes.map((q) => [q.nse_symbol, q])),
      },
    })),

  setMarketStatus: (status) => set({ marketStatus: status }),
  setWsConnected: (connected) => set({ wsConnected: connected }),
  getQuote: (symbol) => get().quotes[symbol],
}));
