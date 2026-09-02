# StockLens — Execution

> This document explains what actually happens when you install, configure, and run the parts of StockLens that exist in this repository. Commands are transcribed from code and file headers — none are invented. Where something could not be safely or completely verified by running it, it is marked **NOT VERIFIED AT RUNTIME**.

## 1. Prerequisites

**VERIFIED FROM CODE** (derived from imports/Dockerfile, not from a documented setup guide — none exists):
- Python 3.12 (`apps/worker/Dockerfile`: `FROM python:3.12-slim`)
- PostgreSQL with the `uuid-ossp`, `vector` (pgvector), `pg_trgm`, and `btree_gin` extensions installable (`supabase/migrations/001_initial.sql` lines 8-11)
- Redis (any version compatible with `redis==5.2.1` / `redis.asyncio`)
- A Upstox developer account + API credentials, for the real-time NSE feed
- `gcc`, `g++`, `libpq-dev` at the OS level to build `asyncpg`/`psycopg`-family packages (installed in the Dockerfile; needed locally too on Linux if wheels aren't available)

**UNKNOWN / NOT VERIFIED**: Node.js/npm version for the frontend — no `package.json` or `.nvmrc` exists to pin one. `node_modules` is present and includes `next`, `react`, `typescript`, etc., implying Node was used at some point, but the exact version is not recorded anywhere in the repo.

## 2. Installation

**Worker (Python):**
```
pip install -r apps/worker/requirements.txt
```
**VERIFIED FROM CODE** — this is the only Python dependency manifest in the repo. There is no root-level `requirements.txt`; `scripts/*.py` share the same runtime dependencies (`asyncpg`, `httpx`, `python-dotenv`) plus `pandas` (used only by `scripts/parse_financials.py`), none of which are declared in a scripts-specific manifest — **you must install `pandas` and `openpyxl`-family Excel support separately if you intend to run `parse_financials.py`; they are not listed anywhere**.

**Frontend (`apps/web`):**
No installation command can be given — there is no `package.json` in `apps/web/`, only a pre-populated `node_modules/` folder and one source file (`store/market.ts`). Running `npm install` here would do nothing useful without a `package.json` to read. **NOT VERIFIED AT RUNTIME**; this is a structural absence, not a command that failed.

**Database:**
No migration runner (Alembic, Supabase CLI, etc.) is present. Migrations must be applied manually with `psql`, exactly as instructed in the header comment of the first migration file:
```
psql $DATABASE_URL -f supabase/migrations/001_initial.sql
psql $DATABASE_URL -f supabase/migrations/002_global_stocks.sql
psql $DATABASE_URL -f supabase/migrations/002_pgvector_fo_peers.sql
```
Run in this order — `002_pgvector_fo_peers.sql` creates `stock_embeddings`/`peer_groups`/etc. and alters `admin_overrides`, which is independent of `002_global_stocks.sql`'s column additions to `stocks`, but both depend on `001_initial.sql` having run first. **VERIFIED FROM CODE** (header comments in each file); **NOT VERIFIED AT RUNTIME** (no live database was migrated during this review).

## 3. Environment Setup

Every worker/script file loads a `.env` file:
- `apps/worker/upstox_ws.py` and `apps/worker/yahoo_price_fetcher.py` load it explicitly from the project root: `Path(__file__).resolve().parent.parent.parent / ".env"` — i.e., a `.env` file at the repository root (`stocklens/.env`).
- `scripts/*.py` call plain `load_dotenv()`, which searches the current working directory upward — so scripts must be run from a directory where python-dotenv can find the root `.env` (typically the repo root).

**No `.env.example` file exists in the repository.** The variable table below (§4) was reconstructed by grepping every `os.environ[...]` / `os.environ.get(...)` call in `apps/worker/` and `scripts/`.

## 4. Environment Variables

| Variable | Purpose | Required | Default | Used by |
|---|---|---|---|---|
| `REDIS_URL` | Redis connection string | Yes (worker only) | none — `KeyError` if missing | `upstox_ws.py`, `yahoo_price_fetcher.py` |
| `DATABASE_URL` | Async Postgres URL for the candle builder (`+asyncpg` suffix stripped before use) | Yes (worker only) | none — `KeyError` if missing | `candle_builder.py` |
| `DATABASE_SYNC_URL` | Postgres URL used by all `scripts/*.py` and by `yahoo_price_fetcher.py`'s standalone `create_db_pool()` | No | `postgresql://postgres:password@localhost:5432/stocklens` | all `scripts/*.py`, `yahoo_price_fetcher.py` |
| `UPSTOX_ACCESS_TOKEN` | Manually-obtained Upstox access token (fallback if no cached/refreshable token) | No (but needed if no token file / refresh token exists) | `""` | `upstox_ws.py`, `token_manager.py`, `test_ws.py` |
| `UPSTOX_REFRESH_TOKEN` | Refresh token used to mint a new access token | No | `""` | `token_manager.py` |
| `UPSTOX_API_KEY` | Upstox app client ID, required only for the token-refresh HTTP call | Conditionally (only if refreshing) | none — `KeyError` if refresh is attempted without it | `token_manager.py` |
| `UPSTOX_API_SECRET` | Upstox app client secret, required only for token refresh | Conditionally | none — `KeyError` if refresh is attempted without it | `token_manager.py` |
| `UPSTOX_REDIRECT_URI` | OAuth redirect URI registered with Upstox, required only for token refresh | Conditionally | none — `KeyError` if refresh is attempted without it | `token_manager.py` |
| `GEMINI_API_KEY` | Google Gemini API key | No (only referenced in `scratch/`) | none | `scratch/test_gemini.py`, `test_all_models.py`, `list_models.py` — via a `config.settings` module that **does not exist** in this repo (`apps/api` is absent) |
| `GEMINI_MODEL_FAST` | Preferred fast Gemini model name | No (only referenced in `scratch/`) | none | `scratch/test_gemini.py` (same missing-module caveat) |

No secret values were found hardcoded in any tracked source file; token/API-key values are only read from environment/`.env`.

## 5. Main Commands

All commands below are transcribed directly from `if __name__ == "__main__":` blocks / file docstrings — none are invented.

| Command | Purpose | Entry point |
|---|---|---|
| `python apps/worker/upstox_ws.py` | Start the real-time worker: Upstox NSE stream + (if `asyncpg`/`yahoo_price_fetcher` import succeeds) the Yahoo global-stock poller, concurrently | `upstox_ws.py:main()` |
| `python apps/worker/yahoo_price_fetcher.py` | Run the Yahoo Finance poller standalone (without the Upstox connection) | `yahoo_price_fetcher.py:main()` |
| `python scripts/import_nse_symbols.py [--file PATH] [--source nse\|bhavcopy\|file]` | Import the NSE equity list into `stocks` | `import_nse_symbols.py:main()` |
| `python scripts/download_bhavcopy.py [--date YYYY-MM-DD] [--backfill N]` | Download & store NSE daily bhavcopy | `download_bhavcopy.py:run()` |
| `python scripts/fetch_bse_data.py [--symbol SYM] [--limit N]` | Fetch BSE quarterly results for one symbol or a batch of stale-data stocks | `fetch_bse_data.py:run_batch()` |
| `python scripts/parse_financials.py --file FILE.xlsx (--symbol SYM \| --auto)` | Parse a Screener.in Excel export into `financial_results` | `parse_financials.py:main()` |
| `python scripts/seed_sectors.py` | Seed `sector_classification` and tag `stocks.sector_id` from a static symbol map | `seed_sectors.py:run()` |
| `python scripts/seed_global_stocks.py [--exchange NYSE] [--force]` | Seed ~1,750 global-index stocks with `yahoo_ticker`s | `seed_global_stocks.py` (module-level `main`, per docstring) |
| `python scripts/seed_dev_data.py` | Seed realistic mock dev data (only if DB is empty) | `seed_dev_data.py` |
| `python scripts/seed_all_stocks_dev.py` | Populate mock financials/ratios/valuations/ML-scores/signals/price-history for **every** active stock currently in the DB | `seed_all_stocks_dev.py:run()` |
| `psql $DATABASE_URL -f supabase/migrations/<file>.sql` | Apply a schema migration | n/a (raw SQL) |
| `docker build -t stocklens-worker apps/worker` then `docker run ... stocklens-worker` | Build/run the worker in a container | `apps/worker/Dockerfile` |

There is **no command to run the frontend** (`apps/web`) — no `package.json`, hence no `npm run dev`/`build`/`start` script exists to invoke. There is **no command to run an API server** — no such application exists in the repo.

## 6. Startup Flow

```mermaid
flowchart TD
    A["python apps/worker/upstox_ws.py"] --> B["Load .env from repo root\n(load_dotenv override=True)"]
    B --> C["Read REDIS_URL, UPSTOX_ACCESS_TOKEN\n(os.environ)"]
    C --> D["Create UpstoxWorker(redis_client)\n+ its own CandleBuilder"]
    D --> E["Register SIGTERM/SIGINT handlers\n(best-effort; skipped on Windows)"]
    E --> F{"import asyncpg +\nyahoo_price_fetcher\nsucceeds?"}
    F -- yes --> G["create_db_pool() for Yahoo fetcher\nasyncio.gather(worker.run(), yahoo_fetcher.run())"]
    F -- no (ImportError) --> H["await worker.run() only"]
    G --> I["UpstoxWorker.run(): load_subscribed_symbols()\nthen loop _connect_and_stream() with backoff"]
    H --> I
```

## 7. Detailed Startup Execution

1. `upstox_ws.py` computes `_ROOT_ENV = <repo root>/.env` and calls `load_dotenv(dotenv_path=_ROOT_ENV, override=True)`.
2. Module-level config is read immediately from `os.environ`: `REDIS_URL` (raises `KeyError` if unset — the process will not start without it), `UPSTOX_ACCESS_TOKEN` (optional, defaults to `""`).
3. `main()` creates an async Redis client via `aioredis.from_url(REDIS_URL, ...)` and constructs `UpstoxWorker(redis)`, which internally constructs its own `CandleBuilder(redis_client)`.
4. Signal handlers for `SIGTERM`/`SIGINT` are registered to call `worker.stop()`; `loop.add_signal_handler` raises `NotImplementedError` on Windows and is caught/ignored, meaning **graceful shutdown via signal is not available on Windows** — Ctrl+C behavior would fall back to default asyncio/KeyboardInterrupt handling.
5. `main()` attempts `import asyncpg` and `from yahoo_price_fetcher import YahooPriceFetcher, create_db_pool`. If this succeeds, it opens a DB pool and runs both `worker.run()` and `yahoo_fetcher.run()` concurrently via `asyncio.gather`. If it fails (`ImportError`), it logs a warning and runs only `worker.run()`.
6. `UpstoxWorker.run()` sets `running=True`, loads the symbol subscription list (`load_subscribed_symbols()` — reads `apps/worker/subscribed_symbols.json` if present, else falls back to a 5-symbol default list: TCS, Infosys, HDFC Bank, SBI, Reliance), then loops calling `_connect_and_stream(symbols)`, catching any exception and reconnecting with exponential backoff (1s → doubling → capped at 60s), resetting the delay after any clean disconnect.
7. `_connect_and_stream()` calls `token_manager.ensure_valid_token()` (see §4 of ARCHITECTURE.md for its fallback chain); if no token is available it raises `RuntimeError`, which is caught by the retry loop in step 6 and retried after a backoff delay — **this means a persistently-missing token causes an infinite retry loop with log spam, not a fast failure**.
8. On a valid token, the Upstox SDK's WebSocket `connect()` method is monkey-patched (because Upstox deprecated the SDK's default connection path — see comment block in `upstox_ws.py` lines 185-222) to first call `GET https://api.upstox.com/v3/feed/market-data-feed/authorize`, then open a `websocket.WebSocketApp` in a background thread against the returned `authorized_redirect_uri`.
9. On the `open` event, the streamer subscribes to all loaded symbols in `mode="full"`. On each `message`, ticks are normalized and handed to `UpstoxWorker._process_tick()` via `asyncio.run_coroutine_threadsafe` (bridging the SDK's background thread back into the asyncio loop).
10. The coroutine `_connect_and_stream()` blocks on `disconnect_event.wait()` until the SDK signals `close` or `error`, at which point control returns to the retry loop in `run()`.

## 8. Development Execution

No dedicated "dev mode" exists for the worker — it runs the same code path in any environment; `.env` values are the only thing that differ. For local development without live Upstox/Yahoo access, the pattern implied by the scripts is:
1. Apply migrations manually (§2).
2. Run `scripts/import_nse_symbols.py` and/or `scripts/seed_global_stocks.py` to populate `stocks`.
3. Run `scripts/seed_sectors.py` for sector taxonomy.
4. Run `scripts/seed_dev_data.py` or `scripts/seed_all_stocks_dev.py` for mock financials/ratios/valuations so downstream (non-existent) consumers would have data to read.

This sequence is **INFERRED** from script docstrings and dependencies between tables (e.g., `seed_all_stocks_dev.py` joins `stocks` and `realtime_quotes`, so stocks — and ideally quotes — should exist first); it is not documented anywhere in the repo as an explicit setup guide.

## 9. Request Execution

**NOT APPLICABLE** — there is no HTTP API in this repository to trace a request through.

## 10. Major Workflow Execution

**Tick ingestion (Upstox), end to end:**
```
Upstox WS message
 → on_message() callback (SDK thread)
 → asyncio.run_coroutine_threadsafe(UpstoxWorker._process_tick, loop)
 → _validate_tick() — discard if ltp <= 0 or missing symbol/instrument_token
 → _normalize_tick() — strip "NSE_EQ:"/"NSE_INDEX:" prefixes, compute change_abs/change_pct
 → redis.setex("latest:{symbol}", 30, tick_json)
 → redis.publish("ticks:{symbol}", tick_json)
 → queue into _pending_dashboard_updates
 → CandleBuilder.on_tick(symbol, tick) — extend/close 1-minute candle in memory
 → every >=0.5s: redis.publish("ticks:dashboard", batch_of_pending_updates)
```

**Candle flush (on minute rollover):**
```
CandleBuilder.on_tick() detects minute boundary changed
 → _write_candle() for the just-closed candle
 → asyncpg pool (lazily created from DATABASE_URL) 
 → INSERT INTO price_candles_1m (...) ON CONFLICT DO NOTHING
```

**Yahoo poll cycle (every 60s):**
```
YahooPriceFetcher.run_once()
 → load_symbols() — SELECT stocks WHERE yahoo_ticker IS NOT NULL AND is_active
 → _get_crumb() — warm cookies via finance.yahoo.com, fetch a session crumb
 → for each symbol (bounded by Semaphore(8)): _fetch_single() → Yahoo v8 chart API
 → redis.setex("latest:{symbol}", 120, tick_json); redis.publish("ticks:{symbol}", ...)
 → batch db.executemany(...) UPSERT into realtime_quotes
 → redis.publish("ticks:dashboard", batch_message)
```

**Bhavcopy import:**
```
download_bhavcopy.run(date | backfill_days)
 → download_bhavcopy(date): GET NSE zip URL → unzip in memory → csv.DictReader
   (filters SERIES in EQ/BE/BZ; skips weekends)
 → store_bhavcopy(): per-row INSERT ... ON CONFLICT DO NOTHING into nse_bhavcopy_staging
   → if the symbol exists in `stocks` and has open+close: upsert price_candles_daily
     and upsert realtime_quotes (ltp/close only)
```

## 11. AI Execution Flow

**NOT VERIFIED / not functional.** The only AI-related executable code is in `scratch/`, and each script fails at import time because it inserts `apps/api` onto `sys.path` and does `from config import settings` — `apps/api` does not exist in this repository. If that directory were restored, the intended flow (per the scripts) would be: load `GEMINI_API_KEY`/`GEMINI_MODEL_FAST` from `config.settings` → construct a `google.genai.Client` → call `client.models.generate_content(model=..., contents=...)`. No prompt-construction, context-retrieval, or embedding-generation code exists anywhere in the repository.

## 12. Database Execution Flow

Every write path in this repo uses the same pattern: open (or reuse a pooled) `asyncpg` connection → parameterized SQL with `ON CONFLICT` upsert semantics → no ORM, no transaction wrapping beyond what a single `execute`/`executemany` call provides implicitly. The worker's `CandleBuilder` lazily creates its pool on first candle flush (`_get_pool()`), separate from the Yahoo fetcher's pool (`create_db_pool()`), separate again from whatever pool `scripts/*.py` open per-invocation via `asyncpg.connect()` (a single connection, not a pool, for CLI scripts).

## 13. Background Jobs

There is no scheduler, cron, or task queue in this repository. `download_bhavcopy.py` and `fetch_bse_data.py` both contain comments describing an intended daily schedule ("Run via: ... Or called via scheduler at 16:30 IST", "This script runs automatically at 17:00 IST daily (via scheduler_service.py)"), but `scheduler_service.py` does not exist anywhere in the repo, and no cron/systemd/Docker Compose/GitHub Actions schedule configuration was found. **All batch scripts must currently be invoked manually.**

## 14. Testing Commands

**No automated test suite exists.** `apps/worker/test_ws.py` is a manual, interactive debug script (not a `pytest`/`unittest` test — it has no assertions, just prints) for probing the Upstox WebSocket authorize/connect flow directly. It also hardcodes an absolute Windows path specific to one developer's machine: `load_dotenv(r"c:\New folder (2)\stocklens\.env")` — this will fail with a wrong path on any other machine (including this one, whose repo root is `d:\New folder (2)\stocklens`). Run manually as `python apps/worker/test_ws.py` only after fixing that path or setting a matching `.env` location.

No `pytest`, `unittest`, `jest`, or similar test runner configuration was found in the repository.

## 15. Build Process

**Worker:** no build step — it is interpreted Python, run directly.
**Frontend:** no build is possible — there is no `package.json`/`next.config.*` to define a `next build` (or equivalent) target.
**Database:** "build" is the manual `psql -f` migration application described in §2.

## 16. Docker Execution

Only `apps/worker/Dockerfile` exists:
```
docker build -t stocklens-worker apps/worker
docker run --env-file .env stocklens-worker
```
(The `--env-file .env` form is **INFERRED** as the natural way to supply the environment variables in §4 to the container; the Dockerfile itself does not declare `ENV` defaults or an `.env`-loading mechanism beyond what `python-dotenv` does inside the app, and `python-dotenv`'s root-relative path lookup (`parent.parent.parent`) would not resolve correctly inside the container's `/app` working directory unless a `.env` is also copied/mounted there — **NOT VERIFIED AT RUNTIME**, flagged as a likely deployment gotcha.)

The image: builds on `python:3.12-slim`, installs `gcc`/`g++`/`libpq-dev` for native extension builds, installs `requirements.txt`, copies the worker source, creates and switches to an unprivileged `workeruser`, and runs `CMD ["python", "upstox_ws.py"]`. There is no `docker-compose.yml` in the repo to orchestrate this alongside Postgres/Redis — those are assumed to be reachable via the configured `DATABASE_URL`/`REDIS_URL`.

## 17. Deployment Execution

**NOT VERIFIED / not defined.** No CI/CD configuration, cloud deployment manifests (Kubernetes, Terraform, Fly.io, Render, Railway, etc.), or `docker-compose.yml` exist in the repository. The `supabase/` directory name suggests an intended Supabase-hosted Postgres, but no `supabase/config.toml` or Supabase CLI project file is present to confirm that beyond the raw SQL living in a `supabase/migrations/` folder.

## 18. Shutdown Flow

On POSIX systems, `SIGTERM`/`SIGINT` trigger `handle_shutdown()`, which calls `worker.stop()` (sets `running=False`, so the outer retry loop in `run()` will exit after its current iteration) and `loop.stop()`. On Windows, signal handlers cannot be registered (`NotImplementedError` is caught and ignored), so shutdown relies on default Python/asyncio interrupt handling rather than the custom graceful path. `YahooPriceFetcher.main()` (standalone mode) catches `KeyboardInterrupt`/`asyncio.CancelledError` explicitly, then calls `fetcher.stop()`, closes the Redis client, and closes the DB pool in a `finally` block.

## 19. Error/Failure Flow

| Failure | Behavior |
|---|---|
| `REDIS_URL` / `DATABASE_URL` env var missing | Immediate `KeyError` at import time — process exits before doing anything |
| No valid Upstox token available | `_connect_and_stream()` raises `RuntimeError`; caught by `run()`'s retry loop, which logs and retries with exponential backoff indefinitely |
| Upstox WebSocket errors/closes | `on_error`/`on_close` set `disconnect_event`; `_connect_and_stream()` returns; outer loop reconnects with backoff |
| Malformed tick (bad price/missing symbol) | `_validate_tick()` returns `False`; tick is logged at DEBUG and dropped, no exception |
| Candle DB write fails | Caught inside `_write_candle()`, logged as an error; the in-memory candle state is not retried or persisted elsewhere — **that candle's data is lost** |
| Yahoo crumb fetch fails | `run_once()` logs an error and returns early, skipping that entire poll cycle (no partial fetch) |
| Yahoo per-symbol fetch fails (incl. HTTP 429) | Caught inside `_fetch_single()`; one retry after a 5s sleep on 429, otherwise returns `None` and that symbol is silently skipped for the cycle |
| Any DB write error in `scripts/*.py` | Caught per-row/per-symbol, logged, counted as "skipped"/"failed" in the summary printed at the end; the script continues |

## 20. Debugging Guide

| Problem | Where to look | Relevant file/function |
|---|---|---|
| Worker won't start / crashes immediately | Check `.env` exists at repo root and defines `REDIS_URL` and `DATABASE_URL` | `upstox_ws.py` module-level `os.environ[...]` calls |
| No ticks arriving | Check Upstox token validity/log line "No valid Upstox access token found!" and the printed remediation steps | `token_manager.ensure_valid_token()` |
| WebSocket connects then immediately closes | Check the `/v3/feed/market-data-feed/authorize` call succeeds and returns `authorized_redirect_uri`; inspect the monkey-patched `patched_connect()` | `upstox_ws.py` lines ~185-222 |
| Candles missing in `price_candles_1m` | Check `DATABASE_URL` is reachable from the worker process and that `_write_candle()` isn't logging `"Failed to write candle"` | `candle_builder.py:_write_candle()` |
| Global stock prices stale/missing | Check `stocks.yahoo_ticker` is populated (run `seed_global_stocks.py`), and check for `"Could not get Yahoo crumb"` in logs (Yahoo may be blocking the scraping headers) | `yahoo_price_fetcher.py:_get_crumb()` |
| `seed_all_stocks_dev.py` fails partway | Check for the `ml_cluster_results` vs `cluster_results` table-name mismatch documented in ARCHITECTURE.md §8 | `scripts/seed_all_stocks_dev.py` |
| `scratch/test_gemini.py` (or similar) fails with `ModuleNotFoundError: config` | Expected — it depends on `apps/api`, which does not exist in this repo | `scratch/*.py` |
| Frontend won't run | Expected — there is no `package.json`/app source beyond `store/market.ts` | `apps/web/` |

All logging in every observed file goes to stdout via stdlib `logging` — there are no separate log files, log aggregation, or structured-logging (despite `structlog` being a declared dependency) in the current code.

## 21. Runtime Verification

**What was actually executed during this review:** nothing. This was a static, read-only code and file-structure inspection (per the task's constraints) — no processes were started, no database was migrated, no external APIs (Upstox, Yahoo, BSE, NSE, Gemini) were called, and no scripts were run.

**Explicitly marked NOT VERIFIED AT RUNTIME:**
- Whether `upstox_ws.py` successfully connects to Upstox given valid credentials.
- Whether `yahoo_price_fetcher.py` successfully authenticates against Yahoo's crumb endpoint (this is a scraping-style integration against an undocumented API and is inherently fragile to Yahoo-side changes).
- Whether any `scripts/*.py` run to completion against a real database.
- Whether the Dockerfile builds and runs successfully.
- Whether `seed_all_stocks_dev.py` actually fails on the `ml_cluster_results` insert (this is a static-analysis finding based on comparing the script's SQL to the schema file — very likely to fail, but not executed to confirm).

Anyone relying on this document to judge whether the system "works" should treat every claim above as **code inspection, not proof of runtime behavior** — the two are explicitly not the same.

## 22. Complete Execution Flow

```mermaid
flowchart TD
    Start(["Developer runs\npython apps/worker/upstox_ws.py"]) --> Env["Load .env, read REDIS_URL/DATABASE_URL"]
    Env --> Token["token_manager.ensure_valid_token()"]
    Token -->|token found| Connect["Authorize + open Upstox WS\n(monkey-patched v3 flow)"]
    Token -->|no token| Retry["Log remediation steps,\nraise RuntimeError, backoff-retry"]
    Retry --> Token
    Connect --> Sub["Subscribe to symbols\n(subscribed_symbols.json or 5-symbol default)"]
    Sub --> Tick["Ticks arrive on SDK thread\n→ bridged into asyncio loop"]
    Tick --> Validate["_validate_tick / _normalize_tick"]
    Validate --> RedisWrite["Redis: latest:{symbol} (30s TTL)\n+ publish ticks:{symbol}"]
    Validate --> Candle["CandleBuilder.on_tick()\n→ flush closed candle to price_candles_1m"]
    Validate --> Batch["Batch every 0.5s\n→ publish ticks:dashboard"]

    Start2(["Concurrently (asyncio.gather):\nYahooPriceFetcher.run()"]) --> Poll["Every 60s: load_symbols()\n→ crumb → fetch per symbol (Semaphore 8)"]
    Poll --> RedisWrite2["Redis: latest:{symbol} (120s TTL)\n+ publish ticks:{symbol}"]
    Poll --> DBUpsert["Batch UPSERT realtime_quotes"]
    Poll --> Batch2["Publish ticks:dashboard"]

    RedisWrite -.no known consumer in this repo.-> Void["??? (would be apps/web / apps/api,\nneither of which exists here)"]
    RedisWrite2 -.no known consumer.-> Void
```
