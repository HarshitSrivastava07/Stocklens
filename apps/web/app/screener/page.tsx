"use client";
import useSWR from "swr";
import { useRouter } from "next/navigation";
import { screenerApi } from "@/lib/api";
import { Filter, Play, Clock } from "lucide-react";

export default function ScreenerPage() {
  const router = useRouter();
  const { data } = useSWR("prebuilt-screeners", () => screenerApi.getPrebuilt() as any);
  const screeners = data?.screeners || [];

  return (
    <div className="min-h-screen" style={{ background: "var(--bg-base)" }}>
      <div className="topbar">
        <button className="btn btn-ghost" onClick={() => router.push("/")}>← Dashboard</button>
        <Filter className="w-5 h-5 text-blue-400" />
        <span className="font-bold" style={{ color: "var(--text-primary)" }}>Stock Screener</span>
      </div>
      <div className="main-content max-w-5xl mx-auto">
        <h1 className="text-xl font-bold mb-2" style={{ color: "var(--text-primary)" }}>Pre-Built Screeners</h1>
        <p className="text-sm mb-6" style={{ color: "var(--text-muted)" }}>
          Run curated screens on the full NSE universe. Results populate after nightly data pipeline.
        </p>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {screeners.map((s: any) => (
            <div key={s.id} className="card p-5 cursor-pointer" onClick={() => router.push(`/screener/${s.id}`)}>
              <div className="font-semibold mb-1" style={{ color: "var(--text-primary)" }}>{s.name}</div>
              <div className="text-xs mb-3" style={{ color: "var(--text-muted)" }}>{s.description}</div>
              <button className="btn btn-primary text-xs w-full">
                <Play className="w-3 h-3" /> Run Screen
              </button>
            </div>
          ))}
        </div>
        <div className="disclaimer-banner mt-8">
          ⚠️ Screener results are based on model-computed scores. Not investment advice.
        </div>
      </div>
    </div>
  );
}
