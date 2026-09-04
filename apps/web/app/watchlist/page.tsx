"use client";
import useSWR, { mutate as globalMutate } from "swr";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Star, Trash2, ArrowLeft, Plus, TrendingUp, TrendingDown } from "lucide-react";
import { watchlistApi, stocksApi } from "@/lib/api";

export default function WatchlistPage() {
  const router = useRouter();
  const { data, mutate } = useSWR("watchlist", () => watchlistApi.get() as any);
  const [addSymbol, setAddSymbol] = useState("");
  const [addNotes, setAddNotes] = useState("");
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState("");

  const items = data?.watchlist || [];

  const handleAdd = async () => {
    const sym = addSymbol.trim().toUpperCase();
    if (!sym) return;
    setAdding(true);
    setError("");
    try {
      await watchlistApi.add(sym, addNotes.trim());
      setAddSymbol("");
      setAddNotes("");
      await mutate();
    } catch (e: any) {
      setError(e.message || "Failed to add symbol");
    } finally {
      setAdding(false);
    }
  };

  const handleRemove = async (id: string) => {
    try {
      await watchlistApi.remove(id);
      await mutate();
    } catch {}
  };

  return (
    <div className="min-h-screen" style={{ background: "var(--bg-base)" }}>
      <div className="topbar">
        <button className="btn btn-ghost" onClick={() => router.push("/")}>
          <ArrowLeft className="w-4 h-4" /> Dashboard
        </button>
        <Star className="w-5 h-5 text-yellow-400" />
        <span className="font-bold" style={{ color: "var(--text-primary)" }}>Watchlist</span>
      </div>

      <div className="main-content max-w-4xl mx-auto">
        {/* Add stock */}
        <div className="card p-5 mb-6">
          <h2 className="text-sm font-semibold mb-3" style={{ color: "var(--text-primary)" }}>Add Stock to Watchlist</h2>
          <div className="flex gap-3 flex-wrap">
            <input
              className="input"
              style={{ maxWidth: 160 }}
              placeholder="NSE Symbol (e.g. TCS)"
              value={addSymbol}
              onChange={(e) => setAddSymbol(e.target.value.toUpperCase().slice(0, 30))}
              onKeyDown={(e) => e.key === "Enter" && handleAdd()}
            />
            <input
              className="input flex-1"
              placeholder="Notes (optional)"
              value={addNotes}
              onChange={(e) => setAddNotes(e.target.value.slice(0, 500))}
              onKeyDown={(e) => e.key === "Enter" && handleAdd()}
            />
            <button className="btn btn-primary" onClick={handleAdd} disabled={adding || !addSymbol.trim()}>
              <Plus className="w-4 h-4" /> {adding ? "Adding..." : "Add"}
            </button>
          </div>
          {error && <div className="mt-2 text-sm text-red-400">{error}</div>}
        </div>

        {/* Watchlist table */}
        <div className="card overflow-hidden">
          <div className="px-5 py-3 border-b" style={{ borderColor: "var(--bg-border)" }}>
            <span className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>
              {items.length} stocks tracked
            </span>
          </div>
          {items.length === 0 ? (
            <div className="p-12 text-center" style={{ color: "var(--text-muted)" }}>
              <Star className="w-10 h-10 mx-auto mb-3 opacity-30" />
              <p className="text-sm">No stocks in your watchlist yet.</p>
              <p className="text-xs mt-1">Add symbols above to start tracking.</p>
            </div>
          ) : (
            <table className="data-table">
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th>Notes</th>
                  <th>Added</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody>
                {items.map((item: any) => (
                  <tr key={item.id}>
                    <td>
                      <button
                        className="font-semibold text-sm hover:underline"
                        style={{ color: "#2188ff" }}
                        onClick={() => router.push(`/stocks/${item.nse_symbol}`)}
                      >
                        {item.nse_symbol}
                      </button>
                    </td>
                    <td style={{ fontSize: 12, color: "var(--text-muted)", maxWidth: 300 }}>
                      {item.notes || "—"}
                    </td>
                    <td style={{ fontSize: 11, color: "var(--text-muted)" }}>
                      {item.added_at ? new Date(item.added_at).toLocaleDateString("en-IN") : "—"}
                    </td>
                    <td>
                      <button
                        className="btn btn-ghost text-xs"
                        style={{ color: "var(--signal-red)" }}
                        onClick={() => handleRemove(item.id)}
                      >
                        <Trash2 className="w-3 h-3" />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        <div className="disclaimer-banner mt-6">
          ⚠️ Watchlist is for personal tracking only. Not investment advice.
        </div>
      </div>
    </div>
  );
}
