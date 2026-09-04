/**
 * API client for StockLens backend
 * All calls go through backend — no AI keys in frontend
 */
const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function fetchApi<T>(path: string, options?: RequestInit): Promise<T> {
  // BUG20 FIX: Only set Content-Type: application/json when NOT sending FormData.
  // Setting it for multipart/form-data removes the browser-generated boundary and breaks uploads.
  const isFormData = options?.body instanceof FormData;
  const headers = isFormData
    ? { ...options?.headers }
    : { "Content-Type": "application/json", ...options?.headers };

  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers,
  });

  if (!res.ok) {
    let msg = `HTTP ${res.status}`;
    try {
      const err = await res.json();
      msg = err.message || err.detail || msg;
    } catch {}
    throw new ApiError(res.status, msg);
  }

  return res.json();
}

// ── Market Data ──────────────────────────────────────────────
export const marketApi = {
  getStatus: () => fetchApi("/api/v1/market/status"),
  getQuote: (symbol: string) => fetchApi(`/api/v1/market/quote/${symbol}`),
  getCandles: (symbol: string, interval = "1D", limit = 200) =>
    fetchApi(`/api/v1/market/candles/${symbol}?interval=${interval}&limit=${limit}`),
  getIndices: () => fetchApi("/api/v1/market/indices"),
};

// ── Stocks ───────────────────────────────────────────────────
export const stocksApi = {
  list: (params?: Record<string, string | number>) => {
    const qs = params ? "?" + new URLSearchParams(params as any).toString() : "";
    return fetchApi(`/api/v1/stocks${qs}`);
  },
  getDetail: (symbol: string) => fetchApi(`/api/v1/stocks/${symbol}`),
  getFinancials: (symbol: string) => fetchApi(`/api/v1/stocks/${symbol}/financials`),
  getRatios: (symbol: string) => fetchApi(`/api/v1/stocks/${symbol}/ratios`),
  getValuation: (symbol: string) => fetchApi(`/api/v1/stocks/${symbol}/valuation`),
  getReverseDcf: (symbol: string) => fetchApi(`/api/v1/stocks/${symbol}/reverse-dcf`),
  getPeers: (symbol: string) => fetchApi(`/api/v1/stocks/${symbol}/peers`),
  getShareholding: (symbol: string) => fetchApi(`/api/v1/stocks/${symbol}/shareholding`),
  getSignal: (symbol: string) => fetchApi(`/api/v1/stocks/${symbol}/signals`),
  getRiskFlags: (symbol: string) => fetchApi(`/api/v1/stocks/${symbol}/risk-flags`),
};

// ── ML ───────────────────────────────────────────────────────
export const mlApi = {
  getClusters: () => fetchApi("/api/v1/ml/clusters"),
  getCluster: (label: string) => fetchApi(`/api/v1/ml/clusters/${label}`),
  getScores: (symbol: string) => fetchApi(`/api/v1/ml/scores/${symbol}`),
  runInference: () => fetchApi("/api/v1/ml/run-inference", { method: "POST" }),
};

// ── AI ───────────────────────────────────────────────────────
export const aiApi = {
  getSummary: (symbol: string) => fetchApi(`/api/v1/ai/summary/${symbol}`),
  generateSummary: (symbol: string) =>
    fetchApi(`/api/v1/ai/generate/${symbol}`, { method: "POST" }),
  getSectorSummary: (sector: string) => fetchApi(`/api/v1/ai/sector-summary/${sector}`),
  explainDcf: (symbol: string) =>
    fetchApi(`/api/v1/ai/dcf-explain/${symbol}`, { method: "POST" }),
};

// ── Screener ─────────────────────────────────────────────────
export const screenerApi = {
  getPrebuilt: () => fetchApi("/api/v1/screener/prebuilt"),
  runPrebuilt: (id: string) => fetchApi(`/api/v1/screener/prebuilt/${id}`),
  runCustom: (conditions: object) =>
    fetchApi("/api/v1/screener/custom", {
      method: "POST",
      body: JSON.stringify(conditions),
    }),
};

// ── Watchlist ────────────────────────────────────────────────
export const watchlistApi = {
  get: () => fetchApi("/api/v1/watchlist"),
  add: (symbol: string, notes?: string) =>
    fetchApi("/api/v1/watchlist/items", {
      method: "POST",
      body: JSON.stringify({ nse_symbol: symbol, notes }),
    }),
  remove: (id: string) =>
    fetchApi(`/api/v1/watchlist/items/${id}`, { method: "DELETE" }),
};

// ── Portfolio ────────────────────────────────────────────────
export const portfolioApi = {
  get: () => fetchApi("/api/v1/portfolio"),
  addHolding: (holding: object) =>
    fetchApi("/api/v1/portfolio/holdings", {
      method: "POST",
      body: JSON.stringify(holding),
    }),
  updateHolding: (id: string, data: object) =>
    fetchApi(`/api/v1/portfolio/holdings/${id}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    }),
  deleteHolding: (id: string) =>
    fetchApi(`/api/v1/portfolio/holdings/${id}`, { method: "DELETE" }),
  getAnalytics: () => fetchApi("/api/v1/portfolio/analytics"),
};

// ── Sectors ──────────────────────────────────────────────────
export const sectorsApi = {
  list: () => fetchApi("/api/v1/sectors"),
  getDetail: (id: string) => fetchApi(`/api/v1/sectors/${id}`),
  getStocks: (id: string) => fetchApi(`/api/v1/sectors/${id}/stocks`),
};

// ── Admin ────────────────────────────────────────────────────
export const adminApi = {
  getJobs: () => fetchApi("/api/v1/admin/jobs"),
  triggerJob: (job: string) =>
    fetchApi(`/api/v1/admin/jobs/trigger/${job}`, { method: "POST" }),
  getAuditLogs: () => fetchApi("/api/v1/admin/audit-logs"),
  // BUG20 FIX: FormData body — fetchApi auto-detects and skips Content-Type header
  importStocks: (formData: FormData) =>
    fetchApi("/api/v1/admin/stocks/import", {
      method: "POST",
      body: formData,
    }),
  uploadFinancials: (formData: FormData) =>
    fetchApi("/api/v1/admin/financials/upload", {
      method: "POST",
      body: formData,
    }),
};

export { ApiError };
