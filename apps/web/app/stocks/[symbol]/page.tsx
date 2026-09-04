"use client";

import { useParams, useRouter } from "next/navigation";
import useSWR from "swr";
import {
  ArrowLeft, TrendingUp, TrendingDown, AlertTriangle,
  RefreshCw, Brain, BarChart2, Cpu, Activity, Shield,
  Info, ExternalLink,
} from "lucide-react";
import { useSymbolWebSocket } from "@/hooks/useWebSocket";
import { useMarketStore } from "@/store/market";
import { stocksApi, marketApi, aiApi } from "@/lib/api";
import { useState } from "react";
import PriceChart from "@/components/PriceChart";
import ErrorBoundary from "@/components/ErrorBoundary";


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
  price: (v: number | null, symbol = "₹") =>
    v == null ? "—" : `${symbol}${v.toLocaleString(symbol === "₹" ? "en-IN" : "en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`,
  pct: (v: number | null, mult = 1) =>
    v == null ? "—" : `${(v * mult) >= 0 ? "+" : ""}${(v * mult).toFixed(1)}%`,
  num: (v: number | null, dec = 2) => v == null ? "—" : v.toFixed(dec),
  cr: (v: number | null, currency = "INR") => {
    if (v == null) return "—";
    const symbol = CURRENCY_SYMBOLS[currency] || "$";
    const abs = Math.abs(v);
    const sign = v < 0 ? "-" : "";
    if (currency === "INR") {
      if (abs >= 1e7) return `${sign}₹${(abs / 1e7).toFixed(1)}Cr`;
      if (abs >= 1e5) return `${sign}₹${(abs / 1e5).toFixed(1)}L`;
      return `${sign}₹${abs.toFixed(0)}`;
    } else {
      if (abs >= 1e9) return `${sign}${symbol}${(abs / 1e9).toFixed(1)}B`;
      if (abs >= 1e6) return `${sign}${symbol}${(abs / 1e6).toFixed(1)}M`;
      return `${sign}${symbol}${abs.toFixed(0)}`;
    }
  },
};

const signalConfig = {
  GREEN: { bg: "rgba(63,185,80,0.1)", color: "#3fb950", border: "rgba(63,185,80,0.3)" },
  YELLOW: { bg: "rgba(227,179,65,0.1)", color: "#e3b341", border: "rgba(227,179,65,0.3)" },
  RED: { bg: "rgba(248,81,73,0.1)", color: "#f85149", border: "rgba(248,81,73,0.3)" },
  GREY: { bg: "rgba(110,118,129,0.1)", color: "#6e7681", border: "rgba(110,118,129,0.3)" },
};

// ─── Small Components ─────────────────────────────────────────
function MetricCard({ label, value, sub, color }: { label: string; value: string; sub?: string; color?: string }) {
  return (
    <div className="metric-card">
      <div className="metric-label">{label}</div>
      <div className="metric-value" style={color ? { color } : undefined}>{value}</div>
      {sub && <div className="metric-sub">{sub}</div>}
    </div>
  );
}

function RatioRow({ label, value, highlight }: { label: string; value: string; highlight?: "good" | "bad" | "neutral" }) {
  const color = highlight === "good" ? "#3fb950" : highlight === "bad" ? "#f85149" : "var(--text-secondary)";
  return (
    <div className="flex justify-between items-center py-2" style={{ borderBottom: "1px solid var(--bg-border)" }}>
      <span style={{ color: "var(--text-muted)", fontSize: 12 }}>{label}</span>
      <span className="font-mono text-sm font-semibold" style={{ color }}>{value}</span>
    </div>
  );
}

function ScoreGauge({ score, label, color }: { score: number | null; label: string; color: string }) {
  if (score == null) return null;
  return (
    <div className="flex flex-col items-center gap-2">
      <div
        className="w-16 h-16 rounded-full flex items-center justify-center font-bold text-xl"
        style={{ background: `${color}20`, border: `2px solid ${color}`, color }}
      >
        {score}
      </div>
      <div className="text-xs text-center" style={{ color: "var(--text-muted)" }}>{label}</div>
    </div>
  );
}

function RiskFlagBadge({ severity, description }: { severity: string; description: string }) {
  const colors = { CRITICAL: "#f85149", HIGH: "#e3b341", MEDIUM: "#2188ff", LOW: "#6e7681" };
  const c = colors[severity as keyof typeof colors] || "#6e7681";
  return (
    <div className="flex items-start gap-2 py-2" style={{ borderBottom: "1px solid var(--bg-border)" }}>
      <span className="text-xs font-bold px-2 py-0.5 rounded mt-0.5" style={{ background: `${c}20`, color: c }}>
        {severity}
      </span>
      <span style={{ color: "var(--text-secondary)", fontSize: 12 }}>{description}</span>
    </div>
  );
}

// ─── Valuation Visual ─────────────────────────────────────────
function ValuationRange({ iv, cmp, currencySymbol = "₹" }: { iv: any; cmp: number | null; currencySymbol?: string }) {
  if (!iv || !cmp) return null;
  const bear = iv.iv_bear || 0;
  const bull = iv.iv_bull || 0;
  const range = bull - bear;
  if (range <= 0) return null;

  const cmpPct = Math.min(100, Math.max(0, ((cmp - bear) / range) * 100));
  const basePct = ((iv.iv_base - bear) / range) * 100;

  return (
    <div className="mt-4">
      <div className="flex justify-between text-xs mb-1" style={{ color: "var(--text-muted)" }}>
        <span>Bear {currencySymbol}{bear?.toFixed(0)}</span>
        <span>Base {currencySymbol}{iv.iv_base?.toFixed(0)}</span>
        <span>Bull {currencySymbol}{bull?.toFixed(0)}</span>
      </div>
      <div className="relative h-3 rounded-full" style={{ background: "var(--bg-border)" }}>
        {/* Bear to bull range */}
        <div className="absolute inset-0 rounded-full" style={{ background: "linear-gradient(90deg, #f85149 0%, #e3b341 50%, #3fb950 100%)" }} />
        {/* CMP marker */}
        <div
          className="absolute top-1/2 -translate-y-1/2 w-3 h-5 rounded"
          style={{
            left: `${cmpPct}%`,
            background: "white",
            boxShadow: "0 0 8px rgba(0,0,0,0.5)",
            transform: "translate(-50%, -50%)",
          }}
        />
      </div>
      <div className="flex justify-between text-xs mt-1" style={{ color: "var(--text-muted)" }}>
        <span>Bear</span>
        <span style={{ color: "#2188ff" }}>CMP: {fmt.price(cmp, currencySymbol)}</span>
        <span>Bull</span>
      </div>
    </div>
  );
}

// ─── AI Summary Panel ─────────────────────────────────────────
function AISummaryPanel({ symbol }: { symbol: string }) {
  const { data, error, isLoading, mutate } = useSWR(
    `ai-summary-${symbol}`,
    () => aiApi.getSummary(symbol) as any
  );
  const [generating, setGenerating] = useState(false);

  const handleGenerate = async () => {
    setGenerating(true);
    try {
      await aiApi.generateSummary(symbol);
      await mutate();
    } catch (e: any) {
      alert(e.message || "AI generation failed");
    } finally {
      setGenerating(false);
    }
  };

  return (
    <div className="card p-5">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <Brain className="w-4 h-4 text-blue-400" />
          <span className="font-semibold text-sm" style={{ color: "var(--text-primary)" }}>AI Research Summary</span>
        </div>
        <button
          className="btn btn-ghost text-xs"
          onClick={handleGenerate}
          disabled={generating}
        >
          {generating ? <RefreshCw className="w-3 h-3 animate-spin" /> : <RefreshCw className="w-3 h-3" />}
          {generating ? "Generating..." : "Generate"}
        </button>
      </div>

      {isLoading ? (
        <div className="space-y-2">
          <div className="skeleton h-4 w-full" />
          <div className="skeleton h-4 w-4/5" />
          <div className="skeleton h-4 w-3/4" />
        </div>
      ) : error ? (
        <div className="text-red-400 text-sm">Failed to load AI summary</div>
      ) : data?.content ? (
        <div>
          <div
            className="text-sm leading-relaxed whitespace-pre-wrap"
            style={{ color: "var(--text-secondary)" }}
          >
            {data.content}
          </div>
          <div className="mt-4 disclaimer-banner text-xs">
            {data.disclaimer}
          </div>
          <div className="mt-2 text-xs" style={{ color: "var(--text-muted)" }}>
            Generated: {data.generated_at ? new Date(data.generated_at).toLocaleString() : "—"} · Model: {data.model_used}
          </div>
        </div>
      ) : (
        <div className="text-center py-6" style={{ color: "var(--text-muted)" }}>
          <Brain className="w-8 h-8 mx-auto mb-2 opacity-30" />
          <p className="text-sm">No AI summary yet.</p>
          <p className="text-xs mt-1">Click "Generate" to create one using Gemini.</p>
        </div>
      )}
    </div>
  );
}

// ─── Main Page ────────────────────────────────────────────────
export default function StockDetailPage() {
  const params = useParams();
  const router = useRouter();
  const symbol = (params.symbol as string).toUpperCase();

  // WebSocket for live price
  useSymbolWebSocket(symbol);
  const liveQuote = useMarketStore((s) => s.quotes[symbol]);

  const { data: stock, isLoading } = useSWR(
    `stock-${symbol}`,
    () => stocksApi.getDetail(symbol) as any,
    { refreshInterval: 30_000 }
  );
  const { data: ratiosData } = useSWR(
    `ratios-${symbol}`,
    () => stocksApi.getRatios(symbol) as any
  );
  const { data: financialsData } = useSWR(
    `fins-${symbol}`,
    () => stocksApi.getFinancials(symbol) as any
  );
  const { data: candleData } = useSWR(
    `candles-${symbol}`,
    () => marketApi.getCandles(symbol, "1D", 365) as any,
    { revalidateOnFocus: false }
  );
  const candles = (candleData?.candles || []).map((c: any) => ({
    time: Math.floor(new Date(c.timestamp || c.time).getTime() / 1000),
    open: c.open, high: c.high, low: c.low, close: c.close, volume: c.volume,
  }));


  const cmp = liveQuote?.ltp ?? stock?.quote?.ltp ?? null;
  const changePct = liveQuote?.change_pct ?? stock?.quote?.change_pct ?? null;

  if (isLoading) {
    return (
      <div className="min-h-screen p-8" style={{ background: "var(--bg-base)" }}>
        <div className="skeleton h-8 w-48 mb-4" />
        <div className="skeleton h-32 w-full mb-4" />
        <div className="skeleton h-64 w-full" />
      </div>
    );
  }

  if (!stock) {
    return (
      <div className="min-h-screen flex items-center justify-center" style={{ background: "var(--bg-base)" }}>
        <div className="text-center">
          <div className="text-2xl mb-2" style={{ color: "var(--text-muted)" }}>Stock not found</div>
          <button className="btn btn-primary" onClick={() => router.back()}>Go Back</button>
        </div>
      </div>
    );
  }

  const sig = stock.signal;
  const iv = stock.intrinsic_value;
  const ml = stock.ml_scores;
  const ratios = ratiosData;
  const sigColor = (sig?.signal_color || "GREY") as keyof typeof signalConfig;
  const sigStyle = signalConfig[sigColor];

  return (
    <div className="min-h-screen" style={{ background: "var(--bg-base)" }}>
      {/* Topbar */}
      <div className="topbar">
        <button className="btn btn-ghost" onClick={() => router.back()}>
          <ArrowLeft className="w-4 h-4" /> Dashboard
        </button>
        <span style={{ color: "var(--text-muted)" }}>›</span>
        <span className="font-bold" style={{ color: "var(--text-primary)" }}>{symbol}</span>
        <div className="flex-1" />
        {liveQuote && (
          <span className="text-xs text-green-400 flex items-center gap-1">
            <span className="live-dot" /> LIVE
          </span>
        )}
      </div>

      <div className="main-content max-w-7xl mx-auto">
        {/* ── Header ── */}
        <div className="flex flex-col md:flex-row md:items-start gap-6 mb-6">
          <div className="flex-1">
            <div className="flex items-center gap-3 mb-1">
              <h1 className="text-2xl font-bold" style={{ color: "var(--text-primary)" }}>
                {stock.company_name}
              </h1>
              <span className="text-lg font-mono" style={{ color: "var(--text-muted)" }}>
                {symbol}
              </span>
            </div>
            <div className="flex flex-wrap items-center gap-2 text-xs" style={{ color: "var(--text-muted)" }}>
              {stock.sector?.sector && <span>{stock.sector.sector}</span>}
              {stock.sector?.industry && <><span>›</span><span>{stock.sector.industry}</span></>}
              {stock.is_nifty50 && (
                <span className="px-2 py-0.5 rounded" style={{ background: "rgba(33,136,255,0.1)", color: "#2188ff" }}>
                  NIFTY50
                </span>
              )}
              {stock.is_fno && (
                <span className="px-2 py-0.5 rounded" style={{ background: "rgba(227,179,65,0.1)", color: "#e3b341" }}>
                  F&O
                </span>
              )}
            </div>
          </div>

          {/* Price & Signal */}
          <div className="flex items-center gap-6">
            <div className="text-right">
              <div className="text-3xl font-bold font-mono" style={{ color: "var(--text-primary)" }}>
                {fmt.price(cmp, stock.currency_symbol)}
              </div>
              {changePct != null && (
                <div className={`text-sm font-mono font-semibold ${changePct >= 0 ? "price-up" : "price-down"}`}>
                  {changePct >= 0 ? "▲" : "▼"} {Math.abs(changePct).toFixed(2)}%
                </div>
              )}
            </div>
            {sig && (
              <div
                className="px-4 py-3 rounded-lg text-center"
                style={{ background: sigStyle.bg, border: `1px solid ${sigStyle.border}` }}
              >
                <div className="text-xs font-semibold uppercase tracking-widest" style={{ color: sigStyle.color }}>
                  {sig.signal_label}
                </div>
                {iv?.upside_pct != null && (
                  <div className="text-lg font-bold font-mono mt-1" style={{ color: sigStyle.color }}>
                    {iv.upside_pct >= 0 ? "+" : ""}{iv.upside_pct.toFixed(1)}%
                  </div>
                )}
                <div className="text-xs mt-1" style={{ color: "var(--text-muted)" }}>upside</div>
              </div>
            )}
          </div>
        </div>

        {/* ── Main Grid ── */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* Left column */}
          <div className="lg:col-span-2 space-y-6">

            {/* Price Chart */}
            <div className="card p-5">
              <div className="flex items-center gap-2 mb-4">
                <BarChart2 className="w-4 h-4 text-blue-400" />
                <span className="font-semibold text-sm" style={{ color: "var(--text-primary)" }}>
                  Price History — {symbol}
                </span>
              </div>
              <ErrorBoundary fallback="Chart unavailable">
                <PriceChart symbol={symbol} candles={candles} height={340} />
              </ErrorBoundary>
            </div>

            {/* Signal explanation */}
            {sig && (
              <div className="card p-5">
                <div className="flex items-center gap-2 mb-3">
                  <Activity className="w-4 h-4 text-blue-400" />
                  <span className="font-semibold text-sm" style={{ color: "var(--text-primary)" }}>Signal Analysis</span>
                </div>
                <div className="text-sm mb-3" style={{ color: "var(--text-secondary)" }}>
                  <strong style={{ color: sigStyle.color }}>{sig.signal_label}:</strong>{" "}
                  {sig.main_reason || "No reason computed yet"}
                </div>
                {sig.conditions && (
                  <div className="grid grid-cols-2 md:grid-cols-3 gap-2">
                    {Object.entries(sig.conditions).map(([k, v]) => (
                      <div key={k} className="flex items-center gap-2 text-xs">
                        <span style={{ color: v ? "#3fb950" : "#f85149" }}>{v ? "✓" : "✗"}</span>
                        <span style={{ color: "var(--text-muted)" }}>{k.replace(/_/g, " ")}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}

            {/* Valuation */}
            {iv && (
              <div className="card p-5">
                <div className="flex items-center gap-2 mb-4">
                  <BarChart2 className="w-4 h-4 text-blue-400" />
                  <span className="font-semibold text-sm" style={{ color: "var(--text-primary)" }}>
                    Intrinsic Value — {iv.primary_model}
                  </span>
                  {iv.valuation_confidence && (
                    <span
                      className="text-xs font-bold px-2 py-0.5 rounded ml-auto"
                      style={{
                        background: iv.valuation_confidence === "HIGH" ? "rgba(63,185,80,0.1)" : iv.valuation_confidence === "MEDIUM" ? "rgba(227,179,65,0.1)" : "rgba(248,81,73,0.1)",
                        color: iv.valuation_confidence === "HIGH" ? "#3fb950" : iv.valuation_confidence === "MEDIUM" ? "#e3b341" : "#f85149",
                      }}
                    >
                      {iv.valuation_confidence} CONFIDENCE
                    </span>
                  )}
                </div>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
                  <MetricCard label="Bear Case" value={fmt.price(iv.iv_bear, stock.currency_symbol)} sub="conservative" color="#f85149" />
                  <MetricCard label="Base Case" value={fmt.price(iv.iv_base, stock.currency_symbol)} sub="expected" color="#e3b341" />
                  <MetricCard label="Bull Case" value={fmt.price(iv.iv_bull, stock.currency_symbol)} sub="optimistic" color="#3fb950" />
                  <MetricCard label="Blended IV" value={fmt.price(iv.iv_blended, stock.currency_symbol)} sub="25/50/25 weighted" color="#2188ff" />
                </div>
                <ValuationRange iv={iv} cmp={cmp} currencySymbol={stock.currency_symbol} />
                <div className="mt-4 grid grid-cols-2 gap-4">
                  <MetricCard
                    label="Upside / Downside"
                    value={iv.upside_pct != null ? `${iv.upside_pct >= 0 ? "+" : ""}${iv.upside_pct.toFixed(1)}%` : "—"}
                    color={iv.upside_pct != null ? (iv.upside_pct >= 25 ? "#3fb950" : iv.upside_pct >= 0 ? "#e3b341" : "#f85149") : undefined}
                  />
                  <MetricCard
                    label="Margin of Safety"
                    value={iv.margin_of_safety != null ? `${(iv.margin_of_safety * 100).toFixed(1)}%` : "—"}
                    color={iv.margin_of_safety != null ? (iv.margin_of_safety >= 0.15 ? "#3fb950" : "#e3b341") : undefined}
                  />
                </div>
                <div className="disclaimer-banner mt-4 text-xs">
                  ⚠️ Intrinsic value is based on model assumptions. Not a guarantee. CMP and valuations change daily.
                </div>
              </div>
            )}

            {/* Ratios grid */}
            {ratios && (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                {/* Valuation ratios */}
                <div className="card p-5">
                  <h3 className="font-semibold text-sm mb-4" style={{ color: "var(--text-primary)" }}>Valuation</h3>
                  <RatioRow label="P/E (TTM)" value={fmt.num(ratios.valuation?.pe)} highlight={ratios.valuation?.pe != null && ratios.valuation.pe < 15 ? "good" : ratios.valuation?.pe > 40 ? "bad" : "neutral"} />
                  <RatioRow label="P/B" value={fmt.num(ratios.valuation?.pb)} highlight={ratios.valuation?.pb != null && ratios.valuation.pb < 2 ? "good" : ratios.valuation?.pb > 8 ? "bad" : "neutral"} />
                  <RatioRow label="EV/EBITDA" value={fmt.num(ratios.valuation?.ev_ebitda)} />
                  <RatioRow label="P/S" value={fmt.num(ratios.valuation?.ps)} />
                  <RatioRow label="PEG" value={fmt.num(ratios.valuation?.peg)} highlight={ratios.valuation?.peg != null && ratios.valuation.peg < 1 ? "good" : "neutral"} />
                </div>

                {/* Profitability */}
                <div className="card p-5">
                  <h3 className="font-semibold text-sm mb-4" style={{ color: "var(--text-primary)" }}>Profitability</h3>
                  <RatioRow label="ROE" value={fmt.pct(ratios.profitability?.roe, 100)} highlight={ratios.profitability?.roe != null && ratios.profitability.roe > 0.15 ? "good" : "neutral"} />
                  <RatioRow label="ROCE" value={fmt.pct(ratios.profitability?.roce, 100)} highlight={ratios.profitability?.roce != null && ratios.profitability.roce > 0.15 ? "good" : "neutral"} />
                  <RatioRow label="Net Margin" value={fmt.pct(ratios.profitability?.net_margin, 100)} />
                  <RatioRow label="EBITDA Margin" value={fmt.pct(ratios.profitability?.ebitda_margin, 100)} />
                  <RatioRow label="Operating Margin" value={fmt.pct(ratios.profitability?.operating_margin, 100)} />
                </div>

                {/* Growth */}
                <div className="card p-5">
                  <h3 className="font-semibold text-sm mb-4" style={{ color: "var(--text-primary)" }}>Growth</h3>
                  <RatioRow label="Revenue CAGR 3Y" value={fmt.pct(ratios.growth?.revenue_cagr_3y, 100)} highlight={ratios.growth?.revenue_cagr_3y != null && ratios.growth.revenue_cagr_3y > 0.1 ? "good" : ratios.growth?.revenue_cagr_3y < 0 ? "bad" : "neutral"} />
                  <RatioRow label="PAT CAGR 3Y" value={fmt.pct(ratios.growth?.pat_cagr_3y, 100)} highlight={ratios.growth?.pat_cagr_3y != null && ratios.growth.pat_cagr_3y > 0.1 ? "good" : ratios.growth?.pat_cagr_3y < 0 ? "bad" : "neutral"} />
                  <RatioRow label="EPS CAGR 3Y" value={fmt.pct(ratios.growth?.eps_cagr_3y, 100)} />
                  <RatioRow label="Revenue 1Y" value={fmt.pct(ratios.growth?.revenue_growth_1y, 100)} />
                  <RatioRow label="Margin Expansion 3Y" value={ratios.growth?.margin_expansion_3y != null ? `${(ratios.growth.margin_expansion_3y * 100).toFixed(1)}pp` : "—"} />
                </div>

                {/* Leverage */}
                <div className="card p-5">
                  <h3 className="font-semibold text-sm mb-4" style={{ color: "var(--text-primary)" }}>Leverage & Liquidity</h3>
                  <RatioRow label="Debt/Equity" value={fmt.num(ratios.leverage?.debt_equity)} highlight={ratios.leverage?.debt_equity != null && ratios.leverage.debt_equity < 0.5 ? "good" : ratios.leverage?.debt_equity > 2 ? "bad" : "neutral"} />
                  <RatioRow label="Debt/EBITDA" value={fmt.num(ratios.leverage?.debt_ebitda)} highlight={ratios.leverage?.debt_ebitda != null && ratios.leverage.debt_ebitda < 2 ? "good" : ratios.leverage?.debt_ebitda > 4 ? "bad" : "neutral"} />
                  <RatioRow label="Interest Coverage" value={fmt.num(ratios.leverage?.interest_coverage, 1)} highlight={ratios.leverage?.interest_coverage != null && ratios.leverage.interest_coverage > 5 ? "good" : ratios.leverage?.interest_coverage < 2 ? "bad" : "neutral"} />
                  <RatioRow label="CFO/PAT" value={fmt.num(ratios.efficiency?.cfo_pat)} highlight={ratios.efficiency?.cfo_pat != null && ratios.efficiency.cfo_pat > 0.9 ? "good" : ratios.efficiency?.cfo_pat < 0.5 ? "bad" : "neutral"} />
                  <RatioRow label="FCF Margin" value={fmt.pct(ratios.efficiency?.fcf_margin, 100)} />
                </div>
              </div>
            )}

            {/* Risk Flags */}
            {stock.risk_flags?.length > 0 && (
              <div className="card p-5">
                <div className="flex items-center gap-2 mb-4">
                  <AlertTriangle className="w-4 h-4 text-yellow-400" />
                  <span className="font-semibold text-sm" style={{ color: "var(--text-primary)" }}>
                    Risk Flags ({stock.risk_flags.length})
                  </span>
                </div>
                {stock.risk_flags.map((f: any, i: number) => (
                  <RiskFlagBadge key={i} severity={f.severity} description={f.description || f.flag_type} />
                ))}
              </div>
            )}
          </div>

          {/* Right column */}
          <div className="space-y-6">
            {/* ML Scores */}
            {ml && (
              <div className="card p-5">
                <div className="flex items-center gap-2 mb-4">
                  <Cpu className="w-4 h-4 text-blue-400" />
                  <span className="font-semibold text-sm" style={{ color: "var(--text-primary)" }}>ML Scores</span>
                </div>
                <div className="flex justify-around mb-4">
                  <ScoreGauge score={ml.fundamental_score} label="Fundamental" color="#2188ff" />
                  <ScoreGauge score={ml.growth_outlook_score} label="Growth" color="#3fb950" />
                  <ScoreGauge score={ml.risk_score} label="Risk" color={ml.risk_score <= 30 ? "#3fb950" : ml.risk_score <= 60 ? "#e3b341" : "#f85149"} />
                </div>
                <div className="text-center text-xs mt-2" style={{ color: "var(--text-muted)" }}>
                  Confidence: <strong style={{ color: ml.valuation_confidence === "HIGH" ? "#3fb950" : ml.valuation_confidence === "MEDIUM" ? "#e3b341" : "#f85149" }}>{ml.valuation_confidence || "—"}</strong>
                </div>
                {ml.risk_drivers?.length > 0 && (
                  <div className="mt-3">
                    <div className="text-xs font-semibold mb-2" style={{ color: "var(--text-muted)" }}>RISK DRIVERS</div>
                    {ml.risk_drivers.slice(0, 3).map((d: string, i: number) => (
                      <div key={i} className="text-xs py-1" style={{ color: "#e3b341" }}>⚠ {d}</div>
                    ))}
                  </div>
                )}
              </div>
            )}

            {/* Live quote details */}
            {(stock.quote || liveQuote) && (
              <div className="card p-5">
                <div className="flex items-center gap-2 mb-4">
                  <Activity className="w-4 h-4 text-green-400" />
                  <span className="font-semibold text-sm" style={{ color: "var(--text-primary)" }}>Market Data</span>
                </div>
                {(() => {
                  const q = liveQuote || stock.quote;
                  return (
                    <>
                      <RatioRow label="Open" value={fmt.price(q.open, stock.currency_symbol)} />
                      <RatioRow label="High" value={fmt.price(q.high, stock.currency_symbol)} />
                      <RatioRow label="Low" value={fmt.price(q.low, stock.currency_symbol)} />
                      <RatioRow label="Prev Close" value={fmt.price(q.close, stock.currency_symbol)} />
                      <RatioRow label="Volume" value={q.volume?.toLocaleString(stock.currency === "INR" ? "en-IN" : "en-US") || "—"} />
                      <RatioRow label="52W High" value={fmt.price(q.week_52_high, stock.currency_symbol)} />
                      <RatioRow label="52W Low" value={fmt.price(q.week_52_low, stock.currency_symbol)} />
                      {q.last_updated && (
                        <div className="text-xs mt-2" style={{ color: "var(--text-muted)" }}>
                          Updated: {new Date(q.last_updated).toLocaleTimeString()}
                        </div>
                      )}
                    </>
                  );
                })()}
              </div>
            )}

            {/* AI Summary */}
            <AISummaryPanel symbol={symbol} />
          </div>
        </div>

        {/* Disclaimer */}
        <div className="disclaimer-banner mt-8">
          ⚠️ <strong>Research Tool Only.</strong> All data, scores, and AI summaries are for personal research purposes.
          This is not investment advice. Intrinsic value calculations rely on model assumptions and historical data.
          Always verify independently before making financial decisions.
        </div>
      </div>
    </div>
  );
}
