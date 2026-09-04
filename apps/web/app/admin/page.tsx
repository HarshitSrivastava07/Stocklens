"use client";
import useSWR, { mutate } from "swr";
import { useRouter } from "next/navigation";
import { adminApi } from "@/lib/api";
import { Play, RefreshCw, Upload, Clock, CheckCircle, XCircle, AlertCircle } from "lucide-react";
import { useState } from "react";

const JOBS = [
  { id: "eod_snapshot", name: "EOD Snapshot", desc: "Capture closing prices" },
  { id: "ratio_engine", name: "Ratio Engine", desc: "Compute all financial ratios" },
  { id: "valuation_engine", name: "Valuation Engine", desc: "Run DCF/PB-ROE/EV-EBITDA models" },
  { id: "signal_engine", name: "Signal Engine", desc: "Update Green/Yellow/Red signals" },
  { id: "ai_summaries", name: "AI Summaries", desc: "Refresh stale AI reports" },
  { id: "ml_clustering", name: "ML Clustering", desc: "Re-run KMeans clustering" },
  { id: "ml_retrain", name: "ML Retrain", desc: "Retrain risk & confidence models" },
  { id: "cleanup", name: "Cleanup", desc: "Remove old 1m candles (30d+)" },
];

function JobStatusBadge({ status }: { status: string }) {
  const config = {
    SUCCESS: { color: "#3fb950", icon: <CheckCircle className="w-3 h-3" /> },
    RUNNING: { color: "#2188ff", icon: <RefreshCw className="w-3 h-3 animate-spin" /> },
    FAILED: { color: "#f85149", icon: <XCircle className="w-3 h-3" /> },
    PENDING: { color: "#6e7681", icon: <Clock className="w-3 h-3" /> },
  };
  const c = config[status as keyof typeof config] || config.PENDING;
  return <span className="flex items-center gap-1 text-xs font-semibold" style={{ color: c.color }}>{c.icon}{status}</span>;
}

export default function AdminPage() {
  const router = useRouter();
  const { data: jobsData, mutate: refreshJobs } = useSWR("jobs", () => adminApi.getJobs() as any, { refreshInterval: 10000 });
  const { data: logsData } = useSWR("import-logs", () => adminApi.getAuditLogs() as any);
  const [triggering, setTriggering] = useState<string | null>(null);
  const [uploadStatus, setUploadStatus] = useState<string>("");

  const triggerJob = async (jobId: string) => {
    setTriggering(jobId);
    try {
      await adminApi.triggerJob(jobId);
      await refreshJobs();
    } catch (e: any) {
      alert(e.message);
    } finally {
      setTriggering(null);
    }
  };

  const handleFileUpload = async (type: "stocks" | "financials", e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploadStatus("Uploading...");
    const form = new FormData();
    form.append("file", file);
    try {
      const res = type === "stocks" ? await adminApi.importStocks(form) : await adminApi.uploadFinancials(form);
      setUploadStatus(`✓ Queued: ${(res as any).import_id}`);
    } catch (err: any) {
      setUploadStatus(`✗ ${err.message}`);
    }
  };

  return (
    <div className="min-h-screen" style={{ background: "var(--bg-base)" }}>
      <div className="topbar">
        <button className="btn btn-ghost" onClick={() => router.push("/")}>← Dashboard</button>
        <span className="font-bold" style={{ color: "var(--text-primary)" }}>Admin Panel</span>
      </div>
      <div className="main-content max-w-5xl mx-auto space-y-8">
        <div>
          <h1 className="text-xl font-bold mb-1" style={{ color: "var(--text-primary)" }}>Data Pipeline Jobs</h1>
          <p className="text-sm mb-4" style={{ color: "var(--text-muted)" }}>Manually trigger any scheduled job.</p>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {JOBS.map((job) => (
              <div key={job.id} className="card p-4 flex items-center justify-between">
                <div>
                  <div className="font-semibold text-sm" style={{ color: "var(--text-primary)" }}>{job.name}</div>
                  <div className="text-xs" style={{ color: "var(--text-muted)" }}>{job.desc}</div>
                </div>
                <button
                  className="btn btn-ghost"
                  onClick={() => triggerJob(job.id)}
                  disabled={triggering === job.id}
                >
                  {triggering === job.id ? <RefreshCw className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
                </button>
              </div>
            ))}
          </div>
        </div>

        {/* Data Import */}
        <div>
          <h2 className="text-lg font-bold mb-4" style={{ color: "var(--text-primary)" }}>Data Import</h2>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="card p-5">
              <div className="font-semibold mb-2" style={{ color: "var(--text-primary)" }}>Import NSE Symbols (CSV)</div>
              <div className="text-xs mb-3" style={{ color: "var(--text-muted)" }}>Upload EQUITY_L.csv from NSE archives</div>
              <label className="btn btn-ghost w-full cursor-pointer">
                <Upload className="w-4 h-4" /> Choose CSV File
                <input type="file" accept=".csv" className="hidden" onChange={(e) => handleFileUpload("stocks", e)} />
              </label>
            </div>
            <div className="card p-5">
              <div className="font-semibold mb-2" style={{ color: "var(--text-primary)" }}>Upload Financials (CSV/Excel)</div>
              <div className="text-xs mb-3" style={{ color: "var(--text-muted)" }}>Import financial results from Screener/BSE</div>
              <label className="btn btn-ghost w-full cursor-pointer">
                <Upload className="w-4 h-4" /> Choose File
                <input type="file" accept=".csv,.xls,.xlsx" className="hidden" onChange={(e) => handleFileUpload("financials", e)} />
              </label>
            </div>
          </div>
          {uploadStatus && <div className="mt-2 text-sm" style={{ color: uploadStatus.startsWith("✓") ? "#3fb950" : "#f85149" }}>{uploadStatus}</div>}
        </div>

        {/* Recent Jobs */}
        <div>
          <h2 className="text-lg font-bold mb-4" style={{ color: "var(--text-primary)" }}>Recent Job History</h2>
          <div className="card overflow-hidden">
            <table className="data-table">
              <thead><tr><th>Job</th><th>Status</th><th>Started</th><th>Duration</th><th>Stocks</th><th>Trigger</th></tr></thead>
              <tbody>
                {(jobsData?.jobs || []).slice(0, 20).map((j: any) => (
                  <tr key={j.id}>
                    <td className="primary">{j.job_name}</td>
                    <td><JobStatusBadge status={j.status} /></td>
                    <td style={{ fontSize: 11 }}>{j.started_at ? new Date(j.started_at).toLocaleString() : "—"}</td>
                    <td style={{ fontSize: 11 }}>{j.duration_ms ? `${j.duration_ms}ms` : "—"}</td>
                    <td style={{ fontSize: 11 }}>{j.stocks_processed || 0}</td>
                    <td style={{ fontSize: 11, color: "var(--text-muted)" }}>{j.triggered_by}</td>
                  </tr>
                ))}
                {!jobsData?.jobs?.length && (
                  <tr><td colSpan={6} className="text-center py-8" style={{ color: "var(--text-muted)" }}>No job history yet</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
}
