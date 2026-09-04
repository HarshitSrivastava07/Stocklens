"use client";
import useSWR from "swr";
import { useRouter, useParams } from "next/navigation";
import { ArrowLeft, ArrowRight } from "lucide-react";

export default function SectorDetailPage() {
  const { id } = useParams();
  const router = useRouter();

  const { data: sectorData } = useSWR(`sector-${id}`, async () => {
    const res = await fetch(`http://localhost:8000/api/v1/sectors/${id}`);
    return res.json();
  });

  const { data: stocksData, isLoading } = useSWR(`sector-stocks-${id}`, async () => {
    const res = await fetch(`http://localhost:8000/api/v1/sectors/${id}/stocks`);
    return res.json();
  });

  const stocks = stocksData?.stocks || [];
  const sector = sectorData;

  const signalColors: Record<string, string> = {
    GREEN: "#3fb950", YELLOW: "#e3b341", RED: "#f85149", GREY: "#6e7681",
  };

  return (
    <div className="min-h-screen" style={{ background: "var(--bg-base)" }}>
      <div className="topbar">
        <button className="btn btn-ghost" onClick={() => router.push("/sectors")}>
          <ArrowLeft className="w-4 h-4" /> Sectors
        </button>
        <span className="font-bold" style={{ color: "var(--text-primary)" }}>
          {sector?.sector || "Loading..."}
        </span>
        {sector?.macro_sector && (
          <span className="text-xs" style={{ color: "var(--text-muted)" }}>{sector.macro_sector}</span>
        )}
      </div>

      <div className="main-content max-w-6xl mx-auto">
        <div className="card overflow-hidden">
          <div className="px-5 py-3 border-b flex items-center justify-between" style={{ borderColor: "var(--bg-border)" }}>
            <span className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>
              {stocks.length} stocks in sector
            </span>
          </div>
          {isLoading ? (
            <div className="p-8 text-center" style={{ color: "var(--text-muted)" }}>Loading...</div>
          ) : (
            <table className="data-table">
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th>Company</th>
                  <th>Signal</th>
                  <th>CMP</th>
                  <th>IV (Blended)</th>
                  <th>Upside</th>
                  <th>Risk</th>
                  <th>Fundamental</th>
                </tr>
              </thead>
              <tbody>
                {stocks.map((s: any) => (
                  <tr key={s.nse_symbol} onClick={() => router.push(`/stocks/${s.nse_symbol}`)}>
                    <td className="primary">{s.nse_symbol}</td>
                    <td style={{ fontSize: 12, maxWidth: 160, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {s.company_name}
                    </td>
                    <td>
                      <span className="badge" style={{
                        background: `${signalColors[s.signal_color] || "#6e7681"}20`,
                        color: signalColors[s.signal_color] || "#6e7681",
                      }}>
                        {s.signal_label}
                      </span>
                    </td>
                    <td className="font-mono">
                      {s.ltp != null ? `₹${s.ltp.toLocaleString("en-IN", { minimumFractionDigits: 2 })}` : "—"}
                    </td>
                    <td className="font-mono" style={{ color: "#2188ff" }}>
                      {s.iv_blended != null ? `₹${s.iv_blended.toFixed(0)}` : "—"}
                    </td>
                    <td className="font-mono font-semibold" style={{
                      color: s.upside_pct == null ? "var(--text-muted)" :
                        s.upside_pct >= 25 ? "#3fb950" :
                        s.upside_pct >= 0 ? "#e3b341" : "#f85149"
                    }}>
                      {s.upside_pct != null ? `${s.upside_pct >= 0 ? "+" : ""}${s.upside_pct.toFixed(1)}%` : "—"}
                    </td>
                    <td style={{ fontSize: 11, color: s.risk_score > 60 ? "#f85149" : s.risk_score > 40 ? "#e3b341" : "#3fb950" }}>
                      {s.risk_score ?? "—"}
                    </td>
                    <td style={{ fontSize: 11, color: "var(--text-secondary)" }}>
                      {s.fundamental_score ?? "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );
}
