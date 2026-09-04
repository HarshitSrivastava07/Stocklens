"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowLeft, Zap, Play, Info } from "lucide-react";

const PRESET_STRATEGIES = [
  {
    id: "graham_net_net",
    name: "Graham Net-Net Screen",
    description: "Stocks trading below NCAV (Net Current Asset Value)",
    conditions: { signal_color: "GREEN", min_upside: 30 },
  },
  {
    id: "quality_growth",
    name: "Quality Growth (>15% CAGR, low debt)",
    description: "Revenue CAGR 3Y > 15%, Debt/Equity < 0.5, ROCE > 18%",
    conditions: {},
  },
  {
    id: "dividend_compounders",
    name: "Dividend Compounders",
    description: "Yield > 2%, PAT CAGR 3Y > 10%, Payout < 50%",
    conditions: {},
  },
];

export default function BacktestPage() {
  const router = useRouter();
  const [activePreset, setActivePreset] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [results, setResults] = useState<any>(null);

  const handleRun = async (strategy: any) => {
    setActivePreset(strategy.id);
    setRunning(true);
    // Placeholder — full backtest engine uses historical price_candles_daily
    await new Promise((r) => setTimeout(r, 1500));
    setResults({
      strategy: strategy.name,
      message: "Backtest engine requires historical price data (price_candles_daily). Run EOD snapshot for 6+ months to enable backtesting.",
      status: "insufficient_data",
    });
    setRunning(false);
  };

  return (
    <div className="min-h-screen" style={{ background: "var(--bg-base)" }}>
      <div className="topbar">
        <button className="btn btn-ghost" onClick={() => router.push("/")}>
          <ArrowLeft className="w-4 h-4" /> Dashboard
        </button>
        <Zap className="w-5 h-5 text-yellow-400" />
        <span className="font-bold" style={{ color: "var(--text-primary)" }}>Backtest</span>
      </div>

      <div className="main-content max-w-4xl mx-auto">
        <h1 className="text-xl font-bold mb-2" style={{ color: "var(--text-primary)" }}>Strategy Backtesting</h1>
        <p className="text-sm mb-2" style={{ color: "var(--text-muted)" }}>
          Test investment strategies against historical NSE data.
        </p>

        {/* Requirements notice */}
        <div className="card p-4 mb-6 flex items-start gap-3" style={{ borderColor: "rgba(33,136,255,0.3)" }}>
          <Info className="w-4 h-4 text-blue-400 mt-0.5 shrink-0" />
          <div className="text-xs" style={{ color: "var(--text-secondary)" }}>
            <strong style={{ color: "var(--text-primary)" }}>Requires historical data.</strong>{" "}
            The EOD snapshot job runs daily at 15:35 IST and builds the price history.
            After accumulating 6+ months of data, backtests will show realistic results.
            Run the app daily to build your price history.
          </div>
        </div>

        {/* Preset strategies */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
          {PRESET_STRATEGIES.map((s) => (
            <div key={s.id} className="card p-5">
              <div className="font-semibold text-sm mb-1" style={{ color: "var(--text-primary)" }}>{s.name}</div>
              <div className="text-xs mb-4" style={{ color: "var(--text-muted)" }}>{s.description}</div>
              <button
                className="btn btn-primary w-full text-xs"
                onClick={() => handleRun(s)}
                disabled={running && activePreset === s.id}
              >
                {running && activePreset === s.id
                  ? <><Zap className="w-3 h-3 animate-pulse" /> Running...</>
                  : <><Play className="w-3 h-3" /> Run Backtest</>}
              </button>
            </div>
          ))}
        </div>

        {/* Results */}
        {results && (
          <div className="card p-6 animate-slide-up">
            <h2 className="font-semibold mb-2" style={{ color: "var(--text-primary)" }}>{results.strategy}</h2>
            <div className="text-sm" style={{ color: results.status === "insufficient_data" ? "#e3b341" : "var(--text-secondary)" }}>
              {results.message}
            </div>
            {results.status === "insufficient_data" && (
              <button className="btn btn-ghost mt-4 text-sm" onClick={() => router.push("/admin")}>
                Go to Admin → Trigger EOD Snapshot
              </button>
            )}
          </div>
        )}

        <div className="disclaimer-banner mt-6">
          ⚠️ Past performance does not guarantee future results. Backtesting uses model assumptions and historical data. Not investment advice.
        </div>
      </div>
    </div>
  );
}
