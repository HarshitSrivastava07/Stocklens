"use client";
import useSWR from "swr";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { ArrowLeft, TrendingUp, Activity, BarChart2, AlertTriangle } from "lucide-react";

const TABS = ["OI Analysis", "PCR", "Option Chain", "F&O Eligible"] as const;

export default function FOPage() {
  const router = useRouter();
  const [tab, setTab] = useState<typeof TABS[number]>("F&O Eligible");
  const [symbol, setSymbol] = useState("NIFTY");

  const { data: eligible } = useSWR("fo-eligible", async () => {
    const res = await fetch("http://localhost:8000/api/v1/fo/eligible");
    return res.json();
  });

  const { data: oiData } = useSWR(
    symbol ? `fo-oi-${symbol}` : null,
    async () => {
      const res = await fetch(`http://localhost:8000/api/v1/fo/oi/${symbol}`);
      return res.json();
    }
  );

  const { data: pcrData } = useSWR(
    symbol ? `fo-pcr-${symbol}` : null,
    async () => {
      const res = await fetch(`http://localhost:8000/api/v1/fo/pcr/${symbol}`);
      return res.json();
    }
  );

  const { data: chainData } = useSWR(
    symbol && tab === "Option Chain" ? `fo-chain-${symbol}` : null,
    async () => {
      const res = await fetch(`http://localhost:8000/api/v1/fo/chain/${symbol}`);
      return res.json();
    }
  );

  return (
    <div className="min-h-screen" style={{ background: "var(--bg-base)" }}>
      <div className="topbar">
        <button className="btn btn-ghost" onClick={() => router.push("/")}>
          <ArrowLeft className="w-4 h-4" /> Dashboard
        </button>
        <Activity className="w-5 h-5 text-yellow-400" />
        <span className="font-bold" style={{ color: "var(--text-primary)" }}>F&O Analytics</span>
        <span className="text-xs px-2 py-0.5 rounded" style={{ background: "rgba(227,179,65,0.1)", color: "#e3b341" }}>
          Futures & Options
        </span>
      </div>

      <div className="main-content max-w-6xl mx-auto">
        {/* Symbol input + tabs */}
        <div className="flex flex-col sm:flex-row gap-4 mb-6">
          <input
            className="input"
            style={{ maxWidth: 200 }}
            placeholder="Symbol (e.g. RELIANCE)"
            value={symbol}
            onChange={(e) => setSymbol(e.target.value.toUpperCase().slice(0, 30))}
          />
          <div className="tabs" style={{ marginBottom: 0, flex: 1 }}>
            {TABS.map((t) => (
              <button key={t} className={`tab ${tab === t ? "active" : ""}`} onClick={() => setTab(t)}>
                {t}
              </button>
            ))}
          </div>
        </div>

        {/* F&O Eligible tab */}
        {tab === "F&O Eligible" && (
          <div className="card overflow-hidden">
            <div className="px-5 py-3 border-b" style={{ borderColor: "var(--bg-border)" }}>
              <span className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>
                {eligible?.count || 0} F&O Eligible NSE Stocks
              </span>
            </div>
            <div className="overflow-x-auto">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Symbol</th>
                    <th>Company</th>
                    <th>Market Cap</th>
                  </tr>
                </thead>
                <tbody>
                  {(eligible?.stocks || []).map((s: any) => (
                    <tr key={s.nse_symbol} onClick={() => router.push(`/stocks/${s.nse_symbol}`)}>
                      <td className="primary">{s.nse_symbol}</td>
                      <td style={{ fontSize: 12 }}>{s.company_name}</td>
                      <td style={{ fontSize: 11, color: "var(--text-muted)" }}>{s.market_cap_category}</td>
                    </tr>
                  ))}
                  {!eligible?.stocks?.length && (
                    <tr>
                      <td colSpan={3} className="text-center py-8" style={{ color: "var(--text-muted)" }}>
                        No F&O stocks found. Import NSE symbols and mark is_fno=true.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* OI Analysis */}
        {tab === "OI Analysis" && (
          <div className="card p-6">
            <div className="flex items-center gap-2 mb-4">
              <BarChart2 className="w-4 h-4 text-blue-400" />
              <span className="font-semibold text-sm" style={{ color: "var(--text-primary)" }}>
                Open Interest — {symbol}
              </span>
            </div>
            {oiData?.oi_data ? (
              <pre className="text-xs" style={{ color: "var(--text-secondary)" }}>
                {JSON.stringify(oiData.oi_data, null, 2)}
              </pre>
            ) : (
              <div className="text-center py-8" style={{ color: "var(--text-muted)" }}>
                <BarChart2 className="w-10 h-10 mx-auto mb-3 opacity-30" />
                <p className="text-sm font-semibold mb-1">OI data not available</p>
                <p className="text-xs">{oiData?.message || "Start the F&O WebSocket worker to stream OI data."}</p>
              </div>
            )}
          </div>
        )}

        {/* PCR */}
        {tab === "PCR" && (
          <div className="card p-6">
            <div className="flex items-center gap-2 mb-4">
              <TrendingUp className="w-4 h-4 text-green-400" />
              <span className="font-semibold text-sm" style={{ color: "var(--text-primary)" }}>
                Put-Call Ratio — {symbol}
              </span>
            </div>
            {pcrData?.pcr != null ? (
              <div className="flex items-center gap-6">
                <div className="metric-card" style={{ minWidth: 120 }}>
                  <div className="metric-label">PCR</div>
                  <div className="metric-value" style={{
                    color: pcrData.pcr < 0.7 ? "#f85149" : pcrData.pcr > 1.2 ? "#3fb950" : "#e3b341",
                    fontSize: 32,
                  }}>
                    {pcrData.pcr.toFixed(2)}
                  </div>
                </div>
                <div>
                  <div className="text-sm font-semibold mb-1" style={{ color: "var(--text-primary)" }}>
                    {pcrData.interpretation}
                  </div>
                  <div className="text-xs" style={{ color: "var(--text-muted)" }}>
                    PCR &lt; 0.7: Bearish sentiment (put buying) |
                    PCR 0.7–1.2: Neutral |
                    PCR &gt; 1.2: Bullish sentiment (call writing)
                  </div>
                </div>
              </div>
            ) : (
              <div className="text-center py-8" style={{ color: "var(--text-muted)" }}>
                <p className="text-sm">{pcrData?.message || "PCR data not available."}</p>
              </div>
            )}
          </div>
        )}

        {/* Option Chain */}
        {tab === "Option Chain" && (
          <div className="card p-6">
            <div className="flex items-center gap-2 mb-4">
              <Activity className="w-4 h-4 text-yellow-400" />
              <span className="font-semibold text-sm" style={{ color: "var(--text-primary)" }}>
                Option Chain — {symbol}
              </span>
            </div>
            {chainData?.chain ? (
              <div className="text-xs overflow-auto">
                <pre style={{ color: "var(--text-secondary)" }}>
                  {JSON.stringify(chainData.chain, null, 2)}
                </pre>
              </div>
            ) : (
              <div className="text-center py-8" style={{ color: "var(--text-muted)" }}>
                <AlertTriangle className="w-10 h-10 mx-auto mb-3 opacity-30" />
                <p className="text-sm font-semibold mb-1">Option chain not available</p>
                <p className="text-xs">{chainData?.message || "F&O WebSocket worker required."}</p>
              </div>
            )}
          </div>
        )}

        <div className="disclaimer-banner mt-6">
          ⚠️ F&O data requires an active Upstox subscription with F&O market data access.
          This section shows live data only when the F&O worker is running.
          Not investment advice — for research only.
        </div>
      </div>
    </div>
  );
}
