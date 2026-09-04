"use client";
import useSWR from "swr";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowLeft, TrendingUp, TrendingDown, Plus, Trash2, BarChart2 } from "lucide-react";
import { portfolioApi } from "@/lib/api";
import { useMarketStore } from "@/store/market";

const fmt = {
  price: (v: number | null) => v == null ? "—" : `₹${v.toLocaleString("en-IN", { minimumFractionDigits: 2 })}`,
  cr: (v: number | null) => v == null ? "—" : `₹${Math.abs(v).toLocaleString("en-IN", { minimumFractionDigits: 0, maximumFractionDigits: 0 })}`,
  pct: (v: number | null) => v == null ? "—" : `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`,
};

export default function PortfolioPage() {
  const router = useRouter();
  const quotes = useMarketStore((s) => s.quotes);
  const { data, mutate } = useSWR("portfolio", () => portfolioApi.get() as any);
  const [form, setForm] = useState({ nse_symbol: "", quantity: "", avg_cost: "", buy_date: "" });
  const [adding, setAdding] = useState(false);
  const [showAdd, setShowAdd] = useState(false);
  const [error, setError] = useState("");

  const portfolio = data?.portfolio;
  const holdings: any[] = data?.holdings || [];

  // Calculate P&L for each holding
  const enriched = holdings.map((h) => {
    const ltp = quotes[h.nse_symbol]?.ltp ?? null;
    const invested = h.quantity * h.avg_cost;
    const current = ltp ? h.quantity * ltp : null;
    const pnl = current != null ? current - invested : null;
    const pnlPct = pnl != null ? (pnl / invested) * 100 : null;
    return { ...h, ltp, invested, current, pnl, pnlPct };
  });

  const totalInvested = enriched.reduce((s, h) => s + h.invested, 0);
  const totalCurrent = enriched.filter(h => h.current != null).reduce((s, h) => s + (h.current || 0), 0);
  const totalPnl = totalCurrent - totalInvested;
  const totalPnlPct = totalInvested > 0 ? (totalPnl / totalInvested) * 100 : 0;

  const handleAdd = async () => {
    const sym = form.nse_symbol.trim().toUpperCase();
    const qty = parseFloat(form.quantity);
    const cost = parseFloat(form.avg_cost);
    if (!sym || isNaN(qty) || isNaN(cost) || qty <= 0 || cost <= 0) {
      setError("Please fill in all required fields with valid values.");
      return;
    }
    setAdding(true);
    setError("");
    try {
      await portfolioApi.addHolding({
        nse_symbol: sym,
        quantity: qty,
        avg_cost: cost,
        buy_date: form.buy_date || null,
      });
      setForm({ nse_symbol: "", quantity: "", avg_cost: "", buy_date: "" });
      setShowAdd(false);
      await mutate();
    } catch (e: any) {
      setError(e.message || "Failed to add holding");
    } finally {
      setAdding(false);
    }
  };

  return (
    <div className="min-h-screen" style={{ background: "var(--bg-base)" }}>
      <div className="topbar">
        <button className="btn btn-ghost" onClick={() => router.push("/")}>
          <ArrowLeft className="w-4 h-4" /> Dashboard
        </button>
        <TrendingUp className="w-5 h-5 text-green-400" />
        <span className="font-bold" style={{ color: "var(--text-primary)" }}>Portfolio</span>
        <div className="flex-1" />
        <button className="btn btn-primary text-xs" onClick={() => setShowAdd(!showAdd)}>
          <Plus className="w-3 h-3" /> Add Holding
        </button>
      </div>

      <div className="main-content max-w-5xl mx-auto">
        {/* Summary tiles */}
        <div className="grid grid-cols-3 gap-4 mb-6">
          <div className="metric-card">
            <div className="metric-label">Invested</div>
            <div className="metric-value">{fmt.cr(totalInvested)}</div>
          </div>
          <div className="metric-card">
            <div className="metric-label">Current Value</div>
            <div className="metric-value" style={{ color: "#2188ff" }}>
              {totalCurrent > 0 ? fmt.cr(totalCurrent) : "—"}
            </div>
            <div className="metric-sub">Live prices where available</div>
          </div>
          <div className="metric-card">
            <div className="metric-label">Total P&L</div>
            <div className="metric-value" style={{ color: totalPnl >= 0 ? "#3fb950" : "#f85149" }}>
              {totalCurrent > 0 ? `${totalPnl >= 0 ? "+" : ""}${fmt.cr(totalPnl)}` : "—"}
            </div>
            <div className="metric-sub" style={{ color: totalPnl >= 0 ? "#3fb950" : "#f85149" }}>
              {totalCurrent > 0 ? fmt.pct(totalPnlPct) : "Prices loading..."}
            </div>
          </div>
        </div>

        {/* Add holding form */}
        {showAdd && (
          <div className="card p-5 mb-6 animate-slide-up">
            <h3 className="text-sm font-semibold mb-4" style={{ color: "var(--text-primary)" }}>Add Holding</h3>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <div>
                <label className="metric-label">Symbol *</label>
                <input className="input mt-1" placeholder="TCS" value={form.nse_symbol}
                  onChange={(e) => setForm(f => ({ ...f, nse_symbol: e.target.value.toUpperCase().slice(0, 30) }))} />
              </div>
              <div>
                <label className="metric-label">Quantity *</label>
                <input className="input mt-1" type="number" placeholder="100" value={form.quantity}
                  onChange={(e) => setForm(f => ({ ...f, quantity: e.target.value }))} />
              </div>
              <div>
                <label className="metric-label">Avg Buy Price *</label>
                <input className="input mt-1" type="number" placeholder="3500.00" value={form.avg_cost}
                  onChange={(e) => setForm(f => ({ ...f, avg_cost: e.target.value }))} />
              </div>
              <div>
                <label className="metric-label">Buy Date</label>
                <input className="input mt-1" type="date" value={form.buy_date}
                  onChange={(e) => setForm(f => ({ ...f, buy_date: e.target.value }))} />
              </div>
            </div>
            {error && <div className="mt-2 text-sm text-red-400">{error}</div>}
            <div className="flex gap-2 mt-4">
              <button className="btn btn-primary" onClick={handleAdd} disabled={adding}>
                {adding ? "Adding..." : "Add Holding"}
              </button>
              <button className="btn btn-ghost" onClick={() => { setShowAdd(false); setError(""); }}>
                Cancel
              </button>
            </div>
          </div>
        )}

        {/* Holdings table */}
        <div className="card overflow-hidden">
          <div className="px-5 py-3 border-b" style={{ borderColor: "var(--bg-border)" }}>
            <span className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>
              {holdings.length} Holdings
            </span>
          </div>
          {holdings.length === 0 ? (
            <div className="p-12 text-center" style={{ color: "var(--text-muted)" }}>
              <BarChart2 className="w-10 h-10 mx-auto mb-3 opacity-30" />
              <p className="text-sm">No holdings yet.</p>
              <p className="text-xs mt-1">Click "Add Holding" to track your positions.</p>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Symbol</th>
                    <th>Qty</th>
                    <th>Avg Cost</th>
                    <th>CMP</th>
                    <th>Invested</th>
                    <th>Current</th>
                    <th>P&L</th>
                    <th>P&L %</th>
                    <th>Buy Date</th>
                  </tr>
                </thead>
                <tbody>
                  {enriched.map((h) => (
                    <tr key={h.id} onClick={() => router.push(`/stocks/${h.nse_symbol}`)}>
                      <td>
                        <span className="font-semibold" style={{ color: "#2188ff" }}>{h.nse_symbol}</span>
                      </td>
                      <td className="font-mono">{h.quantity}</td>
                      <td className="font-mono">{fmt.price(h.avg_cost)}</td>
                      <td className="font-mono" style={{ color: "var(--text-primary)" }}>
                        {fmt.price(h.ltp)}
                      </td>
                      <td className="font-mono" style={{ color: "var(--text-secondary)" }}>
                        {fmt.cr(h.invested)}
                      </td>
                      <td className="font-mono" style={{ color: "var(--text-secondary)" }}>
                        {h.current != null ? fmt.cr(h.current) : "—"}
                      </td>
                      <td className="font-mono font-semibold" style={{ color: h.pnl != null ? (h.pnl >= 0 ? "#3fb950" : "#f85149") : "var(--text-muted)" }}>
                        {h.pnl != null ? `${h.pnl >= 0 ? "+" : ""}${fmt.cr(h.pnl)}` : "—"}
                      </td>
                      <td className="font-mono font-semibold" style={{ color: h.pnlPct != null ? (h.pnlPct >= 0 ? "#3fb950" : "#f85149") : "var(--text-muted)" }}>
                        {h.pnlPct != null ? fmt.pct(h.pnlPct) : "—"}
                      </td>
                      <td style={{ fontSize: 11, color: "var(--text-muted)" }}>
                        {h.buy_date || "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        <div className="disclaimer-banner mt-6">
          ⚠️ P&L calculations are indicative only. Based on live market prices where available. Not investment advice.
        </div>
      </div>
    </div>
  );
}
