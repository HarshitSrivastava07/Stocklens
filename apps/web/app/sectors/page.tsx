"use client";
import useSWR from "swr";
import { useRouter } from "next/navigation";
import { ArrowLeft, Activity, TrendingUp } from "lucide-react";
import { sectorsApi } from "@/lib/api";

// Signal color dot
const dot = (color: string) => (
  <span className="w-2 h-2 rounded-full inline-block" style={{ background: color, marginRight: 4 }} />
);

export default function SectorsPage() {
  const router = useRouter();
  const { data, isLoading } = useSWR("sectors", () => sectorsApi.list() as any);
  const sectors: any[] = data?.sectors || [];

  return (
    <div className="min-h-screen" style={{ background: "var(--bg-base)" }}>
      <div className="topbar">
        <button className="btn btn-ghost" onClick={() => router.push("/")}>
          <ArrowLeft className="w-4 h-4" /> Dashboard
        </button>
        <Activity className="w-5 h-5 text-blue-400" />
        <span className="font-bold" style={{ color: "var(--text-primary)" }}>Sectors</span>
      </div>

      <div className="main-content max-w-5xl mx-auto">
        <h1 className="text-xl font-bold mb-2" style={{ color: "var(--text-primary)" }}>Sector Overview</h1>
        <p className="text-sm mb-6" style={{ color: "var(--text-muted)" }}>
          NSE sectors with signal distribution and top opportunities.
          Populated after running the signal engine.
        </p>

        {isLoading ? (
          <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
            {[...Array(9)].map((_, i) => (
              <div key={i} className="skeleton h-32 rounded-lg" />
            ))}
          </div>
        ) : sectors.length === 0 ? (
          <div className="card p-12 text-center" style={{ color: "var(--text-muted)" }}>
            <Activity className="w-12 h-12 mx-auto mb-4 opacity-30" />
            <p className="text-sm font-semibold mb-1">No sector data yet</p>
            <p className="text-xs">Import stocks and run the data pipeline from the Admin panel.</p>
            <button className="btn btn-primary mt-4 text-sm" onClick={() => router.push("/admin")}>
              Go to Admin Panel
            </button>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {sectors.map((s: any) => (
              <div
                key={s.id}
                className="card p-5 cursor-pointer"
                onClick={() => router.push(`/sectors/${s.id}`)}
              >
                <div className="font-semibold mb-1" style={{ color: "var(--text-primary)" }}>{s.sector}</div>
                <div className="text-xs mb-3" style={{ color: "var(--text-muted)" }}>{s.macro_sector}</div>
                <div className="flex gap-3 text-xs">
                  <span>{dot("#3fb950")}{s.green_count || 0} Green</span>
                  <span>{dot("#e3b341")}{s.yellow_count || 0} Yellow</span>
                  <span>{dot("#f85149")}{s.red_count || 0} Red</span>
                </div>
                {s.avg_upside_pct != null && (
                  <div className="mt-2 text-xs" style={{ color: s.avg_upside_pct >= 0 ? "#3fb950" : "#f85149" }}>
                    Avg upside: {s.avg_upside_pct >= 0 ? "+" : ""}{s.avg_upside_pct.toFixed(1)}%
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
