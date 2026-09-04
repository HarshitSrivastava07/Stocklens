# StockLens — Complete Setup Guide

> Personal NSE Stock Intelligence Platform — Local Only

---

## Prerequisites

1. **Python 3.12+** — [python.org](https://python.org)
2. **Node.js 20+** — [nodejs.org](https://nodejs.org)
3. **Docker Desktop** (for Postgres + Redis) — [docker.com](https://docker.com)
4. **Upstox Developer Account** — [upstox.com/developer](https://upstox.com/developer) (free)
5. **Google AI Studio Key** — [aistudio.google.com](https://aistudio.google.com) (free tier: 1500 req/day)

---

## Step 1 — Clone & Configure Environment

```powershell
# Copy .env template
copy .env.example .env
```

Open `.env` and fill in these **required** values:

```env
# Upstox (get from upstox.com/developer → create app)
UPSTOX_API_KEY=your_api_key_here
UPSTOX_API_SECRET=your_api_secret_here
UPSTOX_ACCESS_TOKEN=your_daily_access_token_here

# Google Gemini (from aistudio.google.com → Get API key)
GEMINI_API_KEY=your_gemini_key_here

# Database (defaults work with Docker below)
DATABASE_URL=postgresql+asyncpg://postgres:password@localhost:5432/stocklens
DATABASE_SYNC_URL=postgresql://postgres:password@localhost:5432/stocklens
REDIS_URL=redis://localhost:6379/0
```

---

## Step 2 — Start Database & Redis (Docker)

```powershell
# Start PostgreSQL
docker run -d --name stocklens-db `
  -p 5432:5432 `
  -e POSTGRES_DB=stocklens `
  -e POSTGRES_PASSWORD=password `
  postgres:16-alpine

# Start Redis
docker run -d --name stocklens-redis `
  -p 6379:6379 `
  redis:7-alpine
```

---

## Step 3 — Install & Start the Backend API

```powershell
cd apps\api
pip install -r requirements.txt

# This creates all DB tables automatically on startup
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

✅ API running at: http://localhost:8000  
✅ Swagger docs: http://localhost:8000/docs

---

## Step 4 — Import NSE Stock Universe

```powershell
# From the root directory
cd scripts
pip install asyncpg httpx python-dotenv
python import_nse_symbols.py
```

This downloads ~5000 NSE stocks from the official NSE archive and seeds your database.

---

## Step 5 — Install & Start the Frontend

```powershell
cd apps\web
npm install
npm run dev
```

✅ Dashboard: http://localhost:3000

---

## Step 6 — Run the Data Pipeline (First Time)

Visit http://localhost:3000/admin and trigger these jobs **in order**:

1. **Ratio Engine** — computes PE, PB, ROE, CAGR etc. (needs financial data)
2. **Valuation Engine** — runs DCF/PB-ROE/EV-EBITDA models
3. **ML Pipeline** — clustering + risk/confidence scores  
   *(Or run directly: `cd apps/ml && python pipeline.py`)*
4. **Signal Engine** — assigns Green/Yellow/Red/Grey signals

> **Note:** The ratio and valuation engines need financial data. You can:
> - Upload a CSV from Screener.in (screener.in → export → upload via admin)
> - Wait for BSE XML auto-import (scheduled nightly)

---

## Step 7 — Live Prices (Upstox WebSocket)

### Getting your daily Upstox access token:

1. Visit `http://localhost:8000/api/v1/auth/upstox` (or log in to Upstox Developer portal)
2. Complete OAuth flow
3. Copy the access token to your `.env`:
   ```env
   UPSTOX_ACCESS_TOKEN=your_token_here
   ```

### Start the WebSocket worker:

```powershell
cd apps\worker
pip install -r requirements.txt
python upstox_ws.py
```

Live prices will appear on the dashboard within seconds.

---

## Data Flow Architecture

```
Upstox WebSocket → Worker → Redis (latest:{symbol}, ticks:{symbol})
                          → PostgreSQL (price_candles_1m)
                          → Pub/Sub → API WebSocket → Browser

NSE Bhavcopy/BSE XML → Scripts → PostgreSQL (financial_results)
                               → Ratio Engine → financial_ratios
                               → Valuation Engine → intrinsic_values
                               → Signal Engine → signals
                               → ML Pipeline → ml_scores, ml_cluster_results

Gemini AI → ai_reports (cached 4h in Redis + DB)
```

---

## Scheduler (Automatic Daily Jobs)

The FastAPI app runs APScheduler automatically. IST times:

| Time | Job |
|------|-----|
| 09:00 | Pre-market check |
| 15:35 | EOD snapshot (closing prices) |
| 16:00 | Corporate actions |
| 17:00 | Fundamentals refresh (BSE XML) |
| 18:00 | Ratio engine |
| 19:00 | Valuation engine |
| 20:00 | Signal engine |
| 20:30 | Alert evaluation |
| 21:00 | AI summaries (stale only) |
| Sunday 23:00 | ML clustering |
| Sunday 02:00 | ML retrain |

---

## Common Issues

### "No quote data" on dashboard
→ Start the Upstox worker and ensure your access token is valid

### "Valuation not computed"
→ Need financial data first. Upload from Screener.in or trigger fundamentals pipeline

### "Redis connection failed"  
→ Check: `docker ps | grep stocklens-redis`

### Upstox token expires every day
→ Token resets at 06:30 AM IST. Refresh via the auth flow or set a new token in `.env`

---

## ML Features Explained

| Score | Range | Meaning |
|-------|-------|---------|
| **Fundamental Score** | 0–100 | Composite of ROE, ROCE, growth, B/S health |
| **Risk Score** | 0–100 | 0=safe, 100=very risky (debt, ICR, CFO quality) |
| **Growth Score** | 0–100 | Based on 5Y CAGR revenue/PAT/EPS and margin expansion |
| **Confidence Score** | 0–100 | Data completeness + stability of inputs |

### Signal Logic (Green gate):
- Upside ≥ 25%
- Margin of Safety ≥ 15%
- Risk Score ≤ 45
- Fundamental Score ≥ 60
- Valuation Confidence = HIGH or MEDIUM
- No CRITICAL/HIGH risk flags
- Growth Score ≥ 50

---

## Disclaimer

> This platform is a **personal research tool**. All calculations, AI summaries, and signals are based on model assumptions and historical data. Nothing on this platform constitutes investment advice. Verify all data independently before making any financial decisions.
