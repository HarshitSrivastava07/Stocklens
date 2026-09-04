"use client";

import { useEffect, useState, useMemo } from "react";
import { useRouter } from "next/navigation";
import useSWR from "swr";
import {
  TrendingUp, TrendingDown, Activity, Search,
  Filter, RefreshCw, AlertCircle, ChevronUp, ChevronDown,
  Eye, Star, BarChart2, Cpu, Zap,
} from "lucide-react";
import { useDashboardWebSocket } from "@/hooks/useWebSocket";
import { useMarketStore } from "@/store/market";
import { stocksApi } from "@/lib/api";

// ─── Types ────────────────────────────────────────────────────
interface StockRow {
  nse_symbol: string;
  company_name: string;
  sector: string;
  industry: string;
  cluster_label: string | null;
  signal_color: "GREEN" | "YELLOW" | "RED" | "GREY";
  signal_label: string;
  ltp: number | null;
  iv_blended: number | null;
  upside_pct: number | null;
  margin_of_safety: number | null;
  fundamental_score: number | null;
  growth_score: number | null;
  risk_score: number | null;
  valuation_confidence: string | null;
  primary_model: string | null;
  main_reason: string | null;
  data_freshness: string;
  market_cap: number | null;
  pe: number | null;
  roe: number | null;
  debt_equity: number | null;
}

const SIGNAL_CONFIG = {
  GREEN: {
    label: "Potentially Undervalued",
    className: "signal-green",
    dot: "bg-green-500",
  },
  YELLOW: {
    label: "Review Required",
    className: "signal-yellow",
    dot: "bg-yellow-500",
  },
  RED: {
    label: "Avoid / Overvalued",
    className: "signal-red",
    dot: "bg-red-500",
  },
  GREY: {
    label: "Insufficient Data",
    className: "signal-grey",
    dot: "bg-gray-500",
  },
};

// ─── Helpers ──────────────────────────────────────────────────
const CURRENCY_SYMBOLS: Record<string, string> = {
  INR: "₹",
  USD: "$",
  GBP: "£",
  EUR: "€",
  JPY: "¥",
  HKD: "HK$",
  AUD: "A$",
};

const fmt = {
  price: (v: number | null, symbol = "₹") => v == null ? "—" : `${symbol}${v.toLocaleString(symbol === "₹" ? "en-IN" : "en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`,
  pct: (v: number | null) => v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(1)}%`,
  score: (v: number | null) => v == null ? "—" : v.toString(),
  num: (v: number | null, dec = 1) => v == null ? "—" : v.toFixed(dec),
  cr: (v: number | null, currency = "INR") => {
    if (v == null) return "—";
    const symbol = CURRENCY_SYMBOLS[currency] || "$";
    if (currency === "INR") {
      if (v >= 1e7) return `₹${(v / 1e7).toFixed(1)}Cr`;
      if (v >= 1e5) return `₹${(v / 1e5).toFixed(1)}L`;
      return `₹${v.toFixed(0)}`;
    } else {
      if (v >= 1e9) return `${symbol}${(v / 1e9).toFixed(1)}B`;
      if (v >= 1e6) return `${symbol}${(v / 1e6).toFixed(1)}M`;
      return `${symbol}${v.toFixed(0)}`;
    }
  },
};

const scoreColor = (score: number | null): string => {
  if (score == null) return "text-gray-500";
  if (score >= 75) return "text-green-400";
  if (score >= 50) return "text-yellow-400";
  return "text-red-400";
};

const upsideColor = (pct: number | null): string => {
  if (pct == null) return "";
  if (pct >= 25) return "price-up";
  if (pct > 0) return "text-yellow-400";
  return "price-down";
};

// ─── Score Bar Component ──────────────────────────────────────
function ScoreBar({ score }: { score: number | null }) {
  if (score == null) return <span className="text-gray-600">—</span>;
  const color = score >= 75 ? "green" : score >= 50 ? "yellow" : "red";
  return (
    <div className="flex items-center gap-2">
      <span className={`text-xs font-mono font-bold ${scoreColor(score)}`}>
        {score}
      </span>
      <div className="score-bar-track" style={{ width: 40 }}>
        <div
          className={`score-bar-fill ${color}`}
          style={{ width: `${Math.min(score, 100)}%` }}
        />
      </div>
    </div>
  );
}

// ─── Signal Badge ─────────────────────────────────────────────
function SignalBadge({ color, label }: { color: keyof typeof SIGNAL_CONFIG; label: string }) {
  return (
    <span className={`signal-badge ${SIGNAL_CONFIG[color].className}`}>
      <span className={`w-1.5 h-1.5 rounded-full inline-block ${SIGNAL_CONFIG[color].dot}`} />
      {label}
    </span>
  );
}

// ─── Market Status Bar ────────────────────────────────────────
function MarketStatusBar() {
  const { marketStatus, wsConnected, quotes } = useMarketStore();

  return (
    <div className="flex items-center gap-4 text-xs">
      <div className="flex items-center gap-1.5">
        {wsConnected ? (
          <>
            <span className="live-dot" />
            <span className="text-green-400 font-semibold">LIVE</span>
          </>
        ) : (
          <>
            <span className="w-2 h-2 rounded-full bg-gray-600 inline-block" />
            <span className="text-gray-500">Disconnected</span>
          </>
        )}
      </div>
      {marketStatus && (
        <span
          className={
            marketStatus.status === "OPEN"
              ? "text-green-400"
              : "text-gray-500"
          }
        >
          Market:{" "}
          {marketStatus.status === "OPEN"
            ? `Open · ${marketStatus.minutes_remaining}m left`
            : marketStatus.status}
        </span>
      )}
      <span className="text-gray-600">
        {Object.keys(quotes).length} symbols tracked
      </span>
    </div>
  );
}

// ─── Filters ──────────────────────────────────────────────────
interface FilterState {
  signal: string;
  search: string;
  sector: string;
  minUpside: number;
  maxRisk: number;
  cluster: string;
  exchange: string;
  sort: { key: string; dir: "asc" | "desc" };
}

const DEFAULT_FILTERS: FilterState = {
  signal: "ALL",
  search: "",
  sector: "ALL",
  minUpside: 0,
  maxRisk: 100,
  cluster: "ALL",
  exchange: "ALL",
  sort: { key: "upside_pct", dir: "desc" },
};

// ─── Main Dashboard Page ─────────────────────────────────────
export default function DashboardPage() {
  useDashboardWebSocket();

  const [filters, setFilters] = useState<FilterState>(DEFAULT_FILTERS);
  const [showFilters, setShowFilters] = useState(false);
  const [visibleCount, setVisibleCount] = useState(100);
  const quotes = useMarketStore((s) => s.quotes);
  const router = useRouter();

  // Fetch stock list with signals
  const { data, error, isLoading, mutate } = useSWR(
    "/api/v1/stocks?include_signals=true&limit=2000",
    () => stocksApi.list({ include_signals: "true", limit: 2000 }) as any,
    { refreshInterval: 60_000 }
  );

  const stocks: StockRow[] = data?.stocks || [];

  // Merge live prices from WebSocket into stock rows
  const enrichedStocks = useMemo(
    () =>
      stocks.map((s) => ({
        ...s,
        ltp: quotes[s.nse_symbol]?.ltp ?? s.ltp,
        change_pct: quotes[s.nse_symbol]?.change_pct,
        change_abs: quotes[s.nse_symbol]?.change_abs,
        data_freshness: quotes[s.nse_symbol]?.data_freshness ?? s.data_freshness,
      })),
    [stocks, quotes]
  );

  // Apply filters
  const filtered = useMemo(() => {
    let rows = enrichedStocks;

    if (filters.exchange !== "ALL") {
      rows = rows.filter((r) => r.exchange === filters.exchange);
    }
    if (filters.signal !== "ALL") {
      rows = rows.filter((r) => r.signal_color === filters.signal);
    }
    if (filters.search) {
      const q = filters.search.toLowerCase();
      rows = rows.filter(
          (r) =>
              r.nse_symbol.toLowerCase().includes(q) ||
              r.company_name.toLowerCase().includes(q)
      );
    }
    if (filters.sector !== "ALL") {
      rows = rows.filter((r) => r.sector === filters.sector);
    }
    if (filters.minUpside > 0) {
      rows = rows.filter((r) => (r.upside_pct ?? -999) >= filters.minUpside);
    }
    if (filters.maxRisk < 100) {
      rows = rows.filter((r) => (r.risk_score ?? 999) <= filters.maxRisk);
    }
    if (filters.cluster !== "ALL") {
      rows = rows.filter((r) => r.cluster_label === filters.cluster);
    }

    // Sort
    const { key, dir } = filters.sort;
    rows = [...rows].sort((a, b) => {
      const av = (a as any)[key] ?? (dir === "asc" ? Infinity : -Infinity);
      const bv = (b as any)[key] ?? (dir === "asc" ? Infinity : -Infinity);
      return dir === "asc" ? av - bv : bv - av;
    });

    return rows;
  }, [enrichedStocks, filters]);

  // Summary counts
  const counts = useMemo(
    () => ({
      green: enrichedStocks.filter((s) => s.signal_color === "GREEN").length,
      yellow: enrichedStocks.filter((s) => s.signal_color === "YELLOW").length,
      red: enrichedStocks.filter((s) => s.signal_color === "RED").length,
      grey: enrichedStocks.filter((s) => s.signal_color === "GREY").length,
    }),
    [enrichedStocks]
  );

  // Sort header click
  const toggleSort = (key: string) => {
    setFilters((f) => ({
      ...f,
      sort: {
        key,
        dir: f.sort.key === key && f.sort.dir === "desc" ? "asc" : "desc",
      },
    }));
  };

  const SortIcon = ({ col }: { col: string }) =>
    filters.sort.key === col ? (
      filters.sort.dir === "asc" ? (
        <ChevronUp className="w-3 h-3 inline" />
      ) : (
        <ChevronDown className="w-3 h-3 inline" />
      )
    ) : null;

  return (
    <div className="min-h-screen" style={{ background: "var(--bg-base)" }}>
      {/* ── Topbar ── */}
      <div className="topbar">
        <div className="flex items-center gap-3">
          <BarChart2 className="w-5 h-5 text-blue-400" />
          <span className="font-bold text-sm tracking-tight" style={{ color: "var(--text-primary)" }}>
            StockLens
          </span>
          <span className="text-xs px-2 py-0.5 rounded" style={{ background: "rgba(33,136,255,0.15)", color: "#2188ff" }}>
            Personal
          </span>
        </div>
        <div className="flex-1" />
        <MarketStatusBar />
        <button
          className="btn btn-ghost"
          onClick={() => mutate()}
          title="Refresh data"
        >
          <RefreshCw className="w-4 h-4" />
        </button>
      </div>

      <div className="flex">
        {/* ── Sidebar ── */}
        <aside className="sidebar w-[220px] shrink-0">
          <nav className="py-4">
            <div className="nav-section-label">Research</div>
            <a href="/" className="nav-item active">
              <BarChart2 className="w-4 h-4" /> Dashboard
            </a>
            <a href="/screener" className="nav-item">
              <Filter className="w-4 h-4" /> Screener
            </a>
            <a href="/sectors" className="nav-item">
              <Activity className="w-4 h-4" /> Sectors
            </a>
            <a href="/ai" className="nav-item">
              <Cpu className="w-4 h-4" /> AI Research
            </a>
            <a href="/backtest" className="nav-item">
              <Zap className="w-4 h-4" /> Backtest
            </a>

            <div className="nav-section-label mt-2">Portfolio</div>
            <a href="/watchlist" className="nav-item">
              <Star className="w-4 h-4" /> Watchlist
            </a>
            <a href="/portfolio" className="nav-item">
              <TrendingUp className="w-4 h-4" /> Portfolio
            </a>
            <a href="/alerts" className="nav-item">
              <AlertCircle className="w-4 h-4" /> Alerts
            </a>

            <div className="nav-section-label mt-2">Admin</div>
            <a href="/admin" className="nav-item">
              <Eye className="w-4 h-4" /> Admin Panel
            </a>
          </nav>
        </aside>

        {/* ── Main Content ── */}
        <main className="main-content flex-1 min-w-0">
          {/* Summary Tiles */}
          <div className="grid grid-cols-4 gap-4 mb-6">
            {[
              { label: "Potentially Undervalued", count: counts.green, color: "#3fb950", bg: "rgba(63,185,80,0.08)", onClick: () => setFilters((f) => ({ ...f, signal: f.signal === "GREEN" ? "ALL" : "GREEN" })) },
              { label: "Review Required", count: counts.yellow, color: "#e3b341", bg: "rgba(227,179,65,0.08)", onClick: () => setFilters((f) => ({ ...f, signal: f.signal === "YELLOW" ? "ALL" : "YELLOW" })) },
              { label: "Avoid / Overvalued", count: counts.red, color: "#f85149", bg: "rgba(248,81,73,0.08)", onClick: () => setFilters((f) => ({ ...f, signal: f.signal === "RED" ? "ALL" : "RED" })) },
              { label: "Insufficient Data", count: counts.grey, color: "#6e7681", bg: "rgba(110,118,129,0.08)", onClick: () => setFilters((f) => ({ ...f, signal: f.signal === "GREY" ? "ALL" : "GREY" })) },
            ].map((t) => (
              <button
                key={t.label}
                className="metric-card text-left cursor-pointer"
                style={{ borderColor: filters.signal !== "ALL" ? "transparent" : undefined, background: t.bg }}
                onClick={t.onClick}
              >
                <div className="metric-label">{t.label}</div>
                <div className="metric-value" style={{ color: t.color }}>
                  {isLoading ? "—" : t.count}
                </div>
                <div className="metric-sub">stocks</div>
              </button>
            ))}
          </div>

          {/* Exchange/Market Tabs */}
          <div className="flex border-b border-gray-800 mb-5 gap-1 overflow-x-auto pb-1 scrollbar-none">
            {[
              { id: "ALL", label: "All Markets", count: enrichedStocks.length },
              { id: "NSE", label: "India (NSE)", count: enrichedStocks.filter(s => s.exchange === "NSE").length },
              { id: "NASDAQ", label: "US (NASDAQ)", count: enrichedStocks.filter(s => s.exchange === "NASDAQ").length },
              { id: "NYSE", label: "US (NYSE)", count: enrichedStocks.filter(s => s.exchange === "NYSE").length },
              { id: "LSE", label: "UK (LSE)", count: enrichedStocks.filter(s => s.exchange === "LSE").length },
              { id: "TSE", label: "Japan (TSE)", count: enrichedStocks.filter(s => s.exchange === "TSE").length },
              { id: "XETRA", label: "Germany (XETRA)", count: enrichedStocks.filter(s => s.exchange === "XETRA").length },
              { id: "HKEX", label: "HK (HKEX)", count: enrichedStocks.filter(s => s.exchange === "HKEX").length },
              { id: "ASX", label: "Australia (ASX)", count: enrichedStocks.filter(s => s.exchange === "ASX").length },
            ].map((tab) => (
              <button
                key={tab.id}
                onClick={() => setFilters(f => ({ ...f, exchange: tab.id }))}
                className={`px-3 py-1.5 text-xs font-semibold border-b-2 transition-all shrink-0 ${
                  filters.exchange === tab.id
                    ? "border-blue-500 text-blue-400 font-bold"
                    : "border-transparent text-gray-400 hover:text-gray-200"
                }`}
              >
                {tab.label} <span className="text-gray-500 text-[10px] ml-1">({tab.count})</span>
              </button>
            ))}
          </div>

          {/* Search + Filter Bar */}
          <div className="flex items-center gap-3 mb-4">
            <div className="relative flex-1 max-w-xs">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-500" />
              <input
                className="input pl-9"
                placeholder="Search symbol or company..."
                value={filters.search}
                onChange={(e) => {
                  setFilters((f) => ({ ...f, search: e.target.value.slice(0, 50) }));
                  setVisibleCount(100); // Reset visible count on search
                }}
                maxLength={50}
              />
            </div>
            <button
              className={`btn ${showFilters ? "btn-primary" : "btn-ghost"}`}
              onClick={() => setShowFilters(!showFilters)}
            >
              <Filter className="w-4 h-4" /> Filters
            </button>
            {filters.signal !== "ALL" && (
              <button
                className="btn btn-ghost text-xs"
                onClick={() => setFilters(DEFAULT_FILTERS)}
              >
                Clear filters
              </button>
            )}
            <span style={{ color: "var(--text-muted)", fontSize: 12 }}>
              {filtered.length} stocks
            </span>
          </div>

          {/* Extended Filters */}
          {showFilters && (
            <div
              className="card p-4 mb-4 grid grid-cols-2 gap-4 md:grid-cols-4 animate-slide-up"
            >
              <div>
                <label className="metric-label">Signal</label>
                <select
                  className="input mt-1"
                  value={filters.signal}
                  onChange={(e) => setFilters((f) => ({ ...f, signal: e.target.value }))}
                >
                  <option value="ALL">All Signals</option>
                  <option value="GREEN">🟢 Potentially Undervalued</option>
                  <option value="YELLOW">🟡 Review Required</option>
                  <option value="RED">🔴 Avoid / Overvalued</option>
                  <option value="GREY">⚫ Insufficient Data</option>
                </select>
              </div>
              <div>
                <label className="metric-label">Min Upside %</label>
                <input
                  type="number"
                  className="input mt-1"
                  min={0}
                  max={200}
                  value={filters.minUpside}
                  onChange={(e) => setFilters((f) => ({ ...f, minUpside: Number(e.target.value) }))}
                />
              </div>
              <div>
                <label className="metric-label">Max Risk Score</label>
                <input
                  type="number"
                  className="input mt-1"
                  min={0}
                  max={100}
                  value={filters.maxRisk}
                  onChange={(e) => setFilters((f) => ({ ...f, maxRisk: Number(e.target.value) }))}
                />
              </div>
              <div>
                <label className="metric-label">ML Cluster</label>
                <select
                  className="input mt-1"
                  value={filters.cluster}
                  onChange={(e) => setFilters((f) => ({ ...f, cluster: e.target.value }))}
                >
                  <option value="ALL">All Clusters</option>
                  <option value="QUALITY_COMPOUNDER">Quality Compounder</option>
                  <option value="DEEP_VALUE">Deep Value</option>
                  <option value="GARP">GARP</option>
                  <option value="CYCLICAL_RECOVERY">Cyclical Recovery</option>
                  <option value="HIGH_GROWTH_EXPENSIVE">High Growth Expensive</option>
                  <option value="VALUE_TRAP">Value Trap</option>
                  <option value="DISTRESSED">Distressed / Avoid</option>
                </select>
              </div>
            </div>
          )}

          {/* Disclaimer */}
          <div className="disclaimer-banner mb-4">
            ⚠️ <strong>Research Tool Only.</strong> This platform is for personal stock research and screening.
            It is <strong>not investment advice</strong> and does not guarantee returns. "Potentially Undervalued"
            is based on model assumptions, not a guarantee. Verify all data independently before making decisions.
          </div>

          {/* Main Table */}
          <div className="card overflow-hidden">
            {isLoading ? (
              <div className="p-8 text-center" style={{ color: "var(--text-muted)" }}>
                <div className="skeleton h-8 w-full mb-2" />
                <div className="skeleton h-8 w-full mb-2" />
                <div className="skeleton h-8 w-full mb-2" />
                <div className="skeleton h-8 w-full" />
              </div>
            ) : error ? (
              <div className="p-8 text-center text-red-400">
                <AlertCircle className="w-8 h-8 mx-auto mb-2" />
                Failed to load stocks. Is the backend running at localhost:8000?
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th style={{ minWidth: 90 }}>Signal</th>
                      <th style={{ minWidth: 180 }} onClick={() => toggleSort("company_name")}>
                        Company <SortIcon col="company_name" />
                      </th>
                      <th>Sector</th>
                      <th>Cluster</th>
                      <th style={{ minWidth: 100 }} onClick={() => toggleSort("ltp")}>
                        CMP <SortIcon col="ltp" />
                      </th>
                      <th onClick={() => toggleSort("iv_blended")}>
                        Intrinsic Value <SortIcon col="iv_blended" />
                      </th>
                      <th onClick={() => toggleSort("upside_pct")}>
                        Upside <SortIcon col="upside_pct" />
                      </th>
                      <th onClick={() => toggleSort("margin_of_safety")}>
                        MoS <SortIcon col="margin_of_safety" />
                      </th>
                      <th onClick={() => toggleSort("fundamental_score")}>
                        Fund. <SortIcon col="fundamental_score" />
                      </th>
                      <th onClick={() => toggleSort("growth_score")}>
                        Growth <SortIcon col="growth_score" />
                      </th>
                      <th onClick={() => toggleSort("risk_score")}>
                        Risk <SortIcon col="risk_score" />
                      </th>
                      <th>Confidence</th>
                      <th>Model</th>
                      <th>Freshness</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filtered.length === 0 ? (
                      <tr>
                        <td colSpan={14} className="text-center py-12" style={{ color: "var(--text-muted)" }}>
                          No stocks match your filters
                        </td>
                      </tr>
                    ) : (
                      <>
                        {filtered.slice(0, visibleCount).map((stock) => (
                          <tr
                            key={stock.nse_symbol}
                            onClick={() => router.push(`/stocks/${stock.nse_symbol}`)}
                          >
                            <td>
                              <SignalBadge
                                color={stock.signal_color}
                                label={
                                  stock.signal_color === "GREEN"
                                    ? "Undervalued"
                                    : stock.signal_color === "YELLOW"
                                    ? "Review"
                                    : stock.signal_color === "RED"
                                    ? "Avoid"
                                    : "No Data"
                                }
                              />
                            </td>
                            <td>
                              <div className="primary font-semibold" style={{ color: "var(--text-primary)", fontSize: 13 }}>
                                {stock.nse_symbol}
                              </div>
                              <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>
                                {stock.company_name.length > 28
                                  ? stock.company_name.slice(0, 28) + "…"
                                  : stock.company_name}
                              </div>
                            </td>
                            <td style={{ fontSize: 11 }}>
                              <span style={{ color: "var(--text-muted)" }}>{stock.sector}</span>
                            </td>
                            <td style={{ fontSize: 11 }}>
                              {stock.cluster_label ? (
                                <span
                                  style={{
                                    background: "rgba(33,136,255,0.1)",
                                    color: "#2188ff",
                                    padding: "2px 8px",
                                    borderRadius: 4,
                                    fontSize: 10,
                                    fontWeight: 600,
                                  }}
                                >
                                  {stock.cluster_label.replace(/_/g, " ")}
                                </span>
                              ) : (
                                <span style={{ color: "var(--text-muted)" }}>—</span>
                              )}
                            </td>
                            <td>
                              <div className="font-mono font-semibold" style={{ color: "var(--text-primary)", fontSize: 13 }}>
                                {fmt.price(stock.ltp, stock.currency_symbol)}
                              </div>
                              {(stock as any).change_pct != null && (
                                <div className={`text-xs font-mono ${(stock as any).change_pct >= 0 ? "price-up" : "price-down"}`}>
                                  {fmt.pct((stock as any).change_pct)}
                                </div>
                              )}
                            </td>
                            <td className="font-mono" style={{ color: "var(--text-secondary)" }}>
                              {fmt.price(stock.iv_blended, stock.currency_symbol)}
                            </td>
                            <td>
                              <span className={`font-mono font-bold text-sm ${upsideColor(stock.upside_pct)}`}>
                                {fmt.pct(stock.upside_pct)}
                              </span>
                            </td>
                            <td>
                              <span className={`font-mono text-sm ${upsideColor(stock.margin_of_safety ? stock.margin_of_safety * 100 : null)}`}>
                                {stock.margin_of_safety != null
                                  ? `${(stock.margin_of_safety * 100).toFixed(1)}%`
                                  : "—"}
                              </span>
                            </td>
                            <td><ScoreBar score={stock.fundamental_score} /></td>
                            <td><ScoreBar score={stock.growth_score} /></td>
                            <td>
                              {stock.risk_score != null ? (
                                <div className="flex items-center gap-2">
                                  <span className={`text-xs font-mono font-bold ${stock.risk_score <= 30 ? "price-up" : stock.risk_score <= 60 ? "text-yellow-400" : "price-down"}`}>
                                    {stock.risk_score}
                                  </span>
                                  <div className="score-bar-track" style={{ width: 40 }}>
                                    <div
                                      className={`score-bar-fill ${stock.risk_score <= 30 ? "green" : stock.risk_score <= 60 ? "yellow" : "red"}`}
                                      style={{ width: `${stock.risk_score}%` }}
                                    />
                                  </div>
                                </div>
                              ) : (
                                <span style={{ color: "var(--text-muted)" }}>—</span>
                              )}
                            </td>
                            <td>
                              {stock.valuation_confidence ? (
                                <span
                                  className="text-xs font-semibold px-2 py-0.5 rounded"
                                  style={{
                                    background:
                                      stock.valuation_confidence === "HIGH"
                                        ? "rgba(63,185,80,0.1)"
                                        : stock.valuation_confidence === "MEDIUM"
                                        ? "rgba(227,179,65,0.1)"
                                        : "rgba(248,81,73,0.1)",
                                    color:
                                      stock.valuation_confidence === "HIGH"
                                        ? "#3fb950"
                                        : stock.valuation_confidence === "MEDIUM"
                                        ? "#e3b341"
                                        : "#f85149",
                                  }}
                                >
                                  {stock.valuation_confidence}
                                </span>
                              ) : (
                                <span style={{ color: "var(--text-muted)" }}>—</span>
                              )}
                            </td>
                            <td style={{ fontSize: 11, color: "var(--text-muted)" }}>
                              {stock.primary_model || "—"}
                            </td>
                            <td>
                              <span
                                style={{
                                  fontSize: 10,
                                  fontWeight: 600,
                                  color:
                                    stock.data_freshness === "FRESH"
                                      ? "#3fb950"
                                      : stock.data_freshness === "STALE"
                                      ? "#e3b341"
                                      : "#6e7681",
                                }}
                              >
                                {stock.data_freshness === "FRESH" ? "● LIVE" : stock.data_freshness === "STALE" ? "⚠ STALE" : "○ OLD"}
                              </span>
                            </td>
                          </tr>
                        ))}
                        {filtered.length > visibleCount && (
                          <tr>
                            <td colSpan={14} className="text-center py-6">
                              <button
                                className="px-6 py-2 bg-gray-800 hover:bg-gray-700 text-gray-200 text-sm font-semibold rounded-lg transition-colors border border-gray-700"
                                onClick={() => setVisibleCount((v) => v + 100)}
                              >
                                Load More Stocks ({filtered.length - visibleCount} remaining)
                              </button>
                            </td>
                          </tr>
                        )}
                      </>
                    )}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </main>
      </div>
    </div>
  );
}
