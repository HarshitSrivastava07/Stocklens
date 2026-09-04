"use client";
import useSWR from "swr";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { ArrowLeft, Activity, TrendingUp, TrendingDown, Layers } from "lucide-react";

interface SectorCell {
  id: string;
  sector: string;
  macro_sector: string;
  total_stocks: number;
  green_count: number;
  yellow_count: number;
  red_count: number;
  grey_count: number;
  avg_upside_pct: number | null;
  avg_risk_score: number | null;
  top_stock: string | null;
}

function heatmapColor(upside: number | null, green: number, total: number): string {
  if (upside == null || total === 0) return "rgba(110,118,129,0.1)";
  const greenRatio = green / total;
  if (upside >= 20 && greenRatio >= 0.3) return "rgba(63,185,80,0.25)";
  if (upside >= 10 || greenRatio >= 0.2) return "rgba(63,185,80,0.12)";
  if (upside < 0 && greenRatio < 0.1) return "rgba(248,81,73,0.18)";
  return "rgba(227,179,65,0.12)";
}

function textColor(upside: number | null): string {
  if (upside == null) return "#6e7681";
  if (upside >= 15) return "#3fb950";
  if (upside >= 0) return "#e3b341";
  return "#f85149";
}

function cellSize(total: number): number {
  if (total > 100) return 140;
  if (total > 50) return 120;
  if (total > 20) return 100;
  return 90;
}

export default function SectorHeatmapPage() {
  const router = useRouter();
  const [view, setView] = useState<"heatmap" | "table">("heatmap");
  const [sortBy, setSortBy] = useState<"upside" | "green_pct" | "total">("upside");

  const { data, isLoading } = useSWR("sectors-heatmap", async () => {
    const res = await fetch("http://localhost:8000/api/v1/sectors");
    if (!res.ok) throw new Error("Failed to load sectors");
    return res.json();
  });

  const sectors: SectorCell[] = (data?.sectors || []).sort((a: SectorCell, b: SectorCell) => {
    if (sortBy === "upside") return (b.avg_upside_pct ?? -999) - (a.avg_upside_pct ?? -999);
    if (sortBy === "green_pct") {
      const ga = a.total_stocks > 0 ? a.green_count / a.total_stocks : 0;
      const gb = b.total_stocks > 0 ? b.green_count / b.total_stocks : 0;
      return gb - ga;
    }
    return b.total_stocks - a.total_stocks;
  });

  // Group by macro sector
  const macroGroups: Record<string, SectorCell[]> = {};
  sectors.forEach((s) => {
    const key = s.macro_sector || "Other";
    if (!macroGroups[key]) macroGroups[key] = [];
    macroGroups[key].push(s);
  });

  return (
    <div className="min-h-screen" style={{ background: "var(--bg-base)" }}>
      <div className="topbar">
        <button className="btn btn-ghost" onClick={() => router.push("/")}>
          <ArrowLeft className="w-4 h-4" /> Dashboard
        </button>
        <Layers className="w-5 h-5 text-blue-400" />
        <span className="font-bold" style={{ color: "var(--text-primary)" }}>Sector Heatmap</span>
        <div className="flex-1" />
        <div className="tabs" style={{ marginBottom: 0, width: "auto" }}>
          <button className={`tab ${view === "heatmap" ? "active" : ""}`} onClick={() => setView("heatmap")}>
            Heatmap
          </button>
          <button className={`tab ${view === "table" ? "active" : ""}`} onClick={() => setView("table")}>
            Table
          </button>
        </div>
      </div>

      <div className="main-content max-w-7xl mx-auto">
        {/* Sort controls */}
        <div className="flex items-center gap-4 mb-6">
          <span className="text-xs" style={{ color: "var(--text-muted)" }}>Sort by:</span>
          {[
            { key: "upside", label: "Avg Upside" },
            { key: "green_pct", label: "Green %" },
            { key: "total", label: "Stock Count" },
          ].map(({ key, label }) => (
            <button
              key={key}
              className={`text-xs px-3 py-1 rounded ${sortBy === key ? "text-blue-400" : ""}`}
              style={{
                background: sortBy === key ? "rgba(33,136,255,0.1)" : "var(--bg-surface)",
                border: `1px solid ${sortBy === key ? "rgba(33,136,255,0.3)" : "var(--bg-border)"}`,
                color: sortBy === key ? "#2188ff" : "var(--text-muted)",
                cursor: "pointer",
              }}
              onClick={() => setSortBy(key as any)}
            >
              {label}
            </button>
          ))}
        </div>

        {isLoading ? (
          <div className="grid grid-cols-4 gap-3">
            {[...Array(16)].map((_, i) => (
              <div key={i} className="skeleton h-28 rounded-lg" />
            ))}
          </div>
        ) : sectors.length === 0 ? (
          <div className="card p-16 text-center" style={{ color: "var(--text-muted)" }}>
            <Activity className="w-12 h-12 mx-auto mb-4 opacity-30" />
            <p className="font-semibold mb-1" style={{ color: "var(--text-primary)" }}>No sector data yet</p>
            <p className="text-sm">Import NSE stocks and run the signal pipeline.</p>
            <button className="btn btn-primary mt-4" onClick={() => router.push("/admin")}>
              Go to Admin
            </button>
          </div>
        ) : view === "heatmap" ? (
          /* ── HEATMAP VIEW ── */
          <div className="space-y-6">
            {Object.entries(macroGroups).map(([macro, cells]) => (
              <div key={macro}>
                <h2 className="text-xs font-bold uppercase tracking-widest mb-3"
                  style={{ color: "var(--text-muted)" }}>
                  {macro}
                </h2>
                <div className="flex flex-wrap gap-3">
                  {cells.map((s) => {
                    const size = cellSize(s.total_stocks);
                    const bg = heatmapColor(s.avg_upside_pct, s.green_count, s.total_stocks);
                    const tc = textColor(s.avg_upside_pct);
                    const greenPct = s.total_stocks > 0 ? Math.round((s.green_count / s.total_stocks) * 100) : 0;

                    return (
                      <div
                        key={s.id}
                        className="heatmap-cell"
                        style={{
                          width: size,
                          height: size,
                          background: bg,
                          border: `1px solid ${bg.replace("0.25", "0.4").replace("0.12", "0.25").replace("0.18", "0.35")}`,
                        }}
                        onClick={() => router.push(`/sectors/${s.id}`)}
                      >
                        <div className="font-bold text-center px-2"
                          style={{ fontSize: size > 110 ? 12 : 10, color: "var(--text-primary)", lineHeight: 1.3, marginBottom: 4 }}>
                          {s.sector}
                        </div>
                        <div className="font-bold font-mono" style={{ fontSize: 15, color: tc }}>
                          {s.avg_upside_pct != null
                            ? `${s.avg_upside_pct >= 0 ? "+" : ""}${s.avg_upside_pct.toFixed(1)}%`
                            : "—"}
                        </div>
                        <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 2 }}>
                          {s.total_stocks} stocks
                        </div>
                        <div style={{ fontSize: 10, marginTop: 3 }}>
                          <span style={{ color: "#3fb950" }}>■ {s.green_count}</span>
                          {" "}
                          <span style={{ color: "#f85149" }}>■ {s.red_count}</span>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>
        ) : (
          /* ── TABLE VIEW ── */
          <div className="card overflow-hidden">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Sector</th>
                  <th>Macro</th>
                  <th>Stocks</th>
                  <th>🟢 Green</th>
                  <th>🟡 Yellow</th>
                  <th>🔴 Red</th>
                  <th>Green %</th>
                  <th>Avg Upside</th>
                  <th>Avg Risk</th>
                </tr>
              </thead>
              <tbody>
                {sectors.map((s) => {
                  const greenPct = s.total_stocks > 0 ? (s.green_count / s.total_stocks) * 100 : 0;
                  return (
                    <tr key={s.id} onClick={() => router.push(`/sectors/${s.id}`)}>
                      <td className="primary">{s.sector}</td>
                      <td style={{ color: "var(--text-muted)", fontSize: 11 }}>{s.macro_sector}</td>
                      <td>{s.total_stocks}</td>
                      <td style={{ color: "#3fb950", fontWeight: 600 }}>{s.green_count}</td>
                      <td style={{ color: "#e3b341" }}>{s.yellow_count}</td>
                      <td style={{ color: "#f85149" }}>{s.red_count}</td>
                      <td>
                        <div className="progress-bar" style={{ width: 80 }}>
                          <div className="progress-fill" style={{ width: `${greenPct}%`, background: "#3fb950" }} />
                        </div>
                        <span style={{ fontSize: 10, color: "#3fb950" }}>{greenPct.toFixed(0)}%</span>
                      </td>
                      <td style={{
                        fontWeight: 600,
                        color: s.avg_upside_pct == null ? "var(--text-muted)" :
                          s.avg_upside_pct >= 15 ? "#3fb950" :
                          s.avg_upside_pct >= 0 ? "#e3b341" : "#f85149"
                      }}>
                        {s.avg_upside_pct != null
                          ? `${s.avg_upside_pct >= 0 ? "+" : ""}${s.avg_upside_pct.toFixed(1)}%`
                          : "—"}
                      </td>
                      <td style={{ color: "var(--text-muted)", fontSize: 11 }}>
                        {s.avg_risk_score?.toFixed(0) ?? "—"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        <div className="disclaimer-banner mt-6">
          ⚠️ Sector averages are based on model-computed intrinsic values and signals. Not investment advice.
        </div>
      </div>
    </div>
  );
}
