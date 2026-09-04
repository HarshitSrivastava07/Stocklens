"use client";
import { useRouter } from "next/navigation";
import { useParams } from "next/navigation";
import useSWR from "swr";
import { ArrowLeft, Filter, TrendingUp } from "lucide-react";

export default function ScreenerResultPage() {
  const router = useRouter();
  const { id } = useParams();

  const { data, isLoading } = useSWR(`screener-${id}`, async () => {
    const res = await fetch(`http://localhost:8000/api/v1/screener/prebuilt/${id}`);
    return res.json();
  });

  const results: any[] = data?.results || [];

  const signalColors: Record<string, string> = {
    GREEN: "#3fb950", YELLOW: "#e3b341", RED: "#f85149", GREY: "#6e7681",
  };

  return (
    <div className="min-h-screen" style={{ background: "var(--bg-base)" }}>
      <div className="topbar">
        <button className="btn btn-ghost" onClick={() => router.push("/screener")}>
          <ArrowLeft className="w-4 h-4" /> Screener
        </button>
        <Filter className="w-4 h-4 text-blue-400" />
        <span className="font-bold" style={{ color: "var(--text-primary)" }}>
          {id?.toString().replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())}
        </span>
      </div>

      <div className="main-content max-w-6xl mx-auto">
        {isLoading ? (
          <div className="space-y-3">
            {[...Array(5)].map((_, i) => <div key={i} className="skeleton h-12 rounded" />)}
          </div>
        ) : results.length === 0 ? (
          <div className="card p-12 text-center" style={{ color: "var(--text-muted)" }}>
            <TrendingUp className="w-10 h-10 mx-auto mb-3 opacity-30" />
            <p className="font-semibold mb-1" style={{ color: "var(--text-primary)" }}>No results yet</p>
            <p className="text-sm">Run the signal engine and ratio engine first.</p>
            <button className="btn btn-primary mt-4" onClick={() => router.push("/admin")}>
              Admin → Trigger Pipeline
            </button>
          </div>
        ) : (
          <div className="card overflow-hidden">
            <div className="px-5 py-3 border-b flex items-center" style={{ borderColor: "var(--bg-border)" }}>
              <span className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>
                {results.length} stocks matched
              </span>
            </div>
            <table className="data-table">
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th>Company</th>
                  <th>Signal</th>
                  <th>CMP</th>
                  <th>Upside</th>
                  <th>Fundamental</th>
                  <th>Risk</th>
                  <th>Sector</th>
                </tr>
              </thead>
              <tbody>
                {results.map((s: any) => (
                  <tr key={s.nse_symbol} onClick={() => router.push(`/stocks/${s.nse_symbol}`)}>
                    <td className="primary">{s.nse_symbol}</td>
                    <td style={{ fontSize: 12 }}>{s.company_name}</td>
                    <td>
                      <span className="badge" style={{
                        background: `${signalColors[s.signal_color] || "#6e7681"}20`,
                        color: signalColors[s.signal_color] || "#6e7681",
                      }}>
                        {s.signal_label || s.signal_color}
                      </span>
                    </td>
                    <td className="font-mono">
                      {s.ltp != null ? `₹${Number(s.ltp).toLocaleString("en-IN", { minimumFractionDigits: 2 })}` : "—"}
                    </td>
                    <td className="font-mono font-semibold" style={{
                      color: s.upside_pct >= 25 ? "#3fb950" : s.upside_pct >= 0 ? "#e3b341" : "#f85149"
                    }}>
                      {s.upside_pct != null ? `${s.upside_pct >= 0 ? "+" : ""}${Number(s.upside_pct).toFixed(1)}%` : "—"}
                    </td>
                    <td style={{ fontSize: 11 }}>{s.fundamental_score ?? "—"}</td>
                    <td style={{ fontSize: 11, color: s.risk_score > 60 ? "#f85149" : "#e3b341" }}>
                      {s.risk_score ?? "—"}
                    </td>
                    <td style={{ fontSize: 11, color: "var(--text-muted)" }}>{s.sector || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
