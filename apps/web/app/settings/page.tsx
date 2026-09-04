"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowLeft, Settings, Save, RefreshCw, Info, Key, Database, Cpu, Bell } from "lucide-react";

const SECTIONS = ["API Keys", "Data Sources", "Notifications", "Pipeline", "Display"] as const;
type Section = typeof SECTIONS[number];

export default function SettingsPage() {
  const router = useRouter();
  const [active, setActive] = useState<Section>("API Keys");
  const [saved, setSaved] = useState(false);

  const handleSave = () => {
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  };

  return (
    <div className="min-h-screen" style={{ background: "var(--bg-base)" }}>
      <div className="topbar">
        <button className="btn btn-ghost" onClick={() => router.push("/")}>
          <ArrowLeft className="w-4 h-4" /> Dashboard
        </button>
        <Settings className="w-5 h-5 text-blue-400" />
        <span className="font-bold" style={{ color: "var(--text-primary)" }}>Settings</span>
      </div>

      <div className="main-content max-w-4xl mx-auto">
        <div className="flex gap-6">
          {/* Sidebar nav */}
          <div className="w-44 shrink-0">
            <nav className="space-y-1">
              {SECTIONS.map((s) => (
                <button
                  key={s}
                  onClick={() => setActive(s)}
                  className="w-full text-left px-3 py-2 rounded text-sm transition-colors"
                  style={{
                    background: active === s ? "rgba(33,136,255,0.1)" : "transparent",
                    color: active === s ? "#2188ff" : "var(--text-muted)",
                    fontWeight: active === s ? 600 : 400,
                    border: "none",
                    cursor: "pointer",
                  }}
                >
                  {s}
                </button>
              ))}
            </nav>
          </div>

          {/* Content */}
          <div className="flex-1 space-y-5">
            {active === "API Keys" && (
              <>
                <SectionHeader icon={<Key />} title="API Keys" desc="Stored only in your .env file — never sent to browser" />
                <SettingGroup title="Upstox">
                  <InfoRow label="API Key" value="Set in .env → UPSTOX_API_KEY" />
                  <InfoRow label="Access Token" value="Rotates daily — set in .env → UPSTOX_ACCESS_TOKEN" />
                  <InfoRow label="Token refresh" value="POST http://localhost:8000/api/v1/market/auth/upstox/callback" />
                </SettingGroup>
                <SettingGroup title="Google Gemini AI">
                  <InfoRow label="API Key" value="Set in .env → GEMINI_API_KEY" />
                  <InfoRow label="Model" value="gemini-1.5-flash (fast) or gemini-1.5-pro (quality)" />
                  <InfoRow label="Cache TTL" value="4 hours (Redis + DB)" />
                </SettingGroup>
                <SettingGroup title="Database">
                  <InfoRow label="PostgreSQL" value="Set in .env → DATABASE_URL" />
                  <InfoRow label="Redis" value="Set in .env → REDIS_URL" />
                </SettingGroup>
              </>
            )}

            {active === "Data Sources" && (
              <>
                <SectionHeader icon={<Database />} title="Data Sources" desc="Configure where market and fundamental data comes from" />
                <SettingGroup title="Real-time Prices">
                  <InfoRow label="Source" value="Upstox WebSocket (NSE Level 1 quotes)" />
                  <InfoRow label="Worker" value="apps/worker/upstox_ws.py" />
                  <InfoRow label="Fallback" value="BSE Bhavcopy CSV (EOD only)" />
                </SettingGroup>
                <SettingGroup title="Fundamentals">
                  <InfoRow label="Primary" value="Screener.in Excel export (free, manual)" />
                  <InfoRow label="Auto (BSE)" value="BSE XML feeds (daily after 16:00)" />
                  <InfoRow label="Parser" value="scripts/parse_financials.py" />
                </SettingGroup>
                <SettingGroup title="Stock Universe">
                  <InfoRow label="Source" value="NSE EQUITY_L.csv (auto-download)" />
                  <InfoRow label="Script" value="python scripts/import_nse_symbols.py" />
                  <InfoRow label="Nifty50" value="Hardcoded list + auto-flagged" />
                </SettingGroup>
              </>
            )}

            {active === "Pipeline" && (
              <>
                <SectionHeader icon={<Cpu />} title="Data Pipeline" desc="Scheduled jobs running automatically in IST timezone" />
                <SettingGroup title="Daily Schedule (IST)">
                  {[
                    ["09:00", "Pre-market check"],
                    ["15:35", "EOD price snapshot"],
                    ["16:00", "Corporate actions"],
                    ["17:00", "BSE XML fundamentals"],
                    ["18:00", "Ratio engine (40+ ratios)"],
                    ["19:00", "Valuation engine (DCF, P/B, EV/EBITDA)"],
                    ["20:00", "Signal engine (Green/Yellow/Red/Grey)"],
                    ["20:30", "Alert evaluation"],
                    ["21:00", "AI summaries (stale only)"],
                  ].map(([time, job]) => (
                    <div key={time} className="flex justify-between py-2"
                      style={{ borderBottom: "1px solid var(--bg-border)" }}>
                      <span className="font-mono text-xs" style={{ color: "#2188ff" }}>{time}</span>
                      <span className="text-xs" style={{ color: "var(--text-secondary)" }}>{job}</span>
                    </div>
                  ))}
                </SettingGroup>
                <SettingGroup title="Weekly Schedule">
                  {[
                    ["Sun 02:00", "ML model retrain"],
                    ["Sun 23:00", "KMeans clustering"],
                  ].map(([time, job]) => (
                    <div key={time} className="flex justify-between py-2"
                      style={{ borderBottom: "1px solid var(--bg-border)" }}>
                      <span className="font-mono text-xs" style={{ color: "#e3b341" }}>{time}</span>
                      <span className="text-xs" style={{ color: "var(--text-secondary)" }}>{job}</span>
                    </div>
                  ))}
                </SettingGroup>
                <div className="text-xs mt-3" style={{ color: "var(--text-muted)" }}>
                  Manually trigger any job from the <button className="text-blue-400 underline" onClick={() => router.push("/admin")}>Admin Panel</button>.
                </div>
              </>
            )}

            {active === "Notifications" && (
              <>
                <SectionHeader icon={<Bell />} title="Notifications" desc="Alert evaluation runs every 30 minutes during market hours" />
                <SettingGroup title="Alert Types Available">
                  <InfoRow label="PRICE_ABOVE" value="Trigger when LTP rises above a level" />
                  <InfoRow label="PRICE_BELOW" value="Trigger when LTP falls below a level" />
                  <InfoRow label="SIGNAL_CHANGE_GREEN" value="Trigger when signal becomes Green" />
                  <InfoRow label="SIGNAL_CHANGE_RED" value="Trigger when signal becomes Red" />
                  <InfoRow label="UPSIDE_PCT_ABOVE" value="Trigger when modelled upside % exceeds threshold" />
                </SettingGroup>
                <div className="disclaimer-banner mt-3">
                  Alerts are evaluated against live prices from Upstox and model data. Accuracy depends on data freshness.
                </div>
              </>
            )}

            {active === "Display" && (
              <>
                <SectionHeader icon={<Settings />} title="Display" desc="UI preferences (stored in browser localStorage)" />
                <SettingGroup title="Dashboard Table">
                  <InfoRow label="Default sort" value="Signal color → Upside %" />
                  <InfoRow label="Rows per page" value="500 (all stocks by default)" />
                  <InfoRow label="Theme" value="Dark mode (fixed)" />
                </SettingGroup>
                <SettingGroup title="Valuation Assumptions">
                  <InfoRow label="Blended weights" value="Bear 25% + Base 50% + Bull 25%" />
                  <InfoRow label="Terminal growth" value="3-5% (scenario-dependent)" />
                  <InfoRow label="WACC range" value="11-14% (scenario-dependent)" />
                  <InfoRow label="Override via" value="Admin Panel → Data Override" />
                </SettingGroup>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function SectionHeader({ icon, title, desc }: { icon: React.ReactNode; title: string; desc: string }) {
  return (
    <div className="flex items-start gap-3 mb-6">
      <div className="w-8 h-8 rounded-lg flex items-center justify-center text-blue-400"
        style={{ background: "rgba(33,136,255,0.1)" }}>
        {icon}
      </div>
      <div>
        <h2 className="font-bold text-base" style={{ color: "var(--text-primary)" }}>{title}</h2>
        <p className="text-xs" style={{ color: "var(--text-muted)" }}>{desc}</p>
      </div>
    </div>
  );
}

function SettingGroup({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="card p-5 mb-4">
      <h3 className="text-xs font-bold uppercase tracking-widest mb-4" style={{ color: "var(--text-muted)" }}>
        {title}
      </h3>
      {children}
    </div>
  );
}

function InfoRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between items-center py-2" style={{ borderBottom: "1px solid var(--bg-border)" }}>
      <span className="text-xs" style={{ color: "var(--text-muted)" }}>{label}</span>
      <span className="text-xs font-mono" style={{ color: "var(--text-secondary)" }}>{value}</span>
    </div>
  );
}
