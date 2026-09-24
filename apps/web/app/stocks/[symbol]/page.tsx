"use client";

/**
 * Stock detail page.
 *
 * Six tabs over one symbol: the chart and headline verdict, the valuation with
 * its full workings, ten years of filings, the technical state, sector peers,
 * and the user's own research notes.
 *
 * The page never invents a value. Where the backend reports a block as
 * unavailable it renders the stated reason instead of an empty card, because
 * "no valuation yet" and "valuation refused: three years of filings" are
 * different facts and the second one is the one worth reading.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import useSWR from "swr";
import {
  ArrowLeft, BookOpen, Calculator, ExternalLink, Info, LineChart,
  Loader2, Plus, RefreshCw, Trash2, Users,
} from "lucide-react";

import { researchApi, type ChartRange } from "@/lib/api";
import StockChart from "@/components/StockChart";
import ErrorBoundary from "@/components/ErrorBoundary";
import {
  ActionBadge, ConfidencePill, NotAvailable, ScoreBar, StatTile,
  ValuationBand, WarningList, fmtCompact, fmtMoney, fmtPct,
} from "@/components/stock/pieces";

const CURRENCY: Record<string, string> = {
  INR: "₹", USD: "$", GBP: "£", EUR: "€", JPY: "¥", HKD: "HK$", AUD: "A$",
};

type Tab = "overview" | "valuation" | "financials" | "technicals" | "peers" | "notes";

const TABS: { id: Tab; label: string; Icon: typeof LineChart }[] = [
  { id: "overview", label: "Overview", Icon: LineChart },
  { id: "valuation", label: "Valuation", Icon: Calculator },
  { id: "financials", label: "Financials", Icon: BookOpen },
  { id: "technicals", label: "Technicals", Icon: RefreshCw },
  { id: "peers", label: "Peers", Icon: Users },
  { id: "notes", label: "Research", Icon: BookOpen },
];

export default function StockDetailPage() {
  const params = useParams();
  const router = useRouter();
  const symbol = String(params?.symbol ?? "").toUpperCase();

  const [tab, setTab] = useState<Tab>("overview");
  const [range, setRange] = useState<ChartRange>("1Y");

  const { data: overview, error: overviewError, isLoading } = useSWR<any>(
    symbol ? ["overview", symbol] : null,
    () => researchApi.getOverview(symbol),
    { refreshInterval: 60_000, revalidateOnFocus: true },
  );

  const { data: chart, isLoading: chartLoading } = useSWR<any>(
    symbol ? ["chart", symbol, range] : null,
    () => researchApi.getChart(symbol, range),
    { keepPreviousData: true },
  );

  const currency = CURRENCY[overview?.stock?.currency ?? "INR"] ?? "₹";
  const quote = overview?.quote;
  const valuation = overview?.valuation;
  const signal = overview?.signal;

  if (overviewError) {
    return (
      <div className="app-content">
        <button className="btn btn-ghost" onClick={() => router.back()}>
          <ArrowLeft className="w-4 h-4" /> Back
        </button>
        <NotAvailable
          title={`Could not load ${symbol}`}
          reason={String(overviewError?.message ?? overviewError)}
          hint="Check that the symbol exists and the API is reachable."
        />
      </div>
    );
  }

  return (
    <ErrorBoundary>
      <div className="app-content stock-page">
        {/* ── Header ───────────────────────────────────────── */}
        <div className="stock-header">
          <button className="btn btn-ghost" onClick={() => router.back()}>
            <ArrowLeft className="w-4 h-4" /> Back
          </button>

          <div className="stock-header-main">
            <div>
              <div className="stock-symbol-row">
                <h1 className="stock-symbol">{symbol}</h1>
                {signal?.available && <ActionBadge action={signal.action} size="lg" />}
              </div>
              <div className="stock-name">
                {isLoading ? "Loading…" : overview?.stock?.name ?? "—"}
                {overview?.stock?.sector && (
                  <span className="stock-sector"> · {overview.stock.sector}</span>
                )}
                {overview?.stock?.exchange && (
                  <span className="stock-sector"> · {overview.stock.exchange}</span>
                )}
              </div>
            </div>

            <div className="stock-price-block">
              <div className="stock-price">{fmtMoney(quote?.price, currency)}</div>
              <div
                className={
                  (quote?.change_pct ?? 0) >= 0 ? "price-up" : "price-down"
                }
              >
                {fmtMoney(quote?.change_abs, currency)} ({fmtPct(quote?.change_pct)})
              </div>
              {/* Staleness is stated, not hidden: a price the reader cannot tell
                  is two days old is more dangerous than a missing one. */}
              {quote?.available && (
                <div className={`quote-freshness${quote.is_stale ? " is-stale" : ""}`}>
                  {quote.is_stale ? "Stale — " : ""}
                  {quote.age_minutes != null
                    ? `updated ${formatAge(quote.age_minutes)} ago`
                    : "update time unknown"}
                  {quote.source && ` · ${quote.source}`}
                </div>
              )}
            </div>
          </div>
        </div>

        {/* ── Tabs ─────────────────────────────────────────── */}
        <div className="tab-bar" role="tablist">
          {TABS.map(({ id, label, Icon }) => (
            <button
              key={id}
              role="tab"
              aria-selected={tab === id}
              className={`tab${tab === id ? " tab-active" : ""}`}
              onClick={() => setTab(id)}
            >
              <Icon className="w-4 h-4" aria-hidden /> {label}
            </button>
          ))}
        </div>

        {tab === "overview" && (
          <OverviewTab
            symbol={symbol}
            overview={overview}
            chart={chart}
            chartLoading={chartLoading}
            range={range}
            setRange={setRange}
            currency={currency}
            isLoading={isLoading}
          />
        )}
        {tab === "valuation" && <ValuationTab symbol={symbol} currency={currency} />}
        {tab === "financials" && <FinancialsTab symbol={symbol} currency={currency} />}
        {tab === "technicals" && (
          <TechnicalsTab tech={overview?.technicals} currency={currency} />
        )}
        {tab === "peers" && <PeersTab symbol={symbol} currency={currency} />}
        {tab === "notes" && <NotesTab symbol={symbol} currency={currency} />}

        <div className="disclaimer-banner">
          <Info className="w-4 h-4" aria-hidden />
          <span>
            Research output, not investment advice. Every figure is derived from
            reported filings and market data; assumptions are shown on the
            Valuation tab so you can disagree with a specific number rather than
            the conclusion.
          </span>
        </div>
      </div>
    </ErrorBoundary>
  );
}

function formatAge(minutes: number): string {
  if (minutes < 1) return "seconds";
  if (minutes < 60) return `${Math.round(minutes)} min`;
  if (minutes < 1440) return `${(minutes / 60).toFixed(1)} h`;
  return `${(minutes / 1440).toFixed(1)} days`;
}

// ─────────────────────────────────────────────────────────────
// Overview
// ─────────────────────────────────────────────────────────────
function OverviewTab({
  symbol, overview, chart, chartLoading, range, setRange, currency, isLoading,
}: any) {
  const quote = overview?.quote;
  const valuation = overview?.valuation;
  const signal = overview?.signal;
  const tech = overview?.technicals;
  const coverage = overview?.coverage;

  return (
    <div className="tab-panel">
      <StockChart
        symbol={symbol}
        candles={chart?.candles ?? []}
        range={range}
        onRangeChange={setRange}
        currencySymbol={currency}
        intrinsicValue={valuation?.available ? valuation.intrinsic_value : null}
        entryLow={signal?.available ? signal.plan?.entry_low : null}
        entryHigh={signal?.available ? signal.plan?.entry_high : null}
        loading={chartLoading}
        resampled={chart?.resampled}
        sessionsInRange={chart?.sessions_in_range ?? chart?.sessions_available}
        sessionsStored={chart?.sessions_stored}
      />

      {/* ── The verdict ─────────────────────────────────── */}
      {signal?.available ? (
        <div className="card verdict-card">
          <div className="verdict-head">
            <ActionBadge action={signal.action} size="lg" />
            <div>
              <div className="verdict-headline">{signal.headline}</div>
              <div className="verdict-meta">
                Conviction {(signal.conviction * 100).toFixed(0)}%
                {valuation?.available && (
                  <>
                    {" · "}
                    <ConfidencePill
                      confidence={valuation.confidence}
                      years={valuation.years_of_history}
                    />
                  </>
                )}
              </div>
            </div>
          </div>

          <div className="verdict-body">
            <div className="verdict-reasoning">
              <h4>Why</h4>
              <ul>
                {(signal.rationale ?? []).map((r: string, i: number) => (
                  <li key={i}>{r}</li>
                ))}
              </ul>

              <h4>What would change this view</h4>
              <ul className="invalidation-list">
                {(signal.invalidation ?? []).map((r: string, i: number) => (
                  <li key={i}>{r}</li>
                ))}
              </ul>

              {!!signal.risk_flags?.length && (
                <>
                  <h4>Risk flags</h4>
                  <ul className="risk-flag-list">
                    {signal.risk_flags.map((r: string, i: number) => (
                      <li key={i}>{r}</li>
                    ))}
                  </ul>
                </>
              )}
            </div>

            <div className="verdict-scores">
              <h4>Scores</h4>
              <ScoreBar label="Value" value={signal.scores?.value}
                hint="How far below intrinsic value, scaled by how much the valuation can be trusted" />
              <ScoreBar label="Quality" value={signal.scores?.quality}
                hint="Returns on capital, margin stability, cash generation and leverage" />
              <ScoreBar label="Momentum" value={signal.scores?.momentum}
                hint="Trend and price action — a timing input only" />
              <ScoreBar label="Risk" value={signal.scores?.risk} inverted
                hint="Leverage, loss years, volatility and valuation fragility" />
              <div className="composite-row">
                <span>Composite</span>
                <b>{signal.scores?.composite?.toFixed(0) ?? "—"}</b>
              </div>
            </div>
          </div>

          {/* ── The trade plan, in prices ───────────────── */}
          {signal.plan?.max_buy_price != null && (
            <div className="trade-plan">
              <h4>Plan</h4>
              <div className="trade-plan-grid">
                <StatTile label="Buy below" value={fmtMoney(signal.plan.max_buy_price, currency)}
                  sub="Intrinsic value less the margin of safety" />
                <StatTile label="Entry zone"
                  value={`${fmtMoney(signal.plan.entry_low, currency)} – ${fmtMoney(signal.plan.entry_high, currency)}`} />
                <StatTile label="Stop loss" value={fmtMoney(signal.plan.stop_loss, currency)}
                  sub="2.5× ATR below price" />
                <StatTile label="Target 1" value={fmtMoney(signal.plan.target_1, currency)} />
                <StatTile label="Target 2" value={fmtMoney(signal.plan.target_2, currency)} />
                <StatTile label="Risk / reward"
                  value={signal.plan.risk_reward != null ? `${signal.plan.risk_reward.toFixed(2)}×` : "—"} />
                <StatTile label="Position size"
                  value={signal.plan.position_size_pct != null ? `${signal.plan.position_size_pct.toFixed(1)}%` : "—"}
                  sub="Of portfolio" />
                <StatTile label="Horizon" value={signal.plan.horizon || "—"} />
              </div>
            </div>
          )}
        </div>
      ) : (
        <NotAvailable
          title="No signal yet"
          reason={signal?.reason}
          hint="Signals are produced once a valuation and a technical snapshot exist for this stock."
        />
      )}

      {/* ── Valuation summary ───────────────────────────── */}
      {valuation?.available ? (
        <div className="card">
          <div className="card-head">
            <h3>Intrinsic value</h3>
            <ConfidencePill confidence={valuation.confidence} years={valuation.years_of_history} />
          </div>

          <div className="iv-summary">
            <div className="iv-hero">
              <div className="iv-hero-value">
                {fmtMoney(valuation.intrinsic_value, currency)}
              </div>
              <div
                className={
                  (valuation.upside_pct ?? 0) >= 0 ? "price-up" : "price-down"
                }
              >
                {fmtPct(valuation.upside_pct)} vs price
              </div>
              <div className="iv-hero-sub">
                {valuation.primary_model?.replace(/_/g, " ")} ·{" "}
                margin of safety {fmtPct(valuation.margin_of_safety, 1, false)}
              </div>
            </div>
            <ValuationBand
              bear={valuation.bear} base={valuation.base} bull={valuation.bull}
              price={quote?.price} currency={currency}
            />
          </div>

          <WarningList warnings={valuation.warnings} />
        </div>
      ) : (
        <NotAvailable
          title="No valuation"
          reason={valuation?.reason}
          hint="The engine refuses to publish a value it cannot defend from the filings."
        />
      )}

      {/* ── Key numbers ─────────────────────────────────── */}
      <div className="stat-grid">
        <StatTile label="Market cap" value={fmtCompact(quote?.market_cap, currency)} />
        <StatTile label="Day range"
          value={`${fmtMoney(quote?.low, currency)} – ${fmtMoney(quote?.high, currency)}`} />
        <StatTile label="52-week range"
          value={`${fmtMoney(quote?.week_52_low, currency)} – ${fmtMoney(quote?.week_52_high, currency)}`} />
        <StatTile label="Volume" value={fmtCompact(quote?.volume)} />
        <StatTile label="Trend" value={(tech?.trend ?? "—").replace(/_/g, " ")} />
        <StatTile label="RSI (14)" value={tech?.rsi_14?.toFixed(1) ?? "—"}
          sub={tech?.rsi_14 == null ? undefined : tech.rsi_14 > 70 ? "Overbought" : tech.rsi_14 < 30 ? "Oversold" : "Neutral"} />
        <StatTile label="1-year return" value={fmtPct(tech?.return_1y, 1, false)} />
        <StatTile label="Filings stored" value={`${coverage?.annual_filings ?? 0} years`}
          sub={coverage?.has_full_decade ? "Full decade" : "Less than 10 years"} />
      </div>

      {overview?.stock?.summary && (
        <div className="card">
          <div className="card-head"><h3>About</h3></div>
          <p className="business-summary">{overview.stock.summary}</p>
          {overview.stock.website && (
            <a className="btn btn-ghost" href={overview.stock.website}
               target="_blank" rel="noopener noreferrer">
              Company website <ExternalLink className="w-3 h-3" />
            </a>
          )}
        </div>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────
// Valuation — the full workings
// ─────────────────────────────────────────────────────────────
function ValuationTab({ symbol, currency }: { symbol: string; currency: string }) {
  const { data, error, isLoading } = useSWR<any>(
    ["valuation", symbol],
    () => researchApi.getValuation(symbol),
  );

  if (isLoading) return <div className="skeleton" style={{ height: 320 }} />;
  if (error)
    return (
      <NotAvailable
        title="No valuation stored"
        reason={String(error?.message ?? error)}
        hint="Run the valuation stage of the pipeline for this symbol."
      />
    );

  const s = data?.summary;
  const coc = s?.cost_of_capital;
  const hist = data?.history_profile ?? {};
  const rdcf = data?.reverse_dcf ?? {};
  const base = data?.assumptions?.BASE;

  return (
    <div className="tab-panel">
      {/* Every assumption, on the page, so a reader can disagree with one
          number rather than with the conclusion. */}
      <div className="card">
        <div className="card-head">
          <h3>How this number was produced</h3>
          <ConfidencePill confidence={s?.confidence} years={s?.years_of_history} />
        </div>
        <p className="card-intro">
          Every assumption below is measured from this company's own reported
          history — not a market-wide default. The only inputs that are not are
          the risk-free rate and equity risk premium, which are market-wide by
          definition and shown with the cost of capital.
        </p>

        <div className="assumption-grid">
          <StatTile label="Revenue growth (year 1)" value={fmtPct(base?.growth_initial, 1, false)}
            sub="From the measured revenue CAGR" />
          <StatTile label="Terminal growth" value={fmtPct(base?.growth_terminal, 1, false)}
            sub="Capped at the risk-free rate" />
          <StatTile label="Operating margin" value={fmtPct(base?.operating_margin, 1, false)}
            sub="Median reported, with the measured trend" />
          <StatTile label="Tax rate" value={fmtPct(base?.tax_rate, 1, false)}
            sub="Median effective rate actually paid" />
          <StatTile label="Sales to capital" value={base?.sales_to_capital?.toFixed(2) ?? "—"}
            sub="Revenue per unit of invested capital" />
          <StatTile label="Terminal ROIC" value={fmtPct(base?.terminal_roic, 1, false)}
            sub="Its own ROIC, capped as competition arrives" />
        </div>
      </div>

      <div className="card">
        <div className="card-head"><h3>Cost of capital</h3></div>
        <div className="assumption-grid">
          <StatTile label="WACC" value={fmtPct(coc?.wacc, 2, false)} />
          <StatTile label="Cost of equity" value={fmtPct(coc?.cost_of_equity, 2, false)} />
          <StatTile label="Cost of debt" value={fmtPct(coc?.cost_of_debt, 2, false)}
            sub="Interest actually paid ÷ average debt" />
          <StatTile label="Beta" value={coc?.beta?.toFixed(2) ?? "—"}
            sub={coc?.beta_source === "regressed"
              ? "Regressed from real price history"
              : "Default — not enough overlapping history"} />
        </div>
      </div>

      {/* ── The measured history the assumptions came from ── */}
      <div className="card">
        <div className="card-head"><h3>What the company actually did</h3></div>
        <div className="assumption-grid">
          <StatTile label="Revenue CAGR" value={fmtPct(hist.revenue_cagr, 1, false)}
            sub={`over ${hist.years ?? "—"} years`} />
          <StatTile label="Operating margin (median)" value={fmtPct(hist.operating_margin_median, 1, false)} />
          <StatTile label="ROIC (median)" value={fmtPct(hist.roic_median, 1, false)} />
          <StatTile label="Effective tax rate" value={fmtPct(hist.effective_tax_rate, 1, false)} />
          <StatTile label="Profitable years" value={`${hist.profitable_years ?? "—"} / ${hist.years ?? "—"}`} />
          <StatTile label="FCF-positive years" value={`${hist.fcf_positive_years ?? "—"} / ${hist.years ?? "—"}`} />
          <StatTile label="Net debt" value={fmtCompact(hist.net_debt, currency)} />
          <StatTile label="Share count change" value={fmtPct(hist.share_count_cagr, 2, false)}
            sub="Positive means dilution" />
        </div>
      </div>

      {/* ── Reverse DCF: what the price already assumes ───── */}
      {rdcf?.interpretation && (
        <div className="card reverse-dcf">
          <div className="card-head"><h3>What today's price already assumes</h3></div>
          <p className="reverse-dcf-text">{rdcf.interpretation}</p>
          {rdcf.implied_growth_rate != null && (
            <div className="assumption-grid">
              <StatTile label="Growth the price implies"
                value={fmtPct(rdcf.implied_growth_rate, 1, false)} />
              <StatTile label="Growth actually delivered"
                value={fmtPct(rdcf.historical_growth_rate, 1, false)} />
            </div>
          )}
        </div>
      )}

      {/* ── Each model's independent answer ───────────────── */}
      <div className="card">
        <div className="card-head"><h3>Models</h3></div>
        <table className="data-table">
          <thead>
            <tr><th>Model</th><th className="num">Value per share</th><th className="num">Weight</th><th>What it does</th></tr>
          </thead>
          <tbody>
            {(data?.models ?? []).map((m: any) => (
              <tr key={m.model}>
                <td><b>{m.model.replace(/_/g, " ")}</b></td>
                <td className="num">{fmtMoney(m.value_per_share, currency)}</td>
                <td className="num">{(m.weight * 100).toFixed(0)}%</td>
                <td className="muted">{m.note}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* ── The projection, year by year ──────────────────── */}
      {!!data?.projection?.length && (
        <div className="card">
          <div className="card-head">
            <h3>Ten-year projection</h3>
            {s?.terminal_value_share != null && (
              <span className="muted-note">
                {(s.terminal_value_share * 100).toFixed(0)}% of value sits in the
                terminal assumption
              </span>
            )}
          </div>
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Year</th><th className="num">Growth</th><th className="num">Revenue</th>
                  <th className="num">EBIT</th><th className="num">NOPAT</th>
                  <th className="num">Reinvestment</th><th className="num">FCFF</th>
                  <th className="num">Present value</th>
                </tr>
              </thead>
              <tbody>
                {data.projection.map((p: any) => (
                  <tr key={p.year}>
                    <td>{p.year}</td>
                    <td className="num">{fmtPct(p.growth, 1, false)}</td>
                    <td className="num">{fmtCompact(p.revenue, currency)}</td>
                    <td className="num">{fmtCompact(p.ebit, currency)}</td>
                    <td className="num">{fmtCompact(p.nopat, currency)}</td>
                    <td className="num">{fmtCompact(p.reinvestment, currency)}</td>
                    <td className="num">{fmtCompact(p.fcff, currency)}</td>
                    <td className="num">{fmtCompact(p.present_value, currency)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <WarningList warnings={data?.warnings} />
    </div>
  );
}

// ─────────────────────────────────────────────────────────────
// Financials
// ─────────────────────────────────────────────────────────────
function FinancialsTab({ symbol, currency }: { symbol: string; currency: string }) {
  const [periodType, setPeriodType] = useState<"A" | "Q">("A");
  const { data, isLoading } = useSWR<any>(
    ["fundamentals", symbol, periodType],
    () => researchApi.getFundamentals(symbol, periodType),
    { keepPreviousData: true },
  );

  if (isLoading) return <div className="skeleton" style={{ height: 320 }} />;

  const periods: any[] = data?.periods ?? [];
  if (!periods.length) {
    return (
      <NotAvailable
        title="No filings stored"
        reason={data?.message}
        hint="Run the fundamentals stage of the pipeline for this symbol."
      />
    );
  }

  const rows: { key: string; label: string; fmt: (v: any) => string }[] = [
    { key: "revenue", label: "Revenue", fmt: (v) => fmtCompact(v, currency) },
    { key: "revenue_growth", label: "Revenue growth", fmt: (v) => fmtPct(v, 1, false) },
    { key: "ebitda", label: "EBITDA", fmt: (v) => fmtCompact(v, currency) },
    { key: "ebit", label: "Operating profit", fmt: (v) => fmtCompact(v, currency) },
    { key: "operating_margin", label: "Operating margin", fmt: (v) => fmtPct(v, 1, false) },
    { key: "pat", label: "Net profit", fmt: (v) => fmtCompact(v, currency) },
    { key: "net_margin", label: "Net margin", fmt: (v) => fmtPct(v, 1, false) },
    { key: "eps", label: "EPS", fmt: (v) => fmtMoney(v, currency) },
    { key: "cfo", label: "Operating cash flow", fmt: (v) => fmtCompact(v, currency) },
    { key: "capex", label: "Capex", fmt: (v) => fmtCompact(v, currency) },
    { key: "free_cash_flow", label: "Free cash flow", fmt: (v) => fmtCompact(v, currency) },
    { key: "net_worth", label: "Equity", fmt: (v) => fmtCompact(v, currency) },
    { key: "total_debt", label: "Total debt", fmt: (v) => fmtCompact(v, currency) },
    { key: "cash", label: "Cash", fmt: (v) => fmtCompact(v, currency) },
    { key: "shares_outstanding", label: "Shares outstanding", fmt: (v) => fmtCompact(v) },
  ];

  return (
    <div className="tab-panel">
      <div className="card">
        <div className="card-head">
          <h3>
            Reported filings
            <span className="muted-note"> · {periods.length} periods</span>
          </h3>
          <div className="chart-ranges">
            <button className={`chart-range-btn${periodType === "A" ? " is-active" : ""}`}
              onClick={() => setPeriodType("A")}>Annual</button>
            <button className={`chart-range-btn${periodType === "Q" ? " is-active" : ""}`}
              onClick={() => setPeriodType("Q")}>Quarterly</button>
          </div>
        </div>
        <p className="card-intro">
          As reported by the source. A period that did not report a line shows a
          dash — never a zero, which would silently drag every average toward it.
          Margins and growth are computed on read, so they cannot drift out of
          step with the filings above them.
        </p>

        <div className="table-scroll">
          <table className="data-table financials-table">
            <thead>
              <tr>
                <th className="sticky-col">Metric</th>
                {periods.map((p) => (
                  <th key={p.period_end} className="num">
                    {new Date(p.period_end).toLocaleDateString("en-GB", {
                      month: "short", year: "numeric",
                    })}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.key}>
                  <td className="sticky-col">{row.label}</td>
                  {periods.map((p) => (
                    <td key={p.period_end} className="num">
                      {p[row.key] == null ? "—" : row.fmt(p[row.key])}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────
// Technicals
// ─────────────────────────────────────────────────────────────
function TechnicalsTab({ tech, currency }: { tech: any; currency: string }) {
  if (!tech?.available) {
    return (
      <NotAvailable
        title="No technical snapshot"
        reason={tech?.reason}
        hint="Technicals are computed from stored price history."
      />
    );
  }

  return (
    <div className="tab-panel">
      {/* An indicator with too little history shows a dash, never a shorter
          window silently relabelled — a 50-day average presented as a 200-day
          one is a lie on a chart somebody trades from. */}
      {!!tech.warnings?.length && <WarningList warnings={tech.warnings} />}

      <div className="card">
        <div className="card-head">
          <h3>Trend</h3>
          <span className="muted-note">{tech.candles_used} sessions used</span>
        </div>
        <div className="stat-grid">
          <StatTile label="Trend" value={(tech.trend ?? "—").replace(/_/g, " ")} />
          <StatTile label="20-day average" value={fmtMoney(tech.sma_20, currency)} />
          <StatTile label="50-day average" value={fmtMoney(tech.sma_50, currency)} />
          <StatTile label="200-day average" value={fmtMoney(tech.sma_200, currency)}
            sub={tech.sma_200 == null ? "Not enough history" : undefined} />
        </div>
      </div>

      <div className="card">
        <div className="card-head"><h3>Momentum</h3></div>
        <div className="stat-grid">
          <StatTile label="RSI (14)" value={tech.rsi_14?.toFixed(1) ?? "—"}
            sub={tech.rsi_14 == null ? undefined
              : tech.rsi_14 > 70 ? "Overbought" : tech.rsi_14 < 30 ? "Oversold" : "Neutral"} />
          <StatTile label="MACD" value={tech.macd_line?.toFixed(3) ?? "—"}
            sub={tech.macd_histogram == null ? undefined
              : tech.macd_histogram > 0 ? "Above signal" : "Below signal"} />
          <StatTile label="Bollinger %B" value={tech.bollinger_percent_b?.toFixed(2) ?? "—"}
            sub="0 = lower band, 1 = upper band" />
          <StatTile label="ATR (14)" value={fmtMoney(tech.atr_14, currency)}
            sub={tech.atr_pct != null ? `${(tech.atr_pct * 100).toFixed(1)}% of price` : undefined} />
        </div>
      </div>

      <div className="card">
        <div className="card-head"><h3>Returns and risk</h3></div>
        <div className="stat-grid">
          <StatTile label="1 month" value={fmtPct(tech.return_1m, 1, false)} />
          <StatTile label="3 months" value={fmtPct(tech.return_3m, 1, false)} />
          <StatTile label="6 months" value={fmtPct(tech.return_6m, 1, false)} />
          <StatTile label="1 year" value={fmtPct(tech.return_1y, 1, false)} />
          <StatTile label="3-year CAGR" value={fmtPct(tech.return_3y_cagr, 1, false)} />
          <StatTile label="5-year CAGR" value={fmtPct(tech.return_5y_cagr, 1, false)} />
          <StatTile label="Volatility (1y)" value={fmtPct(tech.volatility_1y, 1, false)}
            sub="Annualised" />
          <StatTile label="Worst drawdown" value={fmtPct(tech.max_drawdown_5y, 1, false)}
            sub="Peak to trough, 5 years" />
          <StatTile label="Beta" value={tech.beta?.toFixed(2) ?? "—"}
            sub="Against the benchmark index" />
          <StatTile label="From 52-week high" value={fmtPct(tech.pct_from_52w_high, 1, false)} />
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────
// Peers
// ─────────────────────────────────────────────────────────────
function PeersTab({ symbol, currency }: { symbol: string; currency: string }) {
  const router = useRouter();
  const { data, isLoading } = useSWR<any>(["peers", symbol], () => researchApi.getPeers(symbol));

  if (isLoading) return <div className="skeleton" style={{ height: 280 }} />;
  if (!data?.peers?.length) {
    return <NotAvailable title="No peers found" reason={data?.message} />;
  }

  return (
    <div className="tab-panel">
      <div className="card">
        <div className="card-head">
          <h3>Sector peers</h3>
          <span className="muted-note">{data.sector}</span>
        </div>
        <div className="table-scroll">
          <table className="data-table">
            <thead>
              <tr>
                <th>Symbol</th><th>Company</th><th className="num">Price</th>
                <th className="num">Day</th><th className="num">Intrinsic value</th>
                <th className="num">Upside</th><th>Confidence</th>
                <th className="num">History</th><th>Signal</th>
              </tr>
            </thead>
            <tbody>
              {data.peers.map((p: any) => (
                <tr key={p.symbol} className="clickable-row"
                    onClick={() => router.push(`/stocks/${p.symbol}`)}>
                  <td><b>{p.symbol}</b></td>
                  <td className="muted">{p.name}</td>
                  <td className="num">{fmtMoney(p.price, currency)}</td>
                  <td className={`num ${(p.change_pct ?? 0) >= 0 ? "price-up" : "price-down"}`}>
                    {fmtPct(p.change_pct)}
                  </td>
                  <td className="num">{fmtMoney(p.intrinsic_value, currency)}</td>
                  <td className={`num ${(p.upside_pct ?? 0) >= 0 ? "price-up" : "price-down"}`}>
                    {fmtPct(p.upside_pct)}
                  </td>
                  <td>{p.confidence ?? "—"}</td>
                  <td className="num">{p.years_of_history ? `${p.years_of_history}y` : "—"}</td>
                  <td><ActionBadge action={p.action} size="sm" /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────
// Research notes
// ─────────────────────────────────────────────────────────────
function NotesTab({ symbol, currency }: { symbol: string; currency: string }) {
  const { data, isLoading, mutate } = useSWR<any>(
    ["notes", symbol],
    () => researchApi.listNotes(symbol),
  );

  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [tags, setTags] = useState("");
  const [stance, setStance] = useState<"BULLISH" | "BEARISH" | "NEUTRAL">("NEUTRAL");
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const save = useCallback(async () => {
    if (!body.trim()) return;
    setSaving(true);
    setErr(null);
    try {
      await researchApi.createNote(symbol, {
        body: body.trim(),
        title: title.trim() || undefined,
        tags: tags.split(",").map((t) => t.trim()).filter(Boolean),
        thesis_stance: stance,
      });
      setTitle(""); setBody(""); setTags(""); setStance("NEUTRAL"); setOpen(false);
      await mutate();
    } catch (e: any) {
      setErr(e?.message ?? "Could not save the note.");
    } finally {
      setSaving(false);
    }
  }, [symbol, title, body, tags, stance, mutate]);

  const remove = useCallback(
    async (id: string) => {
      await researchApi.deleteNote(symbol, id);
      await mutate();
    },
    [symbol, mutate],
  );

  return (
    <div className="tab-panel">
      <div className="card">
        <div className="card-head">
          <h3>Research notes</h3>
          <button className="btn btn-primary" onClick={() => setOpen((v) => !v)}>
            <Plus className="w-4 h-4" aria-hidden /> New note
          </button>
        </div>
        <p className="card-intro">
          Each note records the price and intrinsic value at the moment you wrote
          it, and those two figures are never editable. That is the point: a
          thesis reviewed a year from now should be read against what you
          actually knew when you formed it.
        </p>

        {open && (
          <div className="note-editor">
            <input className="input" placeholder="Title (optional)"
              value={title} onChange={(e) => setTitle(e.target.value)} />
            <textarea className="input note-textarea" rows={6}
              placeholder="What is the thesis? What would prove it wrong?"
              value={body} onChange={(e) => setBody(e.target.value)} />
            <input className="input" placeholder="Tags, comma separated"
              value={tags} onChange={(e) => setTags(e.target.value)} />
            <div className="note-editor-row">
              <div className="stance-picker" role="radiogroup" aria-label="Thesis stance">
                {(["BULLISH", "NEUTRAL", "BEARISH"] as const).map((s) => (
                  <button key={s} role="radio" aria-checked={stance === s}
                    className={`chart-range-btn${stance === s ? " is-active" : ""}`}
                    onClick={() => setStance(s)}>
                    {s.charAt(0) + s.slice(1).toLowerCase()}
                  </button>
                ))}
              </div>
              <button className="btn btn-primary" onClick={save} disabled={saving || !body.trim()}>
                {saving ? <Loader2 className="w-4 h-4 animate-spin" aria-hidden /> : null}
                Save note
              </button>
            </div>
            {err && <div className="note-error">{err}</div>}
          </div>
        )}

        {isLoading ? (
          <div className="skeleton" style={{ height: 120 }} />
        ) : !data?.notes?.length ? (
          <NotAvailable title="No notes yet"
            reason={`Nothing recorded for ${symbol}.`}
            hint="Write down the thesis while you still remember why you believed it." />
        ) : (
          <div className="note-list">
            {data.notes.map((n: any) => (
              <article key={n.id} className="note-card">
                <header className="note-card-head">
                  <div>
                    {n.title && <h4 className="note-card-title">{n.title}</h4>}
                    <div className="note-card-meta">
                      {new Date(n.created_at).toLocaleDateString("en-GB", {
                        day: "2-digit", month: "short", year: "numeric",
                      })}
                      {n.thesis_stance && (
                        <span className={`stance-tag stance-${n.thesis_stance.toLowerCase()}`}>
                          {n.thesis_stance}
                        </span>
                      )}
                    </div>
                  </div>
                  <button className="btn btn-ghost icon-only"
                    aria-label="Delete note" onClick={() => remove(n.id)}>
                    <Trash2 className="w-4 h-4" aria-hidden />
                  </button>
                </header>

                <p className="note-card-body">{n.body}</p>

                <footer className="note-card-foot">
                  <span>
                    At the time: price {fmtMoney(n.price_at_note, currency)}
                    {n.iv_at_note != null && <> · intrinsic value {fmtMoney(n.iv_at_note, currency)}</>}
                  </span>
                  {!!n.tags?.length && (
                    <span className="note-tags">
                      {n.tags.map((t: string) => (
                        <span key={t} className="badge">{t}</span>
                      ))}
                    </span>
                  )}
                </footer>
              </article>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
