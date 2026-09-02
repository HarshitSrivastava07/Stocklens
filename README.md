# StockLens

Stock-analysis platform for Indian (NSE/BSE) and global equities — real-time price ingestion, fundamentals ETL, and a Postgres schema designed for ratio analysis, DCF valuation, ML risk/quality scoring, and RAG-grounded AI research summaries.

> **Project status: early / partially built.** The real-time ingestion worker, the batch ETL & seeding scripts, and the database schema are implemented and runnable. The API server, web frontend, valuation engine, ML pipeline, signal engine, and AI/RAG layer are designed in the schema but **not yet implemented**. See [Implementation status](#implementation-status) before you plan work against this repo.

---

## Table of contents

- [What it does](#what-it-does)
- [Architecture](#architecture)
- [Repository layout](#repository-layout)
- [Tech stack](#tech-stack)
- [Getting started](#getting-started)
- [Environment variables](#environment-variables)
- [Running the worker](#running-the-worker)
- [Data pipeline scripts](#data-pipeline-scripts)
- [Database schema](#database-schema)
- [Docker](#docker)
- [Implementation status](#implementation-status)
- [Known issues](#known-issues)
- [Roadmap](#roadmap)
- [Documentation](#documentation)
- [Disclaimer](#disclaimer)

---

## What it does

**Today, in this repository:**

- **Streams live NSE ticks** from the Upstox WebSocket feed, validates and normalizes them, writes the latest quote to Redis (with TTL), and fans out per-symbol and batched dashboard updates over Redis pub/sub.
- **Aggregates ticks into 1-minute OHLCV candles** in memory and flushes each closed candle to a monthly-partitioned Postgres table.
- **Polls Yahoo Finance** every 60s for global stocks (S&P 500, Nasdaq 100, FTSE 100, Nikkei 225, and more) and upserts the latest quotes.
- **Imports reference and fundamental data** from the NSE equity list, NSE daily bhavcopy, BSE quarterly-results XML, and Screener.in Excel exports.
- **Seeds a local dev database** with a realistic stock universe, sector taxonomy, and mock financials/ratios/scores so downstream work has something to read.

**Designed for, but not yet built:** financial-ratio computation, multi-scenario DCF and reverse-DCF valuation, ML-derived risk & quality scores, a rule-based signal engine (deliberately "consider / watch / avoid"-style rather than "Buy/Sell"), watchlist-portfolio-alerts, strategy backtesting, and Gemini-generated research reports grounded in pgvector retrieval.

---

## Architecture

```mermaid
flowchart TD
    subgraph External["External data sources"]
        Upstox["Upstox WebSocket v3<br/>(NSE real-time ticks)"]
        Yahoo["Yahoo Finance v8<br/>chart API"]
        NSEBhav["NSE bhavcopy<br/>(daily EOD CSV)"]
        BSE["BSE India XML API<br/>(quarterly results)"]
        Screener["Screener.in<br/>Excel exports"]
    end

    subgraph Worker["apps/worker — real-time ingestion (implemented)"]
        UW["upstox_ws.py<br/>UpstoxWorker"]
        TM["token_manager.py"]
        CB["candle_builder.py<br/>CandleBuilder"]
        YF["yahoo_price_fetcher.py<br/>YahooPriceFetcher"]
    end

    subgraph Scripts["scripts/ — manual ETL & seeding (implemented)"]
        S["import_nse_symbols · download_bhavcopy<br/>fetch_bse_data · parse_financials<br/>seed_sectors · seed_global_stocks · seed_*_dev"]
    end

    subgraph Data["Data layer"]
        Redis[("Redis<br/>latest:{symbol} · ticks:{symbol}<br/>ticks:dashboard")]
        PG[("PostgreSQL + pgvector<br/>supabase/migrations/")]
    end

    subgraph Planned["Planned — not in this repository"]
        API["API server<br/>(FastAPI)"]
        Engines["Valuation · ML scoring<br/>Signal · AI/RAG"]
        Web["apps/web<br/>Next.js frontend"]
    end

    Upstox --> UW
    TM --> UW
    UW --> Redis
    UW --> CB
    CB --> PG
    Yahoo --> YF
    YF --> Redis
    YF --> PG
    NSEBhav --> S
    BSE --> S
    Screener --> S
    S --> PG

    Redis -.-> API
    PG -.-> API
    API -.-> Engines
    API -.-> Web
    Redis -.WS.-> Web

    classDef planned stroke-dasharray: 5 5;
    class API,Engines,Web planned;
```

**Tick flow, end to end:**

```
Upstox WS message
  → validate (drop ticks with ltp <= 0 or no symbol)
  → normalize (strip NSE_EQ:/NSE_INDEX: prefix, compute change_abs / change_pct)
  → SETEX latest:{symbol} 30s          # low-latency read cache
  → PUBLISH ticks:{symbol}             # per-symbol subscribers
  → CandleBuilder.on_tick()            # in-memory 1m OHLCV, flushed on minute rollover
  → PUBLISH ticks:dashboard            # batched every >= 0.5s
```

Redis is a cache and fan-out bus only — every key carries a TTL (30s for Upstox ticks, 120s for Yahoo). Postgres is the system of record.

---

## Repository layout

```
apps/
  worker/                  Real-time ingestion service (Python asyncio)
    upstox_ws.py           Entry point: Upstox WS stream + Yahoo poller via asyncio.gather
    token_manager.py       Upstox token resolution: file cache → env → OAuth refresh
    candle_builder.py      Tick → 1-minute OHLCV aggregation, flush to price_candles_1m
    yahoo_price_fetcher.py Yahoo Finance poller (also runs standalone)
    test_ws.py             Manual debug script for the Upstox authorize/connect flow
    Dockerfile             python:3.12-slim image for the worker
  web/
    store/market.ts        Zustand store for quotes / market status / WS state
scripts/                   Standalone, manually-invoked ETL & seeding CLIs
docs/
  ARCHITECTURE.md          System design, components, data flow, weaknesses
  EXECUTION.md             Install, config, commands, startup & workflow traces
  FILE_RESPONSIBILITY.md   Per-file responsibility reference
supabase/migrations/       Hand-written SQL schema (applied with psql)
scratch/                   Throwaway API experiments (Gemini, Yahoo) — not wired up
```

---

## Tech stack

| Layer | Technology |
|---|---|
| Ingestion worker | Python 3.12, `asyncio` |
| Market data | `upstox-python-sdk` 2.7.0 (NSE), Yahoo Finance v8 chart API (global) |
| Cache / pub-sub | Redis 5.x via `redis.asyncio` |
| Database | PostgreSQL with `pgvector`, `pg_trgm`, `btree_gin`, `uuid-ossp` |
| DB access | `asyncpg` — raw SQL, no ORM |
| HTTP | `httpx` |
| Config | `python-dotenv` reading a repo-root `.env` |
| Container | Docker (worker only) |
| Frontend (planned) | Next.js, React, TypeScript, Tailwind, Zustand, SWR, Recharts |
| AI (planned) | Google Gemini + pgvector RAG |

---

## Getting started

### Prerequisites

- **Python 3.12**
- **PostgreSQL** with the `vector` (pgvector), `pg_trgm`, `btree_gin`, and `uuid-ossp` extensions available
- **Redis**
- **Upstox developer account** + API credentials (only needed for the live NSE feed)
- On Linux, `gcc`, `g++`, and `libpq-dev` for building `asyncpg` if no wheel is available

### 1. Install Python dependencies

```bash
pip install -r apps/worker/requirements.txt
```

The `scripts/` share these dependencies, plus **`pandas` and `openpyxl`** for `parse_financials.py` — install those separately:

```bash
pip install pandas openpyxl
```

### 2. Apply the database schema

There is no migration runner; apply the SQL by hand, in this order:

```bash
psql $DATABASE_URL -f supabase/migrations/001_initial.sql
psql $DATABASE_URL -f supabase/migrations/002_global_stocks.sql
psql $DATABASE_URL -f supabase/migrations/002_pgvector_fo_peers.sql
```

### 3. Create a `.env` at the repository root

The worker loads `<repo root>/.env` explicitly; the scripts search upward from the working directory, so run them from the repo root. See [Environment variables](#environment-variables).

### 4. Populate the stock universe

```bash
python scripts/import_nse_symbols.py       # NSE equity list → stocks
python scripts/seed_sectors.py             # sector taxonomy + stocks.sector_id
python scripts/seed_global_stocks.py       # ~1,750 global stocks with yahoo_ticker
python scripts/seed_all_stocks_dev.py      # optional: mock financials/ratios/scores
```

### 5. Run the worker

```bash
python apps/worker/upstox_ws.py
```

---

## Environment variables

| Variable | Purpose | Required | Default |
|---|---|---|---|
| `REDIS_URL` | Redis connection string | **Yes** (worker) | none — worker fails to start |
| `DATABASE_URL` | Async Postgres URL for the candle builder (a `+asyncpg` suffix is stripped) | **Yes** (worker) | none — candle flush fails |
| `DATABASE_SYNC_URL` | Postgres URL for all `scripts/` and the standalone Yahoo fetcher | No | `postgresql://postgres:password@localhost:5432/stocklens` |
| `UPSTOX_ACCESS_TOKEN` | Manually-obtained Upstox token (fallback) | Conditional | `""` |
| `UPSTOX_REFRESH_TOKEN` | Refresh token used to mint a new access token | Conditional | `""` |
| `UPSTOX_API_KEY` | Upstox client ID — required only for token refresh | Conditional | none |
| `UPSTOX_API_SECRET` | Upstox client secret — required only for token refresh | Conditional | none |
| `UPSTOX_REDIRECT_URI` | Registered OAuth redirect URI — refresh only | Conditional | none |
| `UPSTOX_ANALYTICS_TOKEN` | Long-lived Upstox analytics token (~1 year validity) | No | none |
| `GEMINI_API_KEY` | Gemini API key (referenced by `scratch/` only) | No | none |
| `GEMINI_MODEL_FAST` | Preferred fast Gemini model (`scratch/` only) | No | none |

No `.env.example` ships with the repo. Upstox's standard access token expires daily (3:30 AM IST); `token_manager.py` resolves one from `apps/worker/upstox_token.json` → `UPSTOX_ACCESS_TOKEN` → OAuth refresh → `UPSTOX_ANALYTICS_TOKEN`, in that order. The analytics token is Upstox's separate long-lived (~1 year) token — it's kept as a last-resort fallback since it's not guaranteed to authenticate the live WebSocket feed the same way the daily token does.

---

## Running the worker

```bash
# Upstox NSE stream + Yahoo global poller, concurrently
python apps/worker/upstox_ws.py

# Yahoo global poller only (no Upstox credentials needed)
python apps/worker/yahoo_price_fetcher.py
```

**Symbol subscription.** `upstox_ws.py` reads `apps/worker/subscribed_symbols.json` if present; otherwise it falls back to a built-in five-symbol list (TCS, Infosys, HDFC Bank, SBI, Reliance).

**Reconnection.** The Upstox stream retries with exponential backoff (1s → 60s cap), resetting after a clean disconnect. A persistently missing token means an indefinite retry loop rather than a fast failure — check the logs if nothing is streaming.

**Windows.** `SIGTERM`/`SIGINT` handlers cannot be registered on Windows, so the graceful-shutdown path is skipped there and Ctrl+C falls back to default asyncio interrupt handling.

---

## Data pipeline scripts

Each script is an independent CLI with its own DB connection — nothing schedules them, so run them manually (or wire up your own cron).

| Command | What it does |
|---|---|
| `python scripts/import_nse_symbols.py [--file PATH] [--source nse\|bhavcopy\|file]` | Import the NSE equity list into `stocks` |
| `python scripts/download_bhavcopy.py [--date YYYY-MM-DD] [--backfill N]` | Download NSE daily bhavcopy → staging, daily candles, latest quotes |
| `python scripts/fetch_bse_data.py [--symbol SYM] [--limit N]` | Fetch BSE quarterly-results XML → `financial_results` |
| `python scripts/parse_financials.py --file FILE.xlsx (--symbol SYM \| --auto)` | Parse a Screener.in Excel export → `financial_results` |
| `python scripts/seed_sectors.py` | Seed `sector_classification`, tag `stocks.sector_id` |
| `python scripts/seed_global_stocks.py [--exchange NYSE] [--force]` | Seed global-index stocks with Yahoo tickers |
| `python scripts/seed_dev_data.py` | Seed realistic mock dev data (only if the DB is empty) |
| `python scripts/seed_all_stocks_dev.py` | Mock financials/ratios/valuations/scores/history for every active stock |

Bhavcopy is intended for ~16:30 IST and BSE results for ~17:00 IST, per comments in the scripts.

---

## Database schema

Roughly 45 tables across three migration files. Highlights:

- **Reference:** `stocks`, `sector_classification`, `market_status`, `data_sources`
- **Prices:** `realtime_quotes`, `price_candles_1m` (partitioned monthly, **pre-created through 2026-12**), `price_candles_daily`, `nse_bhavcopy_staging`
- **Fundamentals:** `financial_results`, `financial_ratios`, `shareholding_patterns`, `corporate_actions`
- **Valuation:** `valuation_assumptions`, `valuation_runs`, `dcf_outputs`, `reverse_dcf_outputs`, `intrinsic_values`
- **Intelligence:** `cluster_results`, `ml_scores`, `risk_flags`, `signals`, `peer_groups`, `fo_oi_history`
- **User:** `watchlist`, `alerts`, `alert_history`, `portfolio`, `portfolio_holdings`, `backtest_runs`, `backtest_results`
- **AI/RAG:** `ai_reports` (with a default "not investment advice" disclaimer column), `news_embeddings` (768-dim, IVFFlat), `stock_embeddings` (768-dim, HNSW)
- **Ops:** `data_import_logs`, `admin_overrides`, `audit_logs`, `system_jobs`

All access is parameterized raw SQL via `asyncpg` with `ON CONFLICT` upserts — no ORM.

---

## Docker

Only the worker is containerized:

```bash
docker build -t stocklens-worker apps/worker
docker run --env-file .env stocklens-worker
```

The image builds on `python:3.12-slim`, installs build tooling for native extensions, and runs as an unprivileged `workeruser`. There is no `docker-compose.yml` — Postgres and Redis must be reachable at the configured URLs. Note that `python-dotenv`'s repo-root-relative `.env` lookup does not resolve inside the container's `/app` working directory, so pass configuration via `--env-file`/`-e` rather than relying on a copied `.env`.

---

## Implementation status

| Area | Status |
|---|---|
| Database schema (~45 tables, pgvector) | ✅ Implemented |
| Upstox NSE real-time worker | ✅ Implemented |
| 1-minute candle aggregation | ✅ Implemented |
| Yahoo Finance global poller | ✅ Implemented |
| NSE symbols / bhavcopy / BSE / Screener ETL | ✅ Implemented |
| Dev seeding scripts | ✅ Implemented |
| Worker Dockerfile | ✅ Implemented |
| API server (FastAPI is in `requirements.txt`, no app exists) | ❌ Not started |
| Web frontend (`apps/web` has one Zustand store, no `package.json`) | ❌ Not started |
| Ratio / DCF / reverse-DCF valuation engines | ❌ Not started |
| ML scoring, clustering, signal engine | ❌ Not started |
| AI reports + RAG retrieval (only disconnected `scratch/` experiments) | ❌ Not started |
| Scheduler (`scheduler_service.py` is referenced in comments, absent) | ❌ Not started |
| Automated tests, CI/CD, deployment manifests | ❌ None |

Tables outside the ingestion path are currently filled only by the dev seed scripts with **randomized mock values** — treat any ratio, valuation, score, or signal in a dev database as fabricated.

---

## Known issues

- **No `.gitignore`.** `node_modules/`, `__pycache__/`, and (critically) `apps/worker/upstox_token.json` are not protected from being committed. Add one before pushing anything with a live token.
- **Duplicate migration prefix.** Both `002_global_stocks.sql` and `002_pgvector_fo_peers.sql` are numbered `002`. Alphabetical order happens to be safe, but the numbering should be fixed.
- **Table-name mismatch.** `scripts/seed_all_stocks_dev.py` inserts into `ml_cluster_results`; the schema defines `cluster_results`. That insert fails against the current schema.
- **Candle partitions end at 2026-12.** `price_candles_1m` has no automatic partition creation — new partitions must be added before January 2027.
- **`apps/worker/test_ws.py` hardcodes an absolute developer-machine path** in its `load_dotenv(...)` call and will not run elsewhere without editing.
- **`scratch/*.py` import `from config import settings`** out of an `apps/api` package that does not exist, so they raise `ModuleNotFoundError` as-is.
- **`structlog` is a declared dependency but unused** — all logging goes through the stdlib `logging` module.
- **No shared library between `apps/worker` and `scripts/`** — env loading, DB URL fallback, and logging setup are copy-pasted in every file.

---

## Roadmap

1. Add a `.gitignore` and a `.env.example`; fix the migration numbering and the `ml_cluster_results` mismatch.
2. Extract a shared `common/` package for config, DB pooling, and logging.
3. Build the FastAPI service: quotes, stock detail, screener, and the Upstox OAuth callback the worker's logs already point at (`/api/v1/auth/upstox`).
4. Implement the computation layer — ratios, then DCF/reverse-DCF, then ML scores and the signal engine — replacing the mock seed values table by table.
5. Scaffold `apps/web` properly (`package.json`, Next.js app router) around the existing `store/market.ts` and a Redis-backed WebSocket bridge.
6. Add the AI/RAG layer: embedding generation into `stock_embeddings`, retrieval, and disclaimed Gemini report generation.
7. Add a scheduler for bhavcopy/BSE ingestion, plus a test suite and CI.

---

## Documentation

Deep-dive docs live in [docs/](docs/):

- [ARCHITECTURE.md](docs/ARCHITECTURE.md) — components, layers, data/concurrency/error-handling design, and an evidence-based status audit
- [EXECUTION.md](docs/EXECUTION.md) — install, configuration, every runnable command, startup traces, and a debugging guide
- [FILE_RESPONSIBILITY.md](docs/FILE_RESPONSIBILITY.md) — what every file does, dependency graph, and "where do I change this?"

---

## Disclaimer

StockLens is a research and educational project. Nothing it produces — prices, ratios, valuations, scores, signals, or AI-generated summaries — is investment advice. Data is sourced from third-party providers (Upstox, Yahoo Finance, NSE, BSE, Screener.in) and may be delayed, incomplete, or wrong. Verify independently before making any financial decision.
