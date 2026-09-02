# StockLens — Architecture

> Status legend used throughout this document: **VERIFIED FROM CODE** (read directly in the repository), **INFERRED** (reasonable conclusion from evidence but not explicit), **UNKNOWN / NOT VERIFIED** (could not be determined from the repository).

## 1. Project Overview

StockLens is an Indian (NSE/BSE) and global stock-analysis platform. **VERIFIED FROM CODE**: the database schema (`supabase/migrations/001_initial.sql`) defines tables for a stock universe, real-time/historical prices, company fundamentals, computed financial ratios, a multi-scenario DCF valuation engine, ML-derived risk/quality scores, a rule-based "signal" (buy/watch/avoid-style, but explicitly not labeled "Buy/Sell") engine, a personal watchlist/portfolio/alerts module, an AI-report + RAG (pgvector) subsystem, and a backtesting module.

Of that ambitious schema, **only two parts have working application code in this repository**:

1. A real-time market-data ingestion worker (`apps/worker/`) — a Python asyncio service that streams NSE ticks from Upstox and polls Yahoo Finance for global stocks, and writes both to Redis and PostgreSQL. **VERIFIED FROM CODE**.
2. A set of standalone, manually-run data-import/seeding scripts (`scripts/`) that populate the stock universe, sector taxonomy, fundamentals, and dev/mock data. **VERIFIED FROM CODE**.

Everything else implied by the schema and by comments in the code — an API server, an admin panel, a valuation engine, an ML scoring pipeline, a signal engine, AI report generation, a scheduler service, and a Next.js frontend — is either **absent from the repository**, or present only as an empty scaffold. See §20 (Current Architecture Status) for the evidence behind each claim.

## 2. Goals and Responsibilities

**INFERRED** from the schema and worker code (no design doc exists in the repo):
- Maintain a canonical universe of NSE-listed (and, per migration `002_global_stocks.sql`, global) stocks.
- Ingest real-time prices for NSE stocks via Upstox WebSocket and for global stocks via Yahoo Finance polling.
- Aggregate ticks into 1-minute and daily OHLCV candles.
- Ingest fundamentals (BSE XML feeds, NSE bhavcopy, Screener.in exports).
- Compute financial ratios, DCF/relative valuations, ML risk/quality scores, and a plain-language "signal" per stock.
- Surface AI-generated, RAG-grounded research summaries with an explicit "not investment advice" disclaimer (`ai_reports.disclaimer` default in the schema).
- Support personal watchlist/portfolio/alerts and strategy backtesting.

## 3. Technology Stack

| Layer | Technology | Evidence |
|---|---|---|
| Real-time worker | Python 3.12, `asyncio` | `apps/worker/Dockerfile` (`FROM python:3.12-slim`), all `apps/worker/*.py` |
| Market data feed | `upstox-python-sdk==2.7.0` (NSE), `yfinance==0.2.50` + direct Yahoo v8 chart API calls (global) | `apps/worker/requirements.txt`, `upstox_ws.py`, `yahoo_price_fetcher.py` |
| Cache / pub-sub | Redis (`redis==5.2.1`, `hiredis`) via `redis.asyncio` | `apps/worker/requirements.txt`, `upstox_ws.py`, `yahoo_price_fetcher.py` |
| Database driver | `asyncpg==0.30.0` (worker + all scripts) | `candle_builder.py`, `scripts/*.py` |
| Database | PostgreSQL with `pgvector`, `pg_trgm`, `btree_gin`, `uuid-ossp` extensions | `supabase/migrations/001_initial.sql` lines 8-11 |
| HTTP client | `httpx==0.28.1` | worker + scripts |
| Config loading | `python-dotenv==1.0.1`, loads a root-level `.env` | `upstox_ws.py`, `yahoo_price_fetcher.py`, `scripts/*.py` |
| Logging | `structlog==24.4.0` declared as a dependency, but actual code uses stdlib `logging.basicConfig` everywhere observed | `requirements.txt` vs. every worker/script file |
| Planned web API framework | `fastapi==0.115.5`, `uvicorn[standard]==0.32.1` declared in `apps/worker/requirements.txt`, but **no FastAPI app exists in the repository** (see §20) | requirements.txt vs. absence of `apps/api/` |
| Frontend (planned) | Next.js, React, TypeScript, Tailwind CSS, Zustand, SWR, Recharts, Zod, lucide-react — inferred only from installed `apps/web/node_modules` packages; **no `package.json`, no application source beyond one store file** | `apps/web/node_modules/*`, `apps/web/store/market.ts` |
| AI / LLM (planned) | Google Gemini (`google.genai`), referenced by an `apps/api/config.py` that does not exist in the repo | `scratch/test_gemini.py`, `scratch/test_all_models.py`, `scratch/list_models.py` |
| Containerization | Docker (worker only) | `apps/worker/Dockerfile` |
| Migrations | Hand-written SQL files, no migration tool/framework detected (no Alembic, no Supabase CLI config file) | `supabase/migrations/*.sql` |

**UNKNOWN / NOT VERIFIED**: exact frontend dependency versions (no `package.json`/lock file present to read), whether a hosted Supabase project is actually used vs. a plain self-hosted Postgres (the `supabase/` directory contains only raw SQL migration files, no `supabase/config.toml` or CLI project files).

## 4. System Architecture

```mermaid
flowchart TD
    subgraph External["External Data Sources"]
        Upstox["Upstox WebSocket API\n(NSE real-time ticks)"]
        Yahoo["Yahoo Finance\nv8 chart API"]
        NSEBhav["NSE Bhavcopy\n(daily EOD CSV)"]
        BSE["BSE India XML API\n(quarterly results)"]
        Screener["Screener.in\nExcel exports (manual)"]
    end

    subgraph Worker["apps/worker — real-time ingestion service (implemented)"]
        UW["upstox_ws.py\nUpstoxWorker"]
        TM["token_manager.py"]
        CB["candle_builder.py\nCandleBuilder"]
        YF["yahoo_price_fetcher.py\nYahooPriceFetcher"]
    end

    subgraph Scripts["scripts/ — manual ETL & seeding CLIs (implemented, not scheduled)"]
        S1["import_nse_symbols.py"]
        S2["download_bhavcopy.py"]
        S3["fetch_bse_data.py"]
        S4["parse_financials.py"]
        S5["seed_sectors.py"]
        S6["seed_global_stocks.py"]
        S7["seed_dev_data.py / seed_all_stocks_dev.py"]
    end

    subgraph Data["Data layer"]
        Redis[("Redis\nlatest:{symbol}, ticks:{symbol} pub/sub,\nticks:dashboard batch channel")]
        PG[("PostgreSQL\n(schema in supabase/migrations/)")]
    end

    subgraph Missing["Referenced but NOT present in this repository"]
        API["apps/api — FastAPI backend\n(config.py, auth routes, admin panel)"]
        Sched["scheduler_service.py"]
        WebApp["apps/web — Next.js app\n(only node_modules + 1 store file exist)"]
    end

    Upstox --> UW
    TM --> UW
    UW --> Redis
    UW --> CB
    CB --> PG
    Yahoo --> YF
    YF --> Redis
    YF --> PG

    NSEBhav --> S2
    BSE --> S3
    Screener --> S4
    S1 --> PG
    S2 --> PG
    S3 --> PG
    S4 --> PG
    S5 --> PG
    S6 --> PG
    S7 --> PG

    Redis -.would feed.-> API
    PG -.would feed.-> API
    API -.would serve.-> WebApp
    WebApp -.reads via Zustand store.-> Redis

    classDef missing fill:#00000000,stroke:#999,stroke-dasharray: 5 5;
    class API,Sched,WebApp missing;
```

The dashed nodes/edges are **NOT VERIFIED** to exist as code — they represent the architecture implied by table comments, worker log messages (e.g. `token_manager.py` tells the operator to "Visit http://localhost:8000/api/v1/auth/upstox to authorize"), and `scratch/` scripts that `import` from `apps/api`, none of which is present in the repository at the time of this review.

## 5. Architectural Layers

Only two layers have real implementation:

- **Ingestion layer** (`apps/worker/`): connects to external market-data providers, validates/normalizes ticks, writes to cache and DB. Runs as a single long-lived asyncio process.
- **Batch/ETL layer** (`scripts/`): independent, manually-invoked Python CLI scripts that populate reference and fundamental data. No shared framework — each script opens its own `asyncpg` connection and duplicates its own CLI/logging boilerplate.

Layers implied by the schema but with **no corresponding code found**:
- API/application layer (REST/GraphQL endpoints, auth, request validation)
- Business/domain layer (ratio computation, DCF/valuation engine, reverse-DCF, ML scoring, signal engine, risk-flag engine)
- Presentation layer (the Next.js app is an empty shell)
- Scheduling/orchestration layer (cron/scheduler referenced in comments, not implemented)
- AI/RAG layer (embeddings generation, prompt construction, Gemini calls — only ad hoc test scripts in `scratch/`)

## 6. Major Components

| Component | Location | Responsibility | Dependencies |
|---|---|---|---|
| `UpstoxWorker` | `apps/worker/upstox_ws.py` | Connects to Upstox WebSocket, validates/normalizes ticks, writes to Redis, forwards to candle builder, reconnects with exponential backoff | `upstox_client` (monkey-patched), `redis.asyncio`, `token_manager`, `candle_builder` |
| Token manager | `apps/worker/token_manager.py` | Resolves a valid Upstox access token from file cache → env var → OAuth refresh, in that order | `httpx`, local `upstox_token.json` (not present in repo, created at runtime, documented as gitignored) |
| `CandleBuilder` | `apps/worker/candle_builder.py` | Aggregates ticks into 1-minute OHLCV candles in an in-process dict, flushes closed candles to `price_candles_1m` | `asyncpg` |
| `YahooPriceFetcher` | `apps/worker/yahoo_price_fetcher.py` | Polls Yahoo Finance v8 chart API every 60s for all stocks with a `yahoo_ticker`, writes Redis + upserts `realtime_quotes` | `httpx`, `yfinance` (imported but the actual fetch path uses raw `httpx` calls, not the `yfinance` library), `asyncpg` |
| NSE symbol importer | `scripts/import_nse_symbols.py` | Downloads/parses the official NSE equity list, upserts `stocks` | `httpx`, `asyncpg` |
| Bhavcopy downloader | `scripts/download_bhavcopy.py` | Downloads daily NSE bhavcopy ZIP/CSV, stores staging rows + daily candles | `httpx`, `asyncpg` |
| BSE fundamentals fetcher | `scripts/fetch_bse_data.py` | Fetches quarterly result XML from BSE's public API, parses into `financial_results` | `httpx`, `asyncpg` |
| Financials parser | `scripts/parse_financials.py` | Parses Screener.in Excel exports into `financial_results`, with basic sanity validation | `pandas`, `asyncpg` |
| Sector seeder | `scripts/seed_sectors.py` | Seeds `sector_classification` and assigns `stocks.sector_id` from a static symbol→sector map and optional NSE index-constituent lookups | `httpx`, `asyncpg` |
| Global stock seeder | `scripts/seed_global_stocks.py` | Seeds ~1,750 global-index stocks (S&P 500, Nasdaq 100, FTSE 100, Nikkei 225, etc.) with embedded symbol tables and `yahoo_ticker` mappings | `asyncpg` |
| Dev data seeders | `scripts/seed_dev_data.py`, `scripts/seed_all_stocks_dev.py` | Populate randomized/mock financials, ratios, intrinsic values, ML scores, and price history for local development | `asyncpg`, `random` |
| Market data store | `apps/web/store/market.ts` | Client-side Zustand store shape for quotes/market status/WS connection state — the only frontend application code present | `zustand` |

## 7. Component Dependency Graph

```
apps/worker/upstox_ws.py (entry point / main())
 ├── token_manager.ensure_valid_token()
 ├── candle_builder.CandleBuilder
 │    └── asyncpg (DATABASE_URL)
 └── yahoo_price_fetcher.YahooPriceFetcher   (imported and run concurrently via asyncio.gather)
      └── asyncpg (DATABASE_SYNC_URL / DATABASE_URL, its own pool via create_db_pool())

apps/worker/yahoo_price_fetcher.py can ALSO run standalone (its own __main__)

scripts/*.py — each script is an independent entry point with no imports between scripts
 (all connect directly to Postgres via DATABASE_SYNC_URL)
```

There is no shared internal library between `apps/worker` and `scripts/` — duplicated logic (env loading, DB URL fallback, logging setup) is copy-pasted across files rather than factored into a shared module. **VERIFIED FROM CODE**: every script independently defines `DB_URL = os.environ.get("DATABASE_SYNC_URL", "postgresql://postgres:password@localhost:5432/stocklens")` and its own `logging.basicConfig(...)`.

## 8. Data Architecture

Primary store is PostgreSQL (schema in `supabase/migrations/`). Redis is a secondary, ephemeral store used only by the worker for low-latency reads and pub/sub fan-out — it is not a system of record (all `SETEX` calls carry a TTL: 30s for Upstox ticks, 120s for Yahoo ticks).

Key tables actually written to by implemented code:
- `stocks`, `sector_classification` — reference data (written by `scripts/import_nse_symbols.py`, `seed_sectors.py`, `seed_global_stocks.py`)
- `realtime_quotes` — latest quote per symbol, upserted by `yahoo_price_fetcher.py` and `download_bhavcopy.py`
- `price_candles_1m` (monthly-partitioned, partitions pre-created through 2027-01) — written by `candle_builder.py`
- `price_candles_daily` — written by `download_bhavcopy.py`, `seed_all_stocks_dev.py`
- `financial_results` — written by `fetch_bse_data.py`, `parse_financials.py`, `seed_dev_data.py`/`seed_all_stocks_dev.py`
- `nse_bhavcopy_staging` — written by `download_bhavcopy.py` (staging table added in `002_pgvector_fo_peers.sql`)

Tables defined in the schema with **no observed writer** in the current codebase (only referenced by column comments, or by dev seed scripts producing synthetic values): `financial_ratios`, `valuation_assumptions`, `valuation_runs`, `dcf_outputs`, `reverse_dcf_outputs`, `intrinsic_values`, `cluster_results`, `ml_scores`, `risk_flags`, `signals`, `ai_reports`, `news_embeddings`, `stock_embeddings`, `fo_oi_history`, `peer_groups`, `watchlist`, `alerts`, `alert_history`, `portfolio`, `portfolio_holdings`, `backtest_runs`, `backtest_results`, `admin_overrides`, `audit_logs`, `system_jobs`, `data_import_logs`. These are populated only by `seed_dev_data.py`/`seed_all_stocks_dev.py` with **random mock values**, not by any real computation engine.

**PROBLEM — evidence-based**: `scripts/seed_all_stocks_dev.py` (line ~157) inserts into a table named `ml_cluster_results`, but the schema (`001_initial.sql`) defines the table as `cluster_results`. No migration creates `ml_cluster_results`. This INSERT will fail at runtime against the schema as currently defined.

## 9. API Architecture

**NOT VERIFIED / does not exist.** No router, controller, or HTTP endpoint definitions were found anywhere in the repository (`FastAPI(`, `APIRouter`, `@app.` patterns were searched for across `apps/` and found only in `apps/web/node_modules` — third-party library internals, not project code). `fastapi`/`uvicorn` are listed in `apps/worker/requirements.txt` but the worker's Dockerfile `CMD` runs `python upstox_ws.py`, not a `uvicorn` server.

## 10. Database Architecture

See §8 and the full schema in `supabase/migrations/001_initial.sql`, `002_global_stocks.sql`, `002_pgvector_fo_peers.sql`. Notable design points, **VERIFIED FROM CODE**:
- `price_candles_1m` is `PARTITION BY RANGE (ts)` with monthly partitions manually created from 2025-07 through 2026-12 in the migration file. No code was found that creates future partitions automatically — this will need manual/scripted extension before 2027.
- `pgvector` is used twice: `news_embeddings.embedding vector(768)` (IVFFlat index, migration 001) and `stock_embeddings.embedding vector(768)` (HNSW index, migration `002_pgvector_fo_peers.sql`) — two separate, seemingly overlapping embedding tables for the same 768-dim (Gemini `text-embedding-004`-sized) vectors.
- **PROBLEM — evidence-based**: two migration files are both prefixed `002_` (`002_global_stocks.sql` and `002_pgvector_fo_peers.sql`). Filename-lexicographic migration runners would apply `002_global_stocks.sql` before `002_pgvector_fo_peers.sql` (alphabetical), which happens to be a safe order here, but the duplicate numbering is inconsistent with the `001_`, `002_`, `003_`... convention implied by the first file, and there is no migration-runner tool in the repo to confirm intended/enforced ordering.
- No ORM is used anywhere; all queries are raw SQL via `asyncpg`.

## 11. AI/LLM Architecture

**NOT VERIFIED as implemented.** `scratch/test_gemini.py`, `scratch/test_all_models.py`, and `scratch/list_models.py` exercise Google's `google.genai` SDK against a `GEMINI_API_KEY`/`GEMINI_MODEL_FAST` config, but they `import` those settings `from config import settings` after inserting `apps/api` onto `sys.path` — a directory that does not exist in this repository. **These scratch scripts cannot currently run successfully as-is** (they would raise `ModuleNotFoundError: No module named 'config'`). The schema's `ai_reports` and `news_embeddings`/`stock_embeddings` tables describe an intended RAG pipeline (store embeddings → retrieve context → generate a disclaimed summary via Gemini), but no retrieval, prompt-construction, or generation code exists outside these disconnected experiments.

## 12. External Integrations

| Integration | Purpose | Where used | Auth |
|---|---|---|---|
| Upstox API (v2 login, v3 market-data-feed) | Real-time NSE tick streaming | `apps/worker/upstox_ws.py`, `token_manager.py` | OAuth2 access token (daily-expiring), `UPSTOX_API_KEY`/`UPSTOX_API_SECRET`/`UPSTOX_REDIRECT_URI` for refresh |
| Yahoo Finance (undocumented v8 chart API + crumb/cookie flow) | Delayed global stock prices | `apps/worker/yahoo_price_fetcher.py` | None (session cookie + crumb scraped per poll cycle) |
| NSE archives / nseindia.com | Equity list, bhavcopy, sector index constituents | `scripts/import_nse_symbols.py`, `download_bhavcopy.py`, `seed_sectors.py` | None (public, but requires browser-like headers/session warm-up) |
| BSE India API | Quarterly financial results XML | `scripts/fetch_bse_data.py` | None (public) |
| Google Gemini (`google.genai`) | AI report generation (planned) | `scratch/*.py` only | `GEMINI_API_KEY` — **currently broken** (see §11) |

## 13. Configuration Architecture

All configuration is via environment variables loaded from a project-root `.env` file (not present in the repo — must be created by the developer; no `.env.example` exists). See `docs/EXECUTION.md` §4 for the full variable table. There is no central config module in the currently-present code — each file reads `os.environ` directly (the `config.py`/`settings` object referenced by `scratch/` scripts lives in the missing `apps/api`).

## 14. Security Architecture

- No authentication/authorization code exists in the repository (no API layer to protect).
- Upstox tokens are cached in a local JSON file (`apps/worker/upstox_token.json`) documented in-line as "gitignored" — consistent with no `.gitignore` being present and the file being absent from the checkout, but there is also no `.gitignore` in the repo to actually enforce that (see §20).
- `scripts/parse_financials.py` builds raw SQL column lists/placeholders dynamically from dict keys (`INSERT INTO financial_results ({cols}) VALUES ({placeholders})` and a similarly dynamic `UPDATE ... SET {set_clause}`), but the dict keys originate from a fixed internal mapping (`SCREENER_PL_COLS`/`SCREENER_BS_COLS`/`SCREENER_CF_COLS` and hardcoded literal keys), not from unsanitized user input — values are still passed as bound parameters (`$1, $2, ...`), so this is parameterized SQL with a dynamically-built column list, not classic SQL injection via values. **INFERRED low risk**, but worth flagging since column names are string-concatenated into the query text.
- `test_ws.py` and `scratch/*.py` print API keys/tokens to stdout (`print("GEMINI_API_KEY:", ...)`, token debug logging) — fine for local debugging scripts but not safe if ever run in a shared/CI context.

## 15. Concurrency Architecture

- The worker (`upstox_ws.py`) is single-process `asyncio`, with the Upstox SDK's WebSocket client running its own OS thread (`threading.Thread(target=self.ws.run_forever, ...)` inside the monkey-patched `connect()`); messages are bridged back to the asyncio loop via `asyncio.run_coroutine_threadsafe`.
- `YahooPriceFetcher.run_once()` fans out concurrent HTTP requests across all symbols bounded by an `asyncio.Semaphore(8)`, batching Yahoo calls in groups (`BATCH_SIZE = 50`, though the actual fetch loop issues one HTTP request per symbol under the semaphore rather than a true batched multi-symbol call).
- `CandleBuilder` uses a module-level `asyncio.Lock` (`_candle_lock`) around a module-level dict (`_candles`) — safe within one process but not safe across multiple worker replicas, since state is in-memory and not shared.
- `main()` in `upstox_ws.py` runs the Upstox worker and the Yahoo poller concurrently via `asyncio.gather`, sharing one Redis connection and (separately) each holding its own asyncpg pool.

## 16. Error Handling Architecture

- `UpstoxWorker.run()` implements exponential backoff reconnection (`RECONNECT_BASE_DELAY=1s` up to `RECONNECT_MAX_DELAY=60s`), resetting the delay after any clean disconnect.
- `YahooPriceFetcher.run()` catches exceptions per poll cycle and continues on a fixed interval, logging the error rather than crashing.
- `token_manager.ensure_valid_token()` falls back through three sources (cached file → env var → refresh token) and returns `None` (with an actionable log message) if all fail; callers treat `None` as fatal (`RuntimeError("No valid Upstox access token available")`).
- Tick processing (`_validate_tick`) silently discards malformed ticks rather than raising.
- All `scripts/*.py` catch and log per-row/per-symbol exceptions and continue batch processing rather than aborting the whole run.

## 17. Logging & Observability

- `structlog` is declared in `requirements.txt` but **not actually used** — every file observed uses stdlib `logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s — %(message)s")` (or similar), printed to stdout/console. **VERIFIED FROM CODE**.
- No metrics, tracing, or health-check endpoints exist (no API layer to host them).
- `system_jobs`, `audit_logs`, and `data_import_logs` tables exist in the schema for operational tracking, but no code in this repository writes to them.

## 18. Architecture Strengths

- The database schema is thorough and internally consistent for a fundamentals-and-valuation domain (clear separation of raw fundamentals, derived ratios, valuation outputs, ML outputs, and signals).
- The worker's reconnect/backoff and tick-validation logic is defensive and production-minded for a real-time feed.
- The dual price-source design (real-time Upstox for NSE, delayed Yahoo polling for global) is cleanly separated into two independent, composable classes that can run together or standalone.
- ETL scripts consistently use `ON CONFLICT` upsert patterns, making them safe to re-run.

## 19. Architecture Weaknesses

- The application/business logic layer implied by the schema (ratios, valuation, ML scoring, signals, AI reports) has no implementation in this repository — the schema is far ahead of the code.
- The frontend (`apps/web`) is an empty scaffold: dependencies are installed but there is no `package.json`, no pages, no components, and no way to run it.
- No shared configuration/DB-connection module — every script reimplements the same env-loading and connection boilerplate.
- `scratch/` scripts depend on a nonexistent `apps/api` package and cannot run as committed.
- In-memory candle state (`CandleBuilder`) does not scale beyond a single worker process/replica.
- No `.gitignore`, no `.env.example`, no automated tests beyond one manual debug script (`apps/worker/test_ws.py`), no CI/CD configuration.
- Duplicate `002_` migration filenames create ambiguity in migration ordering.

## 20. Current Architecture Status

| Area | Status | Evidence |
|---|---|---|
| Real-time NSE ingestion (Upstox) | 🟢 WORKING (code-complete; **not runtime-verified** in this review — no live credentials were exercised) | `apps/worker/upstox_ws.py` |
| Global stock polling (Yahoo) | 🟢 WORKING (code-complete; not runtime-verified) | `apps/worker/yahoo_price_fetcher.py` |
| 1-minute candle aggregation | 🟢 WORKING (code-complete; not runtime-verified) | `apps/worker/candle_builder.py` |
| Data import/seed scripts | 🟡 PARTIALLY WORKING — most are code-complete, but `seed_all_stocks_dev.py` references a table (`ml_cluster_results`) not defined by any migration | `scripts/*.py` vs. `supabase/migrations/001_initial.sql` |
| Database schema | 🟢 WORKING as a schema definition; 🔵 UNUSED in large part — most tables have no writer in this codebase | `supabase/migrations/*.sql` |
| API / backend service | 🔴 PROBLEM / absent — referenced everywhere (log messages, scratch imports, requirements.txt) but the directory does not exist | absence of `apps/api/` |
| Valuation / ML / signal engines | 🟠 PLACEHOLDER — only simulated via random mock data in dev-seed scripts | `scripts/seed_all_stocks_dev.py` |
| AI/RAG report generation | 🟠 PLACEHOLDER / broken — only disconnected experiments in `scratch/`, dependent on the missing `apps/api/config.py` | `scratch/test_gemini.py` etc. |
| Frontend web app | 🟠 PLACEHOLDER — dependencies installed, one Zustand store file, no pages/components/config | `apps/web/` |
| Scheduler / cron orchestration | 🔴 PROBLEM / absent — referenced in comments ("via scheduler at 16:30 IST", "via scheduler_service.py") but no such file exists | comments in `download_bhavcopy.py`, `fetch_bse_data.py` |
| Tests | ⚪ UNKNOWN / effectively absent — only `apps/worker/test_ws.py`, a manual debug script with a hardcoded machine-specific path, not an automated test | `apps/worker/test_ws.py` |
| Docker / deployment | 🟡 PARTIALLY IMPLEMENTED — only the worker has a Dockerfile; no compose file, no deployment config for DB/Redis/API/web | `apps/worker/Dockerfile` |

## 21. Architecture Summary

StockLens is architecturally designed as a full-stack stock-research platform (ingestion → fundamentals → computed ratios → multi-scenario valuation → ML risk/quality scoring → plain-language signals → AI-generated, RAG-grounded research summaries → personal watchlist/portfolio/backtesting), expressed almost entirely through one very comprehensive PostgreSQL schema. In its current state, the repository implements only the real-time/near-real-time **data-ingestion** slice of that vision (the `apps/worker` service) plus a set of **manual, unscheduled ETL scripts** for populating reference and fundamental data. The API layer, business-logic/valuation/ML engines, AI layer, scheduler, and frontend that the schema and code comments describe do not exist in this repository as committed. Anyone picking up this project should treat the SQL schema as a target data model, not as evidence that the corresponding features are built.
