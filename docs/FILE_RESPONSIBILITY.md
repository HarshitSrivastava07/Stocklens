# StockLens — File Responsibility Reference

> Scope: every file in the repository that is not third-party (`node_modules/`) or a build cache (`__pycache__/`). Status values follow the classification defined in `ARCHITECTURE.md` §20.

## 1. Repository Structure

```
stocklens/
├── apps/
│   ├── web/                          # Next.js frontend — EMPTY SCAFFOLD
│   │   ├── node_modules/             # installed deps, no package.json present
│   │   └── store/
│   │       └── market.ts             # only application source file in apps/web
│   └── worker/                       # Python real-time ingestion service — the only fully implemented backend
│       ├── Dockerfile
│       ├── requirements.txt
│       ├── upstox_ws.py
│       ├── token_manager.py
│       ├── candle_builder.py
│       ├── yahoo_price_fetcher.py
│       ├── test_ws.py
│       └── __pycache__/              # build artifact, ignorable
├── scratch/                          # ad hoc, disconnected experiments — NOT part of the running system
│   ├── test_gemini.py
│   ├── test_all_models.py
│   ├── list_models.py
│   └── test_yahoo.py
├── scripts/                          # standalone, manually-run ETL/seed CLIs
│   ├── import_nse_symbols.py
│   ├── download_bhavcopy.py
│   ├── fetch_bse_data.py
│   ├── parse_financials.py
│   ├── seed_sectors.py
│   ├── seed_global_stocks.py
│   ├── seed_dev_data.py
│   └── seed_all_stocks_dev.py
└── supabase/
    └── migrations/
        ├── 001_initial.sql
        ├── 002_global_stocks.sql
        └── 002_pgvector_fo_peers.sql
```

No root-level files exist: no `README`, `package.json`, `docker-compose.yml`, `.env`/`.env.example`, `.gitignore`, CI config, or license file were found at the repository root. **VERIFIED FROM CODE** (directory listing).

## 2. Directory Responsibilities

| Directory | Responsibility |
|---|---|
| `apps/web/` | Intended Next.js/React frontend. Currently only holds installed `node_modules` and one Zustand store definition — no runnable application. |
| `apps/worker/` | The real-time market-data ingestion service: Upstox WebSocket client, Yahoo Finance poller, 1-minute candle builder, Upstox token management. The only backend component with working, runnable code. |
| `scripts/` | Independent command-line scripts for populating the database: stock universe, sector taxonomy, fundamentals (BSE/Screener), daily price history (bhavcopy), and mock/dev data. Each is self-contained and run manually. |
| `scratch/` | Developer scratch/debug scripts for experimenting with Gemini and Yahoo Finance APIs. Not imported by, or wired into, any other part of the system. Two of the four depend on a nonexistent `apps/api` package. |
| `supabase/migrations/` | Hand-written SQL schema migrations defining the entire intended data model (far broader than what the current code populates). |

## 3. Complete File Responsibility Table

| File | Type | Responsibility | Important Components | Used By | Uses | Status |
|---|---|---|---|---|---|---|
| `apps/worker/upstox_ws.py` | Source (entry point) | Real-time NSE tick ingestion via Upstox WebSocket | `UpstoxWorker`, `main()` | Run directly / Docker `CMD` | `candle_builder`, `token_manager`, `yahoo_price_fetcher` (optional), `upstox_client`, `redis.asyncio` | 🟢 Active |
| `apps/worker/token_manager.py` | Source | Resolves/refreshes Upstox OAuth access token | `ensure_valid_token()`, `_refresh_token()` | `upstox_ws.py` | `httpx`, local `upstox_token.json` (runtime-created) | 🟢 Active |
| `apps/worker/candle_builder.py` | Source | Aggregates ticks into 1-minute OHLCV candles, persists to Postgres | `CandleBuilder` | `upstox_ws.py` | `asyncpg` | 🟢 Active |
| `apps/worker/yahoo_price_fetcher.py` | Source (also standalone entry point) | Polls Yahoo Finance for global stock prices, writes Redis + `realtime_quotes` | `YahooPriceFetcher`, `create_db_pool()`, `main()` | `upstox_ws.py` (imported), or run standalone | `httpx`, `asyncpg`, `redis.asyncio` | 🟢 Active |
| `apps/worker/test_ws.py` | Debug script (not a test) | Manual probe of the Upstox v3 authorize+WebSocket handshake | none (script body) | Run manually by a developer | `requests`, `websocket` | 🟠 Placeholder / dev-only — hardcodes a machine-specific absolute path (`c:\New folder (2)\stocklens\.env`) |
| `apps/worker/requirements.txt` | Config | Pins worker Python dependencies | — | `pip install -r` | — | 🟢 Active (but includes `fastapi`/`uvicorn`/`structlog` that are unused by any code in the repo) |
| `apps/worker/Dockerfile` | Build/deploy | Builds the worker container image | — | `docker build` | `requirements.txt`, `upstox_ws.py` (as `CMD` target) | 🟢 Active |
| `apps/web/store/market.ts` | Source | Zustand store shape for real-time quotes, market status, WS connection flag | `useMarketStore`, `Quote`, `MarketStatus` | Would be used by frontend components — **none exist yet** | `zustand` | 🟠 Placeholder — defines state shape with no consumer |
| `scripts/import_nse_symbols.py` | Script | Downloads/parses NSE equity list, upserts `stocks` | `fetch_nse_equity_list()`, `parse_equity_row()`, `import_to_db()` | Run manually | `httpx`, `asyncpg`, `csv` | 🟢 Active |
| `scripts/download_bhavcopy.py` | Script | Downloads NSE daily bhavcopy, stores staging + daily candle rows | `download_bhavcopy()`, `store_bhavcopy()`, `run()` | Run manually (or intended scheduler — absent) | `httpx`, `asyncpg`, `zipfile` | 🟢 Active |
| `scripts/fetch_bse_data.py` | Script | Fetches/parses BSE quarterly result XML into `financial_results` | `fetch_bse_results_xml()`, `parse_bse_xml()`, `run_batch()` | Run manually (or intended scheduler — absent) | `httpx`, `asyncpg`, `xml.etree.ElementTree` | 🟢 Active |
| `scripts/parse_financials.py` | Script | Parses Screener.in Excel exports into `financial_results` with validation | `parse_screener_excel()`, `validate_row()`, `write_to_db()` | Run manually | `pandas`, `asyncpg` | 🟢 Active (requires `pandas`, not in any requirements file) |
| `scripts/seed_sectors.py` | Script | Seeds `sector_classification`, tags `stocks.sector_id` | `seed_sectors()`, `STATIC_OVERRIDES` map | Run manually | `httpx` (index lookup, unused path), `asyncpg` | 🟢 Active |
| `scripts/seed_global_stocks.py` | Script | Seeds ~1,750 global-index stocks with `yahoo_ticker`s | embedded symbol tables (`US_SP500`, etc.), `main()`/import routine | Run manually | `asyncpg` | 🟢 Active |
| `scripts/seed_dev_data.py` | Script | Seeds mock dev data if DB is empty | `SAMPLE_STOCKS`, seeding routine | Run manually, dev only | `asyncpg` | 🟡 Dev-only |
| `scripts/seed_all_stocks_dev.py` | Script | Seeds mock financials/ratios/valuations/ML-scores/signals/price-history for all active stocks | `run()`, `rand_price()` | Run manually, dev only | `asyncpg` | 🔴 Problem — inserts into `ml_cluster_results`, a table not defined by any migration (schema defines `cluster_results`) |
| `scratch/test_gemini.py` | Scratch script | Ad hoc single-prompt Gemini API smoke test | — | Run manually by a developer | `google.genai`, `apps/api/config.py` (**missing**) | 🔴 Broken as committed — `ModuleNotFoundError` |
| `scratch/test_all_models.py` | Scratch script | Probes multiple Gemini model names for availability | — | Run manually | `google.genai`, `apps/api/config.py` (**missing**) | 🔴 Broken as committed |
| `scratch/list_models.py` | Scratch script | Lists available Gemini models for the configured API key | — | Run manually | `google.genai`, `apps/api/config.py` (**missing**) | 🔴 Broken as committed |
| `scratch/test_yahoo.py` | Scratch script | Compares three approaches to fetching a Yahoo Finance quote (v8 chart, `yfinance` w/ session, v10 quoteSummary) | — | Run manually | `requests`, `yfinance` | 🟢 Runnable standalone (no missing-module dependency) |
| `supabase/migrations/001_initial.sql` | Schema | Defines the full StockLens data model (12 sections: stock universe, prices, fundamentals, ratios, valuation engine, ML outputs, signals, personal features, AI, admin/ops, backtesting, seed data) | ~30 tables, indexes, seed `INSERT`s | Applied via `psql` manually | pgvector/pg_trgm/btree_gin/uuid-ossp extensions | 🟢 Active as schema definition; most tables 🔵 Unused by current code |
| `supabase/migrations/002_global_stocks.sql` | Schema | Adds `exchange`/`currency`/`yahoo_ticker`/`country` to `stocks`, drops the old unique ISIN constraint | `ALTER TABLE stocks ...` | Applied via `psql` manually, after `001_initial.sql` | — | 🟢 Active |
| `supabase/migrations/002_pgvector_fo_peers.sql` | Schema | Adds `stock_embeddings`, `nse_bhavcopy_staging`, `fo_oi_history`, `peer_groups`; extends `admin_overrides` | `CREATE TABLE`/`ALTER TABLE` | Applied via `psql` manually, after `001_initial.sql` | pgvector extension | 🟢 Active — **note**: shares the `002_` prefix with `002_global_stocks.sql` (see ARCHITECTURE.md §10) |

## 4. Detailed File Documentation

### `apps/worker/upstox_ws.py`

**Purpose:** Connects to Upstox's real-time market-data WebSocket for NSE stocks, validates and normalizes each tick, writes it to Redis, forwards it to the candle builder, and (optionally) runs the Yahoo global-stock poller concurrently.

**Why it exists:** It is the system's only real-time data ingestion path — the `Dockerfile`'s `CMD` runs this file directly, making it the worker service's entry point.

**Contains:**
- `load_subscribed_symbols()` — reads `subscribed_symbols.json` (not present in the repo; created/provided at deploy time) or falls back to 5 hardcoded Nifty symbols.
- Class `UpstoxWorker` — `_validate_tick`, `_normalize_tick`, `_process_tick`, `run` (reconnect loop), `_connect_and_stream` (contains the SDK monkey-patch), `stop`.
- `main()` — process entry point, signal handling, optional Yahoo-fetcher concurrency.

**Called/imported by:** Not imported elsewhere; it is a process entry point (`python upstox_ws.py`, or the Docker `CMD`).

**Calls/imports:** `candle_builder.CandleBuilder`, `token_manager.ensure_valid_token`, `upstox_client` (and its `feeder.market_data_feeder` module, monkey-patched at runtime), `redis.asyncio`, `yahoo_price_fetcher` (conditional import inside `main()`).

**Execution role:** Long-running process; runs for the lifetime of the container/host.

**Inputs:** Upstox WebSocket messages; `.env` configuration; `subscribed_symbols.json` (optional).

**Outputs:** Redis keys/pub-sub messages; delegated Postgres writes via `CandleBuilder`.

**Failure behavior:** Per-tick errors are caught and logged (tick dropped). Connection failures trigger exponential-backoff reconnect. A missing/invalid token raises `RuntimeError`, which is also caught by the outer retry loop (indefinite retry with backoff, not a fast exit).

**Modification impact:** Central to real-time data availability — any change to `_normalize_tick()`'s output shape must stay in sync with `candle_builder.py`'s expectations (`ltp`, `volume` keys) and with the Redis JSON shape any future frontend/API would need to consume (currently no consumer exists to break, per §20 of ARCHITECTURE.md, but `apps/web/store/market.ts`'s `Quote` interface documents the intended shape).

**Status:** 🟢 Active.

---

### `apps/worker/token_manager.py`

**Purpose:** Provides a single valid Upstox access token to the worker, trying a local file cache, then an environment variable, then an OAuth refresh call, in that order.

**Why it exists:** Upstox access tokens expire daily at midnight IST; this module centralizes the refresh/fallback logic so `upstox_ws.py` doesn't need to know the details.

**Contains:** `_load_token_file`, `_save_token_file`, `_is_token_valid`, `ensure_valid_token` (public API), `_refresh_token`.

**Called/imported by:** `apps/worker/upstox_ws.py` (`ensure_valid_token`).

**Calls/imports:** `httpx` (for the refresh POST to `https://api.upstox.com/v2/login/authorization/token`); reads/writes `apps/worker/upstox_token.json` (not present in this checkout — documented in-code as gitignored, though no `.gitignore` exists in the repo to enforce that).

**Execution role:** Called once per reconnect attempt, before opening a WebSocket connection.

**Inputs:** `UPSTOX_ACCESS_TOKEN`, `UPSTOX_REFRESH_TOKEN`, `UPSTOX_API_KEY`, `UPSTOX_API_SECRET`, `UPSTOX_REDIRECT_URI` env vars; the token file on disk.

**Outputs:** A token string, or `None`.

**Failure behavior:** All exceptions during refresh are caught and logged; on total failure, returns `None` with a log message telling the operator to visit `http://localhost:8000/api/v1/auth/upstox` — an endpoint that would need to live on the (nonexistent) `apps/api` service.

**Modification impact:** Any change to the token file schema (`access_token`/`refresh_token`/`expires_at` keys) must stay consistent between `_load_token_file`/`_save_token_file`/`_is_token_valid`.

**Status:** 🟢 Active. The referenced OAuth authorize endpoint (`/api/v1/auth/upstox`) is 🔴 Problem/absent.

---

### `apps/worker/candle_builder.py`

**Purpose:** Stateful in-memory aggregation of ticks into 1-minute OHLCV candles, flushed to `price_candles_1m` when a minute boundary rolls over.

**Why it exists:** Real-time ticks are too granular/high-volume to store individually; this reduces them to a standard OHLCV time series matching the schema's partitioned `price_candles_1m` table.

**Contains:** Module-level `_candles` dict and `_candle_lock`; class `CandleBuilder` with `_get_pool`, `_candle_minute_key`, `on_tick`, `_write_candle`.

**Called/imported by:** `apps/worker/upstox_ws.py` (`UpstoxWorker.__init__` constructs one instance).

**Calls/imports:** `asyncpg` (lazily-created pool from `DATABASE_URL`).

**Execution role:** Invoked per-tick (`on_tick`), request-time-equivalent for a streaming system; DB writes happen only on minute-boundary crossings, not per tick.

**Inputs:** `symbol`, `tick` dict (needs `ltp`, `volume`).

**Outputs:** Rows in `price_candles_1m`.

**Failure behavior:** DB write errors are caught and logged inside `_write_candle`; the closed candle's data is not retried or buffered elsewhere — it is lost on failure.

**Modification impact:** Because `_candles` is a **module-level** dict (not instance-level), running multiple `CandleBuilder` instances in the same process share the same underlying candle state — currently harmless since `upstox_ws.py` constructs exactly one instance, but a latent trap for future multi-instance use. Not safe across multiple OS processes/replicas (no shared/external state).

**Status:** 🟢 Active.

---

### `apps/worker/yahoo_price_fetcher.py`

**Purpose:** Polls Yahoo Finance every 60 seconds for all stocks that have a `yahoo_ticker`, updating Redis and the `realtime_quotes` table — the price source for non-NSE (global) stocks, since Upstox only covers NSE.

**Why it exists:** Upstox's real-time feed only covers Indian exchanges; this module fills in delayed (~15 min) global prices for the international-stock universe seeded by `scripts/seed_global_stocks.py`.

**Contains:** Class `YahooPriceFetcher` (`load_symbols`, `_make_batches` [defined but not actually used by `run_once`'s per-symbol-with-semaphore approach], `_get_crumb`, `_fetch_single`, `run_once`, `run`, `stop`); module-level `create_db_pool()`; `main()` for standalone execution.

**Called/imported by:** `apps/worker/upstox_ws.py` (conditionally, inside `main()`); or run directly as its own process.

**Calls/imports:** `httpx`, `asyncpg`, `redis.asyncio`; imports `yfinance` at module level but the actual fetch path (`_fetch_single`) uses raw `httpx` calls against Yahoo's `v8/finance/chart` endpoint, not the `yfinance` library's own network calls.

**Execution role:** Long-running loop (`run()`) with a fixed 60s cadence, or single-shot (`run_once()`).

**Inputs:** `stocks` rows with non-null `yahoo_ticker`; Yahoo Finance HTTP responses.

**Outputs:** Redis `latest:{symbol}` keys (120s TTL) and `ticks:{symbol}`/`ticks:dashboard` pub/sub messages; `realtime_quotes` upserts.

**Failure behavior:** A failed crumb fetch skips the entire poll cycle; a failed per-symbol fetch (including one retry on HTTP 429) returns `None` and that symbol is silently skipped for the cycle; the outer `run()` loop catches all other exceptions per-cycle and continues on schedule.

**Modification impact:** Depends on Yahoo's undocumented `v8/finance/chart` API and a scraped session-cookie/crumb flow — a routine target for breakage if Yahoo changes its anti-scraping measures; no fallback data source is implemented.

**Status:** 🟢 Active (code-complete; fragile external dependency).

---

### `apps/worker/test_ws.py`

**Purpose:** One-off manual script to test whether the Upstox v3 market-data-feed authorize call and the resulting WebSocket URL work, with and without an `Authorization` header.

**Why it exists:** Debug aid used while diagnosing the Upstox SDK deprecation that led to the `upstox_ws.py` monkey-patch (see that file's comment block).

**Contains:** Script-level procedural code; no functions/classes.

**Called/imported by:** Nobody — run manually by a developer.

**Calls/imports:** `requests`, `websocket`, `python-dotenv`.

**Execution role:** Ad hoc, developer-invoked only.

**Inputs:** `UPSTOX_ACCESS_TOKEN` from a **hardcoded** path `c:\New folder (2)\stocklens\.env` — not the repo-relative path pattern used elsewhere, and not matching this checkout's actual root (`d:\New folder (2)\stocklens`).

**Outputs:** stdout prints only; no persisted state.

**Failure behavior:** Uncaught exceptions will simply crash the script with a traceback; this is expected for a debug tool.

**Modification impact:** None — nothing depends on this file.

**Status:** 🟠 Placeholder / dev-only, and currently broken on this machine due to the hardcoded path.

---

### `apps/worker/requirements.txt`

**Purpose:** Pins exact versions of the worker's Python dependencies.

**Contains:** `fastapi`, `uvicorn[standard]`, `asyncpg`, `redis`, `hiredis`, `python-dotenv`, `httpx`, `upstox-python-sdk`, `structlog`, `pytz`, `yfinance`.

**Called/imported by:** `pip install -r apps/worker/requirements.txt`; also the de facto dependency source for `scripts/*.py`, which are not covered by a separate manifest.

**Modification impact:** `fastapi`, `uvicorn`, and `structlog` are declared but **unused** by any code found in this repository — evidence that an API server and structured logging were planned but not (yet, or any longer) implemented here. `pandas` (used by `scripts/parse_financials.py`) is conspicuously **absent** from this file.

**Status:** 🟢 Active, but with unused entries and one missing entry relative to `scripts/`.

---

### `apps/worker/Dockerfile`

**Purpose:** Builds a container image for the worker service.

**Contains:** `python:3.12-slim` base, native build deps (`gcc`, `g++`, `libpq-dev`), `pip install -r requirements.txt`, copies the worker source, creates and drops privileges to `workeruser`, `CMD ["python", "upstox_ws.py"]`.

**Modification impact:** Only builds/runs `upstox_ws.py` — the Yahoo poller only runs if launched from within that same process (via the `asyncio.gather` path in `main()`); there is no separate Dockerfile/CMD to run `yahoo_price_fetcher.py` standalone in a container.

**Status:** 🟢 Active. No `docker-compose.yml` exists to wire it to Postgres/Redis.

---

### `apps/web/store/market.ts`

**Purpose:** Defines a Zustand store (`useMarketStore`) holding real-time quotes keyed by NSE symbol, overall market status, and WebSocket connection state, intended to be updated by a (not-yet-implemented) frontend WebSocket client.

**Why it exists:** The comment header states it is "Updated by WebSocket connection" — this is the client-side data model that the (absent) frontend would use to render live prices.

**Contains:** `Quote` interface, `MarketStatus` interface, `MarketStore` interface, `useMarketStore` (the `create()` call from `zustand`).

**Called/imported by:** No other file in the repository imports this module — there are no React components to consume it.

**Calls/imports:** `zustand`.

**Execution role:** Would run in the browser at request/render time, once a frontend exists.

**Failure behavior:** N/A — pure client-side state container with no I/O of its own.

**Modification impact:** None currently (no consumers), but its field names (`nse_symbol`, `ltp`, `data_freshness`, etc.) are a useful contract reference for what a future API/WebSocket payload shape should look like, echoing the tick shape produced by `apps/worker/upstox_ws.py`'s `_normalize_tick()` and `yahoo_price_fetcher.py`'s `_fetch_single()`.

**Status:** 🟠 Placeholder.

---

### `scripts/import_nse_symbols.py`

**Purpose:** Downloads the official NSE equity list (or reads a local CSV) and upserts it into the `stocks` table, flagging Nifty-50 membership.

**Contains:** `fetch_nse_equity_list`, `parse_equity_row`, `get_or_create_sector`, `import_to_db`, `main` (argparse: `--file`, `--source`).

**Called/imported by:** Run manually.

**Calls/imports:** `httpx`, `asyncpg`, `csv`.

**Inputs:** NSE's public `EQUITY_L.csv` (network) or a local file via `--file`.

**Outputs:** Rows in `stocks`.

**Failure behavior:** Per-row DB errors are caught, logged, and counted as "skipped"; the run continues.

**Modification impact:** Downstream scripts (`seed_sectors.py`, `fetch_bse_data.py`, price/candle writers) all assume `stocks` is already populated — this script is a natural first step in any fresh-database setup.

**Status:** 🟢 Active.

---

### `scripts/download_bhavcopy.py`

**Purpose:** Downloads NSE's daily end-of-day bhavcopy (zipped CSV), parses equity-series rows, and stores them into a staging table plus (for symbols already known) `price_candles_daily` and a `realtime_quotes` close-price upsert.

**Contains:** `bhavcopy_url`, `download_bhavcopy`, `store_bhavcopy`, `run` (supports `--date` and `--backfill N`).

**Inputs:** NSE's bhavcopy ZIP URL for a given date.

**Outputs:** `nse_bhavcopy_staging`, `price_candles_daily`, `realtime_quotes` rows.

**Failure behavior:** A 404 (market holiday) is treated as expected and logged as a warning, not an error; per-row insert failures are caught and logged at DEBUG, loop continues; a 2-second polite delay is inserted between backfill days.

**Modification impact:** Comments state this is intended to run "via scheduler at 16:30 IST" — no such scheduler exists in this repo (see ARCHITECTURE.md §16/EXECUTION.md §13); must currently be triggered manually or via an external cron the repo doesn't define.

**Status:** 🟢 Active.

---

### `scripts/fetch_bse_data.py`

**Purpose:** Fetches quarterly/annual financial result XML from BSE India's public API for stocks with a known `bse_code`, parses it, and upserts `financial_results`.

**Contains:** `get_bse_code`, `fetch_bse_results_xml`, `parse_bse_xml`, `_parse_result_item`, `upsert_results`, `process_symbol`, `run_batch` (argparse: `--symbol`, `--limit`).

**Inputs:** `stocks.bse_code` (must be pre-populated — no script in this repo is observed writing `bse_code`, so its origin is **UNKNOWN / NOT VERIFIED** from this codebase alone); BSE's XML API.

**Outputs:** `financial_results` rows with `data_source='BSE_XML'`, `confidence_level='MEDIUM'`.

**Failure behavior:** Missing BSE code → "skipped"; XML fetch failure → "failed"; empty parse → "empty"; all tracked per-symbol and summarized at the end. A 1-second polite delay between symbols.

**Modification impact:** Also references an intended `scheduler_service.py` (absent) for a 17:00 IST daily run.

**Status:** 🟢 Active. Depends on `stocks.bse_code` being populated by an out-of-repo process.

---

### `scripts/parse_financials.py`

**Purpose:** Parses a Screener.in Excel export (P&L / Balance Sheet / Cash Flow sheets) into `financial_results` rows, with basic sanity-check validation, and writes them via upsert.

**Contains:** `_safe_num`, `_parse_screener_year`, `validate_row`, `parse_screener_excel`, `write_to_db`, `main` (argparse: `--file`, `--symbol`, `--auto`).

**Inputs:** A local `.xlsx`/`.xls`/`.csv` file matching Screener's export layout.

**Outputs:** `financial_results` rows with `data_source='SCREENER_EXPORT'`.

**Failure behavior:** Validation issues are logged as warnings but do **not** block insertion (data is still written even if e.g. EPS×Shares doesn't reconcile with PAT within 50%); DB write failures per-row are caught, logged, counted as "skipped".

**Modification impact:** Requires `pandas` (and an Excel engine, e.g. `openpyxl`, for `.xlsx`), neither of which is declared in `apps/worker/requirements.txt` — installing this script's dependencies is a separate, undocumented step.

**Status:** 🟢 Active, with an undeclared dependency gap.

---

### `scripts/seed_sectors.py`

**Purpose:** Seeds `sector_classification` rows and assigns `stocks.sector_id` using a large static Python dict (`STATIC_OVERRIDES`) mapping ~80 well-known NSE symbols to macro-sector/sector/industry, plus (unused in the default `run()` path) a helper to fetch live Nifty sectoral index constituents from NSE.

**Contains:** `MACRO_GROUPS`, `get_macro_sector`, `NIFTY_INDICES`, `fetch_index_constituents` (defined but not called from `run()`), `STATIC_OVERRIDES`, `seed_sectors`, `run`.

**Inputs:** The hardcoded `STATIC_OVERRIDES` table (no network call in the actual `run()` path).

**Outputs:** `sector_classification` rows; `stocks.sector_id` updates.

**Failure behavior:** No explicit per-row try/except in `seed_sectors()` — a DB error here would propagate and abort the script (unlike most other scripts in `scripts/`, which are more defensive).

**Modification impact:** Only covers the ~80 symbols in `STATIC_OVERRIDES`; every other stock in the database is left with `sector_id = NULL` unless a future run extends the map or wires up `fetch_index_constituents`.

**Status:** 🟢 Active; `fetch_index_constituents` is 🔵 Unused (defined, never called).

---

### `scripts/seed_global_stocks.py`

**Purpose:** Seeds roughly 1,750 stocks across major global indices (S&P 500, NASDAQ 100, FTSE 100, Nikkei 225, Hang Seng 50, DAX 40, CAC 40, ASX 200) using embedded Python literal tables, each row carrying a `yahoo_ticker` for the Yahoo poller to consume.

**Contains:** Large embedded lists (`US_SP500`, and similarly-named tables per market, per the file's docstring — only the first ~120 lines were reviewed in full; the remainder follows the same tuple-literal pattern per file structure and docstring), `--exchange`/`--force` argparse options.

**Inputs:** None external — data is embedded in the source file itself (a deliberate design choice per the docstring, "no external API call required for seeding").

**Outputs:** `stocks` rows with `exchange`, `currency`, `country`, `yahoo_ticker` populated (columns added by `002_global_stocks.sql`).

**Modification impact:** This is the primary/only source of `yahoo_ticker` values feeding `yahoo_price_fetcher.py` — without running this script, the Yahoo poller has no symbols to poll (its `load_symbols()` query filters on `yahoo_ticker IS NOT NULL`).

**Status:** 🟢 Active.

---

### `scripts/seed_dev_data.py`

**Purpose:** Seeds a realistic but synthetic Nifty-50-subset dataset for local development, intended to only insert when the DB is empty.

**Contains:** `SAMPLE_STOCKS` and a seeding routine (per docstring; full body not exhaustively re-transcribed here beyond the header/sample data reviewed).

**Modification impact:** Dev-only; not part of any production data path.

**Status:** 🟡 Dev-only / test data generator.

---

### `scripts/seed_all_stocks_dev.py`

**Purpose:** For every active stock currently in the database, generates synthetic financial results, financial ratios, intrinsic values, ML scores, a cluster-label row, a signal, and 90 days of daily price candles, using the stock's real current LTP (from `realtime_quotes`) as a baseline.

**Contains:** `rand_price`, `run` (the whole seeding routine), constant lists `SIGNALS`, `SIGNAL_LABELS`, `CLUSTER_LABELS`.

**Inputs:** Existing `stocks`/`realtime_quotes` rows.

**Outputs:** Rows in `financial_results`, `financial_ratios`, `intrinsic_values`, `ml_scores`, **`ml_cluster_results`** (see below), `signals`, `price_candles_daily`.

**Failure behavior:** Price-candle inserts are wrapped in a bare `try/except: pass` (line ~210) — any failure there is silently swallowed with no logging at all, unlike the rest of the codebase's convention of at least logging caught exceptions.

**Modification impact / PROBLEM:** The `INSERT INTO ml_cluster_results (...)` statement (~line 157) targets a table name that **does not exist** anywhere in `supabase/migrations/`; the schema defines `cluster_results` (singular concept, no `ml_` prefix) with a different column set (no `run_date`/`algorithm` columns). Running this script against the schema as migrated will raise a Postgres `UndefinedTable` error at that point in the per-stock loop — meaning it also **partially completes** for each stock (financial_results/ratios/intrinsic_values succeed, `ml_cluster_results` fails, and the exception is not caught by any surrounding try/except at that point in the loop, so the whole script would likely abort on the first stock processed). **NOT VERIFIED AT RUNTIME** — this conclusion is from comparing the script's SQL text to the migration files, not from executing it.

**Status:** 🔴 Problem.

---

### `scratch/test_gemini.py`, `scratch/test_all_models.py`, `scratch/list_models.py`

**Purpose:** Ad hoc smoke tests against the Google Gemini API (single generation call, multi-model probing, and model listing, respectively).

**Why they exist:** Developer experimentation while evaluating Gemini models for the (unbuilt) AI-report feature described in the `ai_reports` table.

**Contains:** Script-level procedural code only.

**Called/imported by:** Nobody; run manually.

**Calls/imports:** `google.genai`; all three insert `apps/api` onto `sys.path` and do `from config import settings` to obtain `GEMINI_API_KEY`/`GEMINI_MODEL_FAST`.

**Failure behavior:** All three will raise `ModuleNotFoundError: No module named 'config'` immediately, because `apps/api` does not exist in this repository.

**Modification impact:** None on the running system (nothing depends on these); they do serve as evidence of what config surface (`settings.GEMINI_API_KEY`, `settings.GEMINI_MODEL_FAST`) an eventual `apps/api/config.py` was expected to expose.

**Status:** 🔴 Broken as committed.

---

### `scratch/test_yahoo.py`

**Purpose:** Compares three different techniques for pulling a Yahoo Finance quote (raw v8 chart API, `yfinance.download` with a custom `requests` session, and the v10 `quoteSummary` API) — evaluation work that appears to have informed the final approach used in `apps/worker/yahoo_price_fetcher.py` (which settled on the raw v8 chart API + crumb flow).

**Contains:** Script-level procedural code, three sequential test blocks.

**Called/imported by:** Nobody; run manually.

**Calls/imports:** `requests`, `yfinance`, `json`.

**Failure behavior:** Each test block is independently wrapped in `try/except`, printing status/errors; failure in one block doesn't stop the others.

**Modification impact:** None; purely exploratory.

**Status:** 🟢 Runnable standalone / 🔵 not part of the running system.

---

### `supabase/migrations/001_initial.sql`

**Purpose:** Defines the entire baseline schema — stock universe, real-time/historical price tables (including a monthly-range-partitioned `price_candles_1m`), fundamentals, computed ratios, a DCF/relative valuation engine's tables, ML output tables, a rule-based signal table, personal watchlist/alerts/portfolio tables, AI-report and pgvector news-embedding tables, and admin/audit/backtesting tables — plus seed data for `data_sources` and `sector_classification`.

**Why it exists:** The single source of truth for the intended data model; no ORM model classes exist anywhere in the repo, so this file **is** the schema documentation.

**Contains:** ~30 `CREATE TABLE` statements across 12 commented sections, associated indexes, one seed-data section (`INSERT INTO data_sources`, `INSERT INTO sector_classification`), and a closing `DO $$ ... RAISE NOTICE ... END $$` block that prints a confirmation listing every created table when run interactively.

**Called/imported by:** Applied manually via `psql $DATABASE_URL -f supabase/migrations/001_initial.sql`, per its own header comment. Not invoked by any application code.

**Modification impact:** Every script in `scripts/` and every worker module assumes this schema is already applied — none of them run `CREATE TABLE IF NOT EXISTS` themselves (correctly delegating schema ownership to the migrations directory).

**Status:** 🟢 Active as the schema definition. The large majority of its tables have 🔵 no writer in the current codebase outside of the dev-mock seeders.

---

### `supabase/migrations/002_global_stocks.sql`

**Purpose:** Extends `stocks` for multi-exchange/global support: drops the old single-market unique ISIN constraint, adds `exchange`, `currency`, `yahoo_ticker`, `country` columns with sane NSE/India defaults, adds supporting indexes, and back-fills existing rows.

**Called/imported by:** Applied manually after `001_initial.sql`.

**Modification impact:** Required before running `scripts/seed_global_stocks.py` (which writes to these columns) or `yahoo_price_fetcher.py` (which reads `yahoo_ticker`/`currency`/`exchange`/`country`).

**Status:** 🟢 Active.

---

### `supabase/migrations/002_pgvector_fo_peers.sql`

**Purpose:** Adds `stock_embeddings` (a second pgvector embeddings table, HNSW-indexed, alongside `001_initial.sql`'s IVFFlat-indexed `news_embeddings`), `nse_bhavcopy_staging` (used by `download_bhavcopy.py`), `fo_oi_history` (futures & options open interest — no writer found in this repo), and `peer_groups` (no writer found); also extends `admin_overrides` with two new columns.

**Called/imported by:** Applied manually after `001_initial.sql`; independent of `002_global_stocks.sql` (no ordering dependency between the two `002_` files, but see the duplicate-numbering caveat in ARCHITECTURE.md §10).

**Modification impact:** `nse_bhavcopy_staging`, created here, is a hard dependency for `scripts/download_bhavcopy.py` — that script will fail without this migration applied.

**Status:** 🟢 Active for the tables with writers (`nse_bhavcopy_staging`, `stock_embeddings` as a target schema); 🔵 Unused for `fo_oi_history`/`peer_groups` (no writer in this codebase).

## 5. File Dependency Graph

```
apps/worker/upstox_ws.py
 ├── apps/worker/token_manager.py
 ├── apps/worker/candle_builder.py
 └── apps/worker/yahoo_price_fetcher.py   (conditional import)

apps/worker/yahoo_price_fetcher.py         (also standalone entry point)

apps/worker/test_ws.py                     (standalone, no internal deps)

scripts/*.py                               (each standalone; no cross-script imports)

apps/web/store/market.ts                   (standalone; zero internal or external-file consumers in this repo)

scratch/test_gemini.py, test_all_models.py, list_models.py
 └── apps/api/config.py  ← MISSING, breaks these three scripts

scratch/test_yahoo.py                      (standalone, no internal deps)

supabase/migrations/001_initial.sql        (base schema)
 ├── supabase/migrations/002_global_stocks.sql        (depends on 001)
 └── supabase/migrations/002_pgvector_fo_peers.sql     (depends on 001)
```

**Central files:** `apps/worker/upstox_ws.py` is the single highest-fan-out file (imports/coordinates three other worker modules). **Isolated files:** `apps/web/store/market.ts` and all of `scripts/` and `scratch/` have no incoming dependencies from other repository files. **No circular dependencies** were found. **Single point of failure:** `apps/worker/upstox_ws.py` is the only process that ties the real-time pipeline together — if it fails to start, no NSE ticks and (in the common deployment, per `main()`) no Yahoo polling occur either, since the Yahoo fetcher is launched from inside it unless run standalone.

## 6. File Execution Order

For the worker process (the only automatically-orchestrated execution path in this repo):

1. `apps/worker/upstox_ws.py` (import-time: loads `.env`, reads required env vars — fails here if missing)
2. `apps/worker/candle_builder.py`, `apps/worker/token_manager.py` (imported, class/function definitions only — no side effects at import time)
3. `apps/worker/upstox_ws.py:main()` (startup execution): creates Redis client, `UpstoxWorker`
4. `apps/worker/yahoo_price_fetcher.py` (conditionally imported inside `main()`; its own module-level `.env` load and `REDIS_URL`/`DATABASE_SYNC_URL` reads happen at this import point)
5. `apps/worker/token_manager.py:ensure_valid_token()` (request-time-equivalent, called on every reconnect attempt)
6. `apps/worker/upstox_ws.py:UpstoxWorker._process_tick()` → `apps/worker/candle_builder.py:CandleBuilder.on_tick()` (per-tick, background/request-time-equivalent execution, indefinitely, for the life of the process)
7. `apps/worker/yahoo_price_fetcher.py:YahooPriceFetcher.run_once()` (background execution, every 60 seconds, for the life of the process, concurrently with steps 5-6)
8. On shutdown signal (POSIX only): `handle_shutdown()` → `worker.stop()` / `loop.stop()`

`scripts/*.py` have no defined relative order — each is run independently by a human, though a **sensible** order (INFERRED, not enforced by any code) is: `import_nse_symbols.py` → `seed_sectors.py` and/or `seed_global_stocks.py` → `download_bhavcopy.py` / `fetch_bse_data.py` / `parse_financials.py` → (dev only) `seed_dev_data.py` / `seed_all_stocks_dev.py`.

## 7. File → Function → Function Chains

**Real-time tick path:**
```
upstox_ws.py
└── UpstoxWorker._connect_and_stream()
    └── on_message() [SDK thread callback]
        └── asyncio.run_coroutine_threadsafe(UpstoxWorker._process_tick, loop)
            └── _process_tick()
                ├── _validate_tick()
                ├── _normalize_tick()
                ├── redis.setex() / redis.publish()
                └── candle_builder.py :: CandleBuilder.on_tick()
                    └── (on minute rollover) _write_candle()
                        └── asyncpg INSERT INTO price_candles_1m
```

**Yahoo poll path:**
```
yahoo_price_fetcher.py
└── YahooPriceFetcher.run()
    └── run_once()
        ├── load_symbols()          → SELECT stocks
        ├── _get_crumb()            → GET finance.yahoo.com, query1.../getcrumb
        ├── _fetch_single() × N     → GET query1.../v8/finance/chart/{ticker}
        ├── redis.setex/.publish per symbol
        └── db.executemany()        → UPSERT realtime_quotes
```

**Financials import path (Screener):**
```
parse_financials.py :: main()
└── parse_screener_excel()
    ├── _parse_screener_year()  (per column)
    ├── _safe_num()             (per cell)
    └── validate_row()          (per period)
└── write_to_db()
    └── asyncpg INSERT/UPDATE financial_results
```

## 8. Production File → Test File Mapping

**None exists.** No file in this repository follows a `test_*`/`*_test.py` pytest-discoverable naming convention pointed at a specific production module, except `apps/worker/test_ws.py`, which is a manual debug script (no assertions) rather than an automated test of `upstox_ws.py`. No production file in `apps/worker/` or `scripts/` has an associated automated test.

| Production Component | Test File | Tests | Coverage Status |
|---|---|---|---|
| `apps/worker/upstox_ws.py` | `apps/worker/test_ws.py` (partial, manual only) | Manually probes the authorize+connect handshake this file also implements | ⚪ Not automated — no assertions, no CI |
| `apps/worker/token_manager.py` | none | — | ⚪ Untested |
| `apps/worker/candle_builder.py` | none | — | ⚪ Untested |
| `apps/worker/yahoo_price_fetcher.py` | `scratch/test_yahoo.py` (exploratory only, predates/informs the module, does not test it directly) | — | ⚪ Untested |
| `scripts/*.py` (all) | none | — | ⚪ Untested |

## 9. "Where Do I Change This?"

| Requirement | File/Folder | Function/Class | Reason |
|---|---|---|---|
| Change which NSE symbols are streamed | `apps/worker/upstox_ws.py` or `apps/worker/subscribed_symbols.json` (create it) | `load_subscribed_symbols()` | Controls the WS subscription list |
| Change tick validation/normalization rules | `apps/worker/upstox_ws.py` | `UpstoxWorker._validate_tick`, `_normalize_tick` | Sole place ticks are shaped before storage |
| Change candle aggregation granularity/logic | `apps/worker/candle_builder.py` | `CandleBuilder` | Only aggregation logic in the repo (1-minute only; no 5m/1h/daily aggregator exists) |
| Change Upstox token refresh behavior | `apps/worker/token_manager.py` | `ensure_valid_token`, `_refresh_token` | Centralized token logic |
| Change global-stock polling interval/batch size | `apps/worker/yahoo_price_fetcher.py` | module constants `POLL_INTERVAL`, `BATCH_SIZE`, `MAX_WORKERS` | Only Yahoo polling config surface |
| Add/change which global stocks are tracked | `scripts/seed_global_stocks.py` | embedded symbol tables (`US_SP500`, etc.) | Sole source of `yahoo_ticker` values |
| Change NSE symbol import source/logic | `scripts/import_nse_symbols.py` | `fetch_nse_equity_list`, `parse_equity_row` | Sole `stocks` table populator from NSE |
| Change sector classification mapping | `scripts/seed_sectors.py` | `STATIC_OVERRIDES`, `MACRO_GROUPS` | Sole sector-tagging logic |
| Change how BSE fundamentals are parsed | `scripts/fetch_bse_data.py` | `parse_bse_xml`, `_parse_result_item` | Sole BSE XML parser |
| Change how Screener exports are parsed | `scripts/parse_financials.py` | `parse_screener_excel`, `SCREENER_*_COLS` maps | Sole Screener import path |
| Change the database schema | `supabase/migrations/` | new numbered `.sql` file (avoid reusing `002_`) | No migration tool exists — add a new file and apply manually |
| Add a valuation/ratio/ML/signal engine | **Does not exist yet** — would be new code, likely a new `apps/` service | n/a | Confirmed absent from repo; see ARCHITECTURE.md §20 |
| Add an API layer / auth | **Does not exist yet** — `apps/api` referenced but absent | n/a | Would need to be created from scratch |
| Build the frontend | `apps/web/` | starting point: `apps/web/store/market.ts` (existing state contract) | No `package.json`/pages exist; a Next.js app would need to be scaffolded |
| Fix the `seed_all_stocks_dev.py` schema mismatch | `scripts/seed_all_stocks_dev.py` line ~157 | the `ml_cluster_results` INSERT | Table name doesn't match `cluster_results` in `001_initial.sql` |
| Add automated tests | Any new `tests/` directory (none exists) | n/a | No test framework configured anywhere in the repo |

## 10. Files I Must Understand First

1. `supabase/migrations/001_initial.sql` — the data model everything else assumes.
2. `apps/worker/upstox_ws.py` — the only real-time process orchestrating the rest of the worker.
3. `apps/worker/candle_builder.py` and `apps/worker/yahoo_price_fetcher.py` — the two data-write paths from the worker.
4. `apps/worker/token_manager.py` — required to get the Upstox path running at all.
5. `scripts/import_nse_symbols.py` and `scripts/seed_global_stocks.py` — how the stock universe gets populated in the first place.

## 11. Files I Can Ignore Initially

- `apps/worker/__pycache__/` — build artifact.
- `apps/web/node_modules/` — third-party packages, not project code.
- `scratch/*.py` — disconnected experiments, two of the four are broken as committed.
- `apps/worker/test_ws.py` — manual debug aid, not part of the running system.

## 12. Suspicious Files

- **`scripts/seed_all_stocks_dev.py`** — inserts into a nonexistent table (`ml_cluster_results` vs. schema's `cluster_results`); likely fails at runtime (see §4 above and ARCHITECTURE.md §8).
- **`scratch/test_gemini.py`, `scratch/test_all_models.py`, `scratch/list_models.py`** — all three import a nonexistent `apps/api/config` module; broken as committed.
- **`apps/worker/test_ws.py`** — hardcodes an absolute path from a different machine (`c:\New folder (2)\stocklens\.env`); not portable.
- **`apps/web/`** — has `node_modules` populated (implying `npm install` was run against a `package.json` at some point) but no `package.json` and no application source beyond one store file; either the frontend scaffold was never finished or its source files were removed/not committed.
- **Two `002_`-prefixed migration files** (`002_global_stocks.sql`, `002_pgvector_fo_peers.sql`) — ambiguous ordering convention (see ARCHITECTURE.md §10).
- **`apps/worker/requirements.txt`** — declares `fastapi`, `uvicorn`, and `structlog`, none of which are used by any code found in the repository; conversely, `pandas` (needed by `scripts/parse_financials.py`) is not declared anywhere.

## 13. One-Line Explanation of Every Important File

```
apps/worker/upstox_ws.py          → Streams real-time NSE ticks from Upstox, writes Redis, drives candle building.
apps/worker/token_manager.py      → Resolves/refreshes the Upstox OAuth access token.
apps/worker/candle_builder.py     → Aggregates ticks into 1-minute candles and writes them to Postgres.
apps/worker/yahoo_price_fetcher.py→ Polls Yahoo Finance every 60s for global stock prices.
apps/worker/test_ws.py            → Manual debug script for the Upstox WS handshake (not an automated test).
apps/worker/requirements.txt      → Pins worker Python dependencies.
apps/worker/Dockerfile            → Builds the worker's container image; CMD runs upstox_ws.py.
apps/web/store/market.ts          → Zustand store shape for real-time quotes (no consumer exists yet).
scripts/import_nse_symbols.py     → Imports the NSE equity list into the stocks table.
scripts/download_bhavcopy.py      → Downloads NSE daily bhavcopy and stores staging + daily candles.
scripts/fetch_bse_data.py         → Fetches BSE quarterly result XML into financial_results.
scripts/parse_financials.py       → Parses Screener.in Excel exports into financial_results.
scripts/seed_sectors.py           → Seeds sector classification and tags stocks by sector.
scripts/seed_global_stocks.py     → Seeds ~1,750 global-index stocks with Yahoo tickers.
scripts/seed_dev_data.py          → Seeds mock dev data for an empty database.
scripts/seed_all_stocks_dev.py    → Seeds mock financials/ratios/valuations/ML-scores for all stocks (has a schema bug).
scratch/test_gemini.py            → Ad hoc Gemini API smoke test (broken — missing apps/api dependency).
scratch/test_all_models.py        → Probes multiple Gemini model names (broken — missing apps/api dependency).
scratch/list_models.py            → Lists available Gemini models (broken — missing apps/api dependency).
scratch/test_yahoo.py             → Compares three Yahoo Finance fetching techniques.
supabase/migrations/001_initial.sql          → Defines the full intended StockLens schema.
supabase/migrations/002_global_stocks.sql    → Adds multi-exchange/global columns to stocks.
supabase/migrations/002_pgvector_fo_peers.sql→ Adds embeddings, bhavcopy staging, F&O OI, and peer-group tables.
```
