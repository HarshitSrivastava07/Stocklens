"""
Ingestion and computation pipeline.

Moves real data from providers into the database, then runs the engines over it:

    provider  ->  price_candles_daily / financial_results / realtime_quotes
                       |
                       v
              technical_snapshots  +  intrinsic_values  ->  signals

Every stage writes an ``ingest_runs`` row recording what succeeded, what failed
and why, so a broken overnight job can be diagnosed from the database without
re-running it.

Uses asyncpg directly rather than the ORM. These are bulk upserts over tens of
thousands of rows; the ORM's per-object overhead is the wrong tool and the
worker already speaks asyncpg.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime, timezone
from typing import Any, Iterable, Sequence

import asyncpg

from .analytics.intrinsic_value import (
    MarketContext,
    compute_intrinsic_value,
    reverse_dcf,
)
from .analytics.signals import generate_signal
from .analytics.technicals import compute_snapshot, regress_beta_against
from .providers.base import Candle, FetchReport, FinancialPeriod, NotFound
from .providers.yahoo import YahooProvider

log = logging.getLogger("ingest")

# How many symbols to process concurrently. Kept modest because the bottleneck
# is the provider's rate limit, not our CPU.
DEFAULT_CONCURRENCY = 6


# ─────────────────────────────────────────────────────────────
# Column mapping: FinancialPeriod <-> financial_results
# ─────────────────────────────────────────────────────────────
_PERIOD_TO_COLUMN: dict[str, str] = {
    "revenue": "revenue",
    "gross_profit": "gross_profit",
    "operating_income": "ebit",
    "ebitda": "ebitda",
    "depreciation": "depreciation",
    "interest_expense": "interest_expense",
    "pretax_income": "pbt",
    "tax_provision": "tax",
    "net_income": "pat",
    "eps_basic": "eps",
    "eps_diluted": "eps_diluted",
    "total_assets": "total_assets",
    "total_liabilities": "total_liabilities",
    "equity": "net_worth",
    "total_debt": "total_debt",
    "long_term_debt": "long_term_debt",
    "short_term_debt": "short_term_debt",
    "cash": "cash_and_equiv",
    "short_term_investments": "investments",
    "net_ppe": "fixed_assets",
    "invested_capital": "invested_capital",
    "working_capital": "working_capital",
    "cfo": "cfo",
    "capex": "capex",
    "free_cash_flow": "free_cash_flow",
    "dividends_paid": "dividends_paid",
    "shares_outstanding": "shares_outstanding",
}

_COLUMN_TO_PERIOD = {v: k for k, v in _PERIOD_TO_COLUMN.items()}


# ─────────────────────────────────────────────────────────────
# Run bookkeeping
# ─────────────────────────────────────────────────────────────
async def _start_run(pool: asyncpg.Pool, job: str) -> Any:
    return await pool.fetchval(
        "INSERT INTO ingest_runs (job, status) VALUES ($1, 'RUNNING') RETURNING id", job
    )


async def _finish_run(pool: asyncpg.Pool, run_id: Any, report: FetchReport) -> None:
    report.finish()
    await pool.execute(
        """UPDATE ingest_runs
              SET status = $2, requested = $3, succeeded = $4, failed = $5,
                  skipped = $6, rows_written = $7, errors = $8::jsonb,
                  finished_at = NOW(), duration_seconds = $9
            WHERE id = $1""",
        run_id,
        "FAILED" if report.succeeded == 0 and report.failed > 0 else "SUCCESS",
        report.requested,
        report.succeeded,
        report.failed,
        report.skipped,
        report.rows_written,
        json.dumps(report.errors),
        report.duration_seconds,
    )


# ─────────────────────────────────────────────────────────────
# Symbol universe
# ─────────────────────────────────────────────────────────────
async def load_universe(
    pool: asyncpg.Pool,
    symbols: Sequence[str] | None = None,
    *,
    include_non_equity: bool = False,
) -> list[dict]:
    """
    Stocks this pipeline can work on: active, and linked to a provider ticker.

    A stock without a ``yahoo_ticker`` is skipped rather than guessed at — a
    wrong ticker silently populates one company's page with another's prices.

    Indices and derivatives are excluded unless ``include_non_equity`` is set.
    Price ingestion passes it so benchmarks get their history (beta needs it);
    the valuation and signal stages do not, because there is no business to
    value underneath an index.
    """
    query = """
        SELECT s.nse_symbol, s.yahoo_ticker, s.company_name, s.currency,
               s.exchange, s.country, s.industry, s.benchmark_ticker,
               sc.sector AS sector_name
          FROM stocks s
     LEFT JOIN sector_classification sc ON sc.id = s.sector_id
         WHERE s.is_active = TRUE
           AND s.yahoo_ticker IS NOT NULL
    """
    if not include_non_equity:
        # An index is not a company. It has no filings, no share count and no
        # intrinsic value, and valuing one produces a confident-looking buy
        # rating on something that cannot be bought. Indices are still ingested
        # for price history, because beta is regressed against them — they are
        # excluded only from the stages that assume a business underneath.
        query += " AND COALESCE(s.instrument_type, 'EQ') NOT IN ('INDEX', 'FO')"

    params: list[Any] = []
    if symbols:
        query += " AND s.nse_symbol = ANY($1::text[])"
        params.append(list(symbols))
    query += " ORDER BY s.nse_symbol"

    rows = await pool.fetch(query, *params)
    return [dict(r) for r in rows]


# ─────────────────────────────────────────────────────────────
# Stage 1 — price history
# ─────────────────────────────────────────────────────────────
async def backfill_price_history(
    pool: asyncpg.Pool,
    provider: YahooProvider,
    *,
    symbols: Sequence[str] | None = None,
    years: int = 10,
    concurrency: int = DEFAULT_CONCURRENCY,
) -> FetchReport:
    """
    Fetch and store up to ``years`` of daily OHLCV for every tracked stock.

    Stores raw close and adjusted close separately. Raw is what the stock traded
    at and is what a chart shows; adjusted is corrected for splits and dividends
    and is the only correct input to a return or beta calculation. Conflating
    them puts a fake 50% crash on the chart of every stock that has ever split.
    """
    # Benchmarks are included here specifically: beta is regressed against their
    # price history, so they must be ingested even though they are never valued.
    universe = await load_universe(pool, symbols, include_non_equity=True)
    report = FetchReport(requested=len(universe))
    run_id = await _start_run(pool, "price_history")
    semaphore = asyncio.Semaphore(concurrency)

    async def one(stock: dict) -> None:
        async with semaphore:
            symbol, ticker = stock["nse_symbol"], stock["yahoo_ticker"]
            try:
                candles = await provider.get_daily_history(ticker, years=years)
            except NotFound as exc:
                report.record_failure(symbol, str(exc))
                return
            except Exception as exc:  # noqa: BLE001 — recorded, never swallowed
                report.record_failure(symbol, f"{type(exc).__name__}: {exc}")
                return

            if not candles:
                report.record_skip()
                return
            written = await store_candles(pool, symbol, candles)
            report.record_success(written)

    await asyncio.gather(*(one(s) for s in universe))
    await _finish_run(pool, run_id, report)
    log.info("price history: %s", report.as_dict())
    return report


async def store_candles(
    pool: asyncpg.Pool, symbol: str, candles: Sequence[Candle]
) -> int:
    """Upsert candles. Re-running a backfill corrects prior rows in place."""
    if not candles:
        return 0
    # Record the source the candle actually carries. This used to be the literal
    # "YAHOO" for every bar, which meant the provenance column asserted
    # something the code had no way of knowing — and the provenance check in
    # verify_live.py, which looks for exactly this kind of problem, was blind to
    # it because the label looked legitimate.
    rows = [
        (
            symbol,
            c.date,
            c.open,
            c.high,
            c.low,
            c.close,
            c.adj_close,
            c.volume,
            c.source or "UNKNOWN",
        )
        for c in candles
    ]
    await pool.executemany(
        """INSERT INTO price_candles_daily
               (nse_symbol, date, open, high, low, close, adj_close, volume,
                data_source, fetched_at)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,NOW())
           ON CONFLICT (nse_symbol, date) DO UPDATE
             SET open = EXCLUDED.open,
                 high = EXCLUDED.high,
                 low = EXCLUDED.low,
                 close = EXCLUDED.close,
                 adj_close = EXCLUDED.adj_close,
                 volume = EXCLUDED.volume,
                 data_source = EXCLUDED.data_source,
                 fetched_at = NOW()""",
        rows,
    )
    return len(rows)


async def load_candles(
    pool: asyncpg.Pool, symbol: str, *, limit: int | None = None, adjusted: bool = True
) -> list[Candle]:
    """Read stored candles back, oldest first."""
    query = """
        SELECT date, open, high, low, close, adj_close, volume
          FROM price_candles_daily
         WHERE nse_symbol = $1
         ORDER BY date DESC
    """
    params: list[Any] = [symbol]
    if limit:
        query += " LIMIT $2"
        params.append(limit)

    rows = await pool.fetch(query, *params)
    out: list[Candle] = []
    for row in reversed(rows):
        close = row["close"]
        adj = row["adj_close"]
        if close is None and adj is None:
            continue
        # Valuation work uses adjusted prices; display uses raw. The caller picks.
        primary = adj if (adjusted and adj is not None) else close
        if primary is None:
            continue
        out.append(
            Candle(
                date=row["date"],
                open=float(row["open"]) if row["open"] is not None else None,
                high=float(row["high"]) if row["high"] is not None else None,
                low=float(row["low"]) if row["low"] is not None else None,
                close=float(primary),
                adj_close=float(adj) if adj is not None else None,
                volume=int(row["volume"]) if row["volume"] is not None else None,
            )
        )
    return out


# ─────────────────────────────────────────────────────────────
# Stage 2 — fundamentals
# ─────────────────────────────────────────────────────────────
async def refresh_fundamentals(
    pool: asyncpg.Pool,
    provider: YahooProvider,
    *,
    symbols: Sequence[str] | None = None,
    years: int = 12,
    concurrency: int = DEFAULT_CONCURRENCY,
    include_quarterly: bool = True,
) -> FetchReport:
    """
    Fetch and store annual (and optionally quarterly) filings.

    Asks for a twelve-year window and stores exactly what the source returns.
    Missing years are left missing — never interpolated, never carried forward
    from the previous year. A fabricated filing is indistinguishable from a real
    one once it is in the table, and it would silently drive a valuation.
    """
    universe = await load_universe(pool, symbols)
    report = FetchReport(requested=len(universe))
    run_id = await _start_run(pool, "fundamentals")
    semaphore = asyncio.Semaphore(concurrency)

    async def one(stock: dict) -> None:
        async with semaphore:
            symbol, ticker = stock["nse_symbol"], stock["yahoo_ticker"]
            written = 0
            try:
                annual = await provider.get_fundamentals(
                    ticker, period_type="A", years=years
                )
                written += await store_financial_periods(pool, symbol, annual)
            except NotFound as exc:
                report.record_failure(symbol, str(exc))
                return
            except Exception as exc:  # noqa: BLE001
                report.record_failure(symbol, f"{type(exc).__name__}: {exc}")
                return

            if include_quarterly:
                try:
                    quarterly = await provider.get_fundamentals(
                        ticker, period_type="Q", years=4
                    )
                    written += await store_financial_periods(pool, symbol, quarterly)
                except Exception as exc:  # noqa: BLE001
                    # Quarterly data is a nice-to-have; its absence must not
                    # fail an otherwise good annual fetch.
                    log.debug("%s: quarterly fetch failed: %s", symbol, exc)

            report.record_success(written)

    await asyncio.gather(*(one(s) for s in universe))
    await _finish_run(pool, run_id, report)
    log.info("fundamentals: %s", report.as_dict())
    return report


async def store_financial_periods(
    pool: asyncpg.Pool, symbol: str, periods: Sequence[FinancialPeriod]
) -> int:
    """
    Upsert filings.

    Only columns the provider actually populated are written, so a partial
    refresh cannot blank a field that an earlier, richer source filled in.
    """
    if not periods:
        return 0

    written = 0
    for period in periods:
        values: dict[str, Any] = {}
        for attr, column in _PERIOD_TO_COLUMN.items():
            value = getattr(period, attr, None)
            if value is not None:
                values[column] = value
        if not values:
            continue

        values["currency"] = period.currency
        values["data_source"] = period.source or "YAHOO"
        values["confidence_level"] = "HIGH" if len(values) > 15 else "MEDIUM"

        columns = ["nse_symbol", "period_type", "period_end", *values.keys()]
        params = [symbol, period.period_type, period.period_end, *values.values()]
        placeholders = ", ".join(f"${i + 1}" for i in range(len(params)))
        updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in values)

        await pool.execute(
            f"""INSERT INTO financial_results ({", ".join(columns)}, fetched_at)
                VALUES ({placeholders}, NOW())
                ON CONFLICT (nse_symbol, period_type, period_end) DO UPDATE
                  SET {updates}, fetched_at = NOW(), updated_at = NOW()""",
            *params,
        )
        written += 1
    return written


async def load_financial_periods(
    pool: asyncpg.Pool, symbol: str, *, period_type: str = "A"
) -> list[FinancialPeriod]:
    """Read filings back into the engine's own type, oldest first."""
    columns = sorted(set(_PERIOD_TO_COLUMN.values()))
    rows = await pool.fetch(
        f"""SELECT period_end, period_type, currency, data_source, {", ".join(columns)}
              FROM financial_results
             WHERE nse_symbol = $1 AND period_type = $2
             ORDER BY period_end ASC""",
        symbol,
        period_type,
    )

    out: list[FinancialPeriod] = []
    for row in rows:
        period = FinancialPeriod(
            period_end=row["period_end"],
            period_type=row["period_type"],
            currency=row["currency"],
            source=row["data_source"] or "",
        )
        for column in columns:
            value = row[column]
            if value is None:
                continue
            attr = _COLUMN_TO_PERIOD.get(column)
            if attr:
                setattr(period, attr, float(value))
        out.append(period)
    return out


# ─────────────────────────────────────────────────────────────
# Stage 3 — live quotes
# ─────────────────────────────────────────────────────────────
async def refresh_quotes(
    pool: asyncpg.Pool,
    provider: YahooProvider,
    *,
    symbols: Sequence[str] | None = None,
    redis_client=None,
    concurrency: int = 10,
) -> FetchReport:
    """
    Poll live prices and store them.

    Writes to Postgres for durability and, when a Redis client is supplied,
    publishes to the per-symbol and dashboard channels the websocket layer
    already listens on.
    """
    universe = await load_universe(pool, symbols)
    report = FetchReport(requested=len(universe))
    run_id = await _start_run(pool, "quotes")

    pairs = [(s["nse_symbol"], s["yahoo_ticker"]) for s in universe]
    quotes, errors = await provider.get_quotes(pairs, concurrency=concurrency)

    for symbol, message in errors.items():
        report.record_failure(symbol, message)

    if quotes:
        rows = [
            (
                q.symbol,
                q.price,
                q.open,
                q.day_high,
                q.day_low,
                q.previous_close,
                q.change_abs,
                q.change_pct,
                q.volume,
                q.week_52_high,
                q.week_52_low,
                q.market_cap,
                q.source,
            )
            for q in quotes
        ]
        await pool.executemany(
            """INSERT INTO realtime_quotes
                   (nse_symbol, ltp, open, high, low, close, change_abs, change_pct,
                    volume, week_52_high, week_52_low, market_cap, data_source,
                    is_stale, last_updated)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,FALSE,NOW())
               ON CONFLICT (nse_symbol) DO UPDATE
                 SET ltp = EXCLUDED.ltp, open = EXCLUDED.open, high = EXCLUDED.high,
                     low = EXCLUDED.low, close = EXCLUDED.close,
                     change_abs = EXCLUDED.change_abs, change_pct = EXCLUDED.change_pct,
                     volume = EXCLUDED.volume, week_52_high = EXCLUDED.week_52_high,
                     week_52_low = EXCLUDED.week_52_low, market_cap = EXCLUDED.market_cap,
                     data_source = EXCLUDED.data_source, is_stale = FALSE,
                     last_updated = NOW()""",
            rows,
        )
        for _ in quotes:
            report.record_success(1)

        if redis_client is not None:
            await _publish_quotes(redis_client, quotes)

    await _finish_run(pool, run_id, report)
    return report


async def _publish_quotes(redis_client, quotes) -> None:
    """Push to Redis for the live dashboard. Failures here never fail ingest."""
    payloads = []
    for quote in quotes:
        payload = {
            "symbol": quote.symbol,
            "ltp": quote.price,
            "change_abs": quote.change_abs,
            "change_pct": quote.change_pct,
            "volume": quote.volume,
            "currency": quote.currency,
            "market_state": quote.market_state,
            "ts": quote.fetched_at.isoformat(),
        }
        payloads.append(payload)
        try:
            encoded = json.dumps(payload)
            await redis_client.setex(f"latest:{quote.symbol}", 120, encoded)
            await redis_client.publish(f"ticks:{quote.symbol}", encoded)
        except Exception as exc:  # noqa: BLE001
            log.debug("redis publish failed for %s: %s", quote.symbol, exc)

    try:
        await redis_client.publish(
            "ticks:dashboard",
            json.dumps(
                {
                    "type": "batch",
                    # Same reasoning as the candle provenance above: report the
                    # source the quotes carry, not a hardcoded guess.
                    "source": (
                        quotes[0].source if quotes and quotes[0].source else "UNKNOWN"
                    ),
                    "updates": payloads,
                    "ts": datetime.now(timezone.utc).isoformat(),
                }
            ),
        )
    except Exception as exc:  # noqa: BLE001
        log.debug("redis dashboard publish failed: %s", exc)


# ─────────────────────────────────────────────────────────────
# Stage 4 — technicals
# ─────────────────────────────────────────────────────────────
async def _load_market_context(pool: asyncpg.Pool, country: str | None) -> MarketContext:
    """Read the operator's house view for a market, falling back to defaults."""
    row = await pool.fetchrow(
        "SELECT * FROM market_assumptions WHERE country = $1", country or "India"
    )
    if row is None:
        return MarketContext.for_country(country)
    return MarketContext(
        risk_free_rate=float(row["risk_free_rate"]),
        equity_risk_premium=float(row["equity_risk_premium"]),
        country=row["country"],
        currency=row["currency"],
        default_beta=float(row["default_beta"]),
        as_of=date.today(),
    )


async def _benchmark_candles(
    pool: asyncpg.Pool, ticker: str | None, cache: dict[str, list[Candle]]
) -> list[Candle]:
    """
    Load an index's history once per run and reuse it.

    Beta is regressed for every stock in the universe against the same index;
    re-reading a decade of index candles thousands of times would dominate the
    run's cost.
    """
    if not ticker:
        return []
    if ticker in cache:
        return cache[ticker]
    candles = await load_candles(pool, ticker)
    cache[ticker] = candles
    return candles


async def run_technicals(
    pool: asyncpg.Pool, *, symbols: Sequence[str] | None = None
) -> FetchReport:
    """Compute and store the current technical state for every tracked stock."""
    universe = await load_universe(pool, symbols)
    report = FetchReport(requested=len(universe))
    run_id = await _start_run(pool, "technicals")
    benchmark_cache: dict[str, list[Candle]] = {}

    for stock in universe:
        symbol = stock["nse_symbol"]
        try:
            candles = await load_candles(pool, symbol)
            if not candles:
                report.record_skip()
                continue

            snapshot = compute_snapshot(candles)

            beta_value = None
            benchmark = stock.get("benchmark_ticker")
            if benchmark:
                index_candles = await _benchmark_candles(
                    pool, benchmark, benchmark_cache
                )
                if index_candles:
                    beta_value, _ = regress_beta_against(candles, index_candles)

            await _store_snapshot(pool, symbol, snapshot, beta_value)
            report.record_success(1)
        except Exception as exc:  # noqa: BLE001
            report.record_failure(symbol, f"{type(exc).__name__}: {exc}")

    await _finish_run(pool, run_id, report)
    return report


async def _store_snapshot(
    pool: asyncpg.Pool, symbol: str, snapshot, beta_value: float | None
) -> None:
    payload = snapshot.as_dict()
    await pool.execute(
        """INSERT INTO technical_snapshots
               (nse_symbol, as_of, price, sma_20, sma_50, sma_200, ema_12, ema_26,
                rsi_14, macd_line, macd_signal, macd_histogram, atr_14, atr_pct,
                bollinger_percent_b, week_52_high, week_52_low, pct_from_52w_high,
                pct_from_52w_low, return_1m, return_3m, return_6m, return_1y,
                return_3y_cagr, return_5y_cagr, volatility_1y, max_drawdown_5y,
                avg_volume_20d, volume_ratio, beta, trend, candles_used, warnings,
                computed_at)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,
                   $19,$20,$21,$22,$23,$24,$25,$26,$27,$28,$29,$30,$31,$32,$33::jsonb,
                   NOW())
           ON CONFLICT (nse_symbol) DO UPDATE SET
                as_of = EXCLUDED.as_of, price = EXCLUDED.price,
                sma_20 = EXCLUDED.sma_20, sma_50 = EXCLUDED.sma_50,
                sma_200 = EXCLUDED.sma_200, ema_12 = EXCLUDED.ema_12,
                ema_26 = EXCLUDED.ema_26, rsi_14 = EXCLUDED.rsi_14,
                macd_line = EXCLUDED.macd_line, macd_signal = EXCLUDED.macd_signal,
                macd_histogram = EXCLUDED.macd_histogram, atr_14 = EXCLUDED.atr_14,
                atr_pct = EXCLUDED.atr_pct,
                bollinger_percent_b = EXCLUDED.bollinger_percent_b,
                week_52_high = EXCLUDED.week_52_high,
                week_52_low = EXCLUDED.week_52_low,
                pct_from_52w_high = EXCLUDED.pct_from_52w_high,
                pct_from_52w_low = EXCLUDED.pct_from_52w_low,
                return_1m = EXCLUDED.return_1m, return_3m = EXCLUDED.return_3m,
                return_6m = EXCLUDED.return_6m, return_1y = EXCLUDED.return_1y,
                return_3y_cagr = EXCLUDED.return_3y_cagr,
                return_5y_cagr = EXCLUDED.return_5y_cagr,
                volatility_1y = EXCLUDED.volatility_1y,
                max_drawdown_5y = EXCLUDED.max_drawdown_5y,
                avg_volume_20d = EXCLUDED.avg_volume_20d,
                volume_ratio = EXCLUDED.volume_ratio, beta = EXCLUDED.beta,
                trend = EXCLUDED.trend, candles_used = EXCLUDED.candles_used,
                warnings = EXCLUDED.warnings, computed_at = NOW()""",
        symbol,
        snapshot.as_of,
        payload["price"],
        payload["sma_20"],
        payload["sma_50"],
        payload["sma_200"],
        payload["ema_12"],
        payload["ema_26"],
        payload["rsi_14"],
        payload["macd_line"],
        payload["macd_signal"],
        payload["macd_histogram"],
        payload["atr_14"],
        payload["atr_pct"],
        payload["bollinger_percent_b"],
        payload["week_52_high"],
        payload["week_52_low"],
        payload["pct_from_52w_high"],
        payload["pct_from_52w_low"],
        payload["return_1m"],
        payload["return_3m"],
        payload["return_6m"],
        payload["return_1y"],
        payload["return_3y_cagr"],
        payload["return_5y_cagr"],
        payload["volatility_1y"],
        payload["max_drawdown_5y"],
        payload["avg_volume_20d"],
        payload["volume_ratio"],
        beta_value,
        snapshot.trend,
        snapshot.candles_used,
        json.dumps(snapshot.warnings),
    )


# ─────────────────────────────────────────────────────────────
# Stage 5 — valuation
# ─────────────────────────────────────────────────────────────
async def run_valuation(
    pool: asyncpg.Pool, *, symbols: Sequence[str] | None = None
) -> FetchReport:
    """
    Value every tracked stock from its stored filings.

    A stock whose filings cannot support a valuation is recorded as a skip with
    the engine's stated reason, and its previous intrinsic value is cleared —
    never left in place to go stale and read as current.
    """
    universe = await load_universe(pool, symbols)
    report = FetchReport(requested=len(universe))
    run_id = await _start_run(pool, "valuation")

    for stock in universe:
        symbol = stock["nse_symbol"]
        try:
            periods = await load_financial_periods(pool, symbol, period_type="A")
            quote = await pool.fetchrow(
                "SELECT ltp, market_cap FROM realtime_quotes WHERE nse_symbol = $1",
                symbol,
            )
            price = float(quote["ltp"]) if quote and quote["ltp"] else None
            market_cap = (
                float(quote["market_cap"]) if quote and quote["market_cap"] else None
            )

            technical = await pool.fetchrow(
                "SELECT beta FROM technical_snapshots WHERE nse_symbol = $1", symbol
            )
            beta_value = (
                float(technical["beta"]) if technical and technical["beta"] else None
            )

            market = await _load_market_context(pool, stock.get("country"))

            result = compute_intrinsic_value(
                symbol,
                periods,
                current_price=price,
                market=market,
                sector=stock.get("sector_name"),
                industry=stock.get("industry"),
                market_cap=market_cap,
                regressed_beta=beta_value,
                beta_source="regressed" if beta_value else "default",
            )

            reverse = None
            if result.ok and result.history and result.cost_of_capital and price:
                reverse = reverse_dcf(
                    result.history,
                    result.cost_of_capital,
                    market,
                    current_price=price,
                    shares=result.history.shares_outstanding or 0,
                    net_debt=result.history.net_debt or 0.0,
                )

            await _store_valuation(pool, symbol, result, reverse)
            if result.ok:
                report.record_success(1)
            else:
                report.record_skip()
        except Exception as exc:  # noqa: BLE001
            report.record_failure(symbol, f"{type(exc).__name__}: {exc}")

    await _finish_run(pool, run_id, report)
    return report


def _profile_as_dict(profile) -> dict:
    """Serialize the history profile for the audit panel in the UI."""
    if profile is None:
        return {}
    out: dict[str, Any] = {}
    for key, value in vars(profile).items():
        if isinstance(value, date):
            out[key] = value.isoformat()
        elif isinstance(value, float):
            out[key] = round(value, 6)
        else:
            out[key] = value
    return out


async def _store_valuation(pool: asyncpg.Pool, symbol: str, result, reverse) -> None:
    payload = result.as_dict()
    coc = payload.get("cost_of_capital") or {}

    await pool.execute(
        """INSERT INTO intrinsic_values
               (nse_symbol, iv_bear, iv_base, iv_bull, iv_blended, cmp, upside_pct,
                margin_of_safety, primary_model, valuation_confidence,
                confidence_score, years_of_history, wacc, cost_of_equity,
                cost_of_debt, beta, beta_source, terminal_value_share,
                assumptions, projection, models, history_profile, warnings,
                reverse_dcf, computed_at, updated_at)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,
                   $19::jsonb,$20::jsonb,$21::jsonb,$22::jsonb,$23::jsonb,$24::jsonb,
                   NOW(),NOW())
           ON CONFLICT (nse_symbol) DO UPDATE SET
                iv_bear = EXCLUDED.iv_bear, iv_base = EXCLUDED.iv_base,
                iv_bull = EXCLUDED.iv_bull, iv_blended = EXCLUDED.iv_blended,
                cmp = EXCLUDED.cmp, upside_pct = EXCLUDED.upside_pct,
                margin_of_safety = EXCLUDED.margin_of_safety,
                primary_model = EXCLUDED.primary_model,
                valuation_confidence = EXCLUDED.valuation_confidence,
                confidence_score = EXCLUDED.confidence_score,
                years_of_history = EXCLUDED.years_of_history,
                wacc = EXCLUDED.wacc, cost_of_equity = EXCLUDED.cost_of_equity,
                cost_of_debt = EXCLUDED.cost_of_debt, beta = EXCLUDED.beta,
                beta_source = EXCLUDED.beta_source,
                terminal_value_share = EXCLUDED.terminal_value_share,
                assumptions = EXCLUDED.assumptions,
                projection = EXCLUDED.projection, models = EXCLUDED.models,
                history_profile = EXCLUDED.history_profile,
                warnings = EXCLUDED.warnings, reverse_dcf = EXCLUDED.reverse_dcf,
                computed_at = NOW(), updated_at = NOW()""",
        symbol,
        payload["iv_bear"],
        payload["iv_base"],
        payload["iv_bull"],
        payload["iv_blended"],
        payload["current_price"],
        payload["upside_pct"],
        payload["margin_of_safety"],
        payload["primary_model"],
        payload["confidence"],
        payload["confidence_score"],
        payload["years_of_history"],
        coc.get("wacc"),
        coc.get("cost_of_equity"),
        coc.get("cost_of_debt_pretax"),
        coc.get("beta"),
        coc.get("beta_source"),
        payload["terminal_value_share"],
        json.dumps(payload["assumptions"]),
        json.dumps(payload["projection"]),
        json.dumps(payload["models"]),
        json.dumps(_profile_as_dict(result.history)),
        json.dumps(
            payload["warnings"] + ([] if result.ok else [result.reason])
        ),
        json.dumps(reverse or {}),
    )


# ─────────────────────────────────────────────────────────────
# Stage 6 — signals
# ─────────────────────────────────────────────────────────────
async def run_signals(
    pool: asyncpg.Pool, *, symbols: Sequence[str] | None = None
) -> FetchReport:
    """Turn stored valuations and technicals into an actionable view per stock."""
    from .analytics.intrinsic_value import HistoryProfile, ValuationResult
    from .analytics.technicals import TechnicalSnapshot

    universe = await load_universe(pool, symbols)
    report = FetchReport(requested=len(universe))
    run_id = await _start_run(pool, "signals")

    for stock in universe:
        symbol = stock["nse_symbol"]
        try:
            valuation_row = await pool.fetchrow(
                "SELECT * FROM intrinsic_values WHERE nse_symbol = $1", symbol
            )
            if valuation_row is None:
                report.record_skip()
                continue

            technical_row = await pool.fetchrow(
                "SELECT * FROM technical_snapshots WHERE nse_symbol = $1", symbol
            )
            quote = await pool.fetchrow(
                "SELECT ltp FROM realtime_quotes WHERE nse_symbol = $1", symbol
            )
            price = float(quote["ltp"]) if quote and quote["ltp"] else None

            valuation = _valuation_from_row(symbol, valuation_row, price)
            technical = _snapshot_from_row(technical_row)

            signal = generate_signal(symbol, valuation, technical, current_price=price)
            await _store_signal(pool, signal)
            report.record_success(1)
        except Exception as exc:  # noqa: BLE001
            report.record_failure(symbol, f"{type(exc).__name__}: {exc}")

    await _finish_run(pool, run_id, report)
    return report


def _valuation_from_row(symbol: str, row, price: float | None):
    """Rebuild a ValuationResult from its stored form."""
    from .analytics.intrinsic_value import (
        CostOfCapital,
        HistoryProfile,
        ValuationResult,
    )

    def num(key):
        value = row[key]
        return float(value) if value is not None else None

    def js(key, default):
        raw = row[key]
        if raw is None:
            return default
        return json.loads(raw) if isinstance(raw, str) else raw

    result = ValuationResult(symbol=symbol, ok=row["iv_blended"] is not None)
    result.iv_bear = num("iv_bear")
    result.iv_base = num("iv_base")
    result.iv_bull = num("iv_bull")
    result.iv_blended = num("iv_blended")
    result.current_price = price if price is not None else num("cmp")
    result.primary_model = row["primary_model"] or ""
    result.confidence = row["valuation_confidence"] or "LOW"
    result.confidence_score = num("confidence_score") or 0.0
    result.years_of_history = row["years_of_history"] or 0
    result.terminal_value_share = num("terminal_value_share")
    result.warnings = list(js("warnings", []))

    # Upside is recomputed against the live price rather than reused from the
    # stored row, so a signal generated between valuation runs reflects the
    # price now, not the price at the last overnight job.
    if result.iv_blended and result.current_price:
        result.upside_pct = (
            (result.iv_blended - result.current_price) / result.current_price * 100.0
        )
        result.margin_of_safety = 1.0 - (result.current_price / result.iv_blended)

    if row["wacc"] is not None:
        result.cost_of_capital = CostOfCapital(
            wacc=num("wacc"),
            cost_of_equity=num("cost_of_equity") or 0.0,
            cost_of_debt_pretax=num("cost_of_debt") or 0.0,
            cost_of_debt_after_tax=(num("cost_of_debt") or 0.0) * 0.75,
            beta=num("beta") or 1.0,
            beta_source=row["beta_source"] or "default",
            equity_weight=1.0,
            debt_weight=0.0,
            risk_free_rate=0.0,
            equity_risk_premium=0.0,
        )

    stored_profile = js("history_profile", {})
    if stored_profile:
        profile = HistoryProfile(
            years=stored_profile.get("years", 0),
            first_period=None,
            last_period=None,
        )
        for key, value in stored_profile.items():
            if key in ("first_period", "last_period"):
                continue
            if hasattr(profile, key):
                setattr(profile, key, value)
        result.history = profile

    if not result.ok:
        result.reason = (result.warnings or ["Valuation unavailable"])[0]
    return result


def _snapshot_from_row(row):
    """Rebuild a TechnicalSnapshot from its stored form."""
    from .analytics.technicals import TechnicalSnapshot

    if row is None:
        return None

    snapshot = TechnicalSnapshot()
    for field_name in vars(snapshot):
        if field_name in ("warnings", "trend", "as_of", "candles_used"):
            continue
        try:
            value = row[field_name]
        except (KeyError, IndexError):
            continue
        if value is not None:
            setattr(snapshot, field_name, float(value))

    snapshot.as_of = row["as_of"]
    snapshot.trend = row["trend"] or "UNKNOWN"
    snapshot.candles_used = row["candles_used"] or 0
    raw_warnings = row["warnings"]
    if raw_warnings:
        snapshot.warnings = (
            json.loads(raw_warnings) if isinstance(raw_warnings, str) else list(raw_warnings)
        )
    return snapshot


async def _store_signal(pool: asyncpg.Pool, signal) -> None:
    payload = signal.as_dict()
    plan = payload["plan"]

    previous = await pool.fetchrow(
        "SELECT action, signal_color FROM signals WHERE nse_symbol = $1", signal.symbol
    )
    changed = previous is not None and previous["action"] != signal.action

    await pool.execute(
        """INSERT INTO signals
               (nse_symbol, signal, signal_color, signal_label, action, conviction,
                headline, rationale, invalidation, value_score, quality_score,
                momentum_score, risk_score, composite_score, upside_pct,
                margin_of_safety, valuation_confidence, conditions, blocking_flags,
                entry_low, entry_high, max_buy_price, stop_loss, target_1, target_2,
                risk_reward, position_size_pct, horizon, trend, intrinsic_value,
                current_price, main_reason, prev_signal, prev_signal_color,
                signal_changed_at, data_freshness, computed_at, updated_at)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9::jsonb,$10,$11,$12,$13,$14,$15,
                   $16,$17,$18::jsonb,$19::jsonb,$20,$21,$22,$23,$24,$25,$26,$27,$28,
                   $29,$30,$31,$32,$33,$34,$35,$36,NOW(),NOW())
           ON CONFLICT (nse_symbol) DO UPDATE SET
                signal = EXCLUDED.signal, signal_color = EXCLUDED.signal_color,
                signal_label = EXCLUDED.signal_label, action = EXCLUDED.action,
                conviction = EXCLUDED.conviction, headline = EXCLUDED.headline,
                rationale = EXCLUDED.rationale, invalidation = EXCLUDED.invalidation,
                value_score = EXCLUDED.value_score,
                quality_score = EXCLUDED.quality_score,
                momentum_score = EXCLUDED.momentum_score,
                risk_score = EXCLUDED.risk_score,
                composite_score = EXCLUDED.composite_score,
                upside_pct = EXCLUDED.upside_pct,
                margin_of_safety = EXCLUDED.margin_of_safety,
                valuation_confidence = EXCLUDED.valuation_confidence,
                conditions = EXCLUDED.conditions,
                blocking_flags = EXCLUDED.blocking_flags,
                entry_low = EXCLUDED.entry_low, entry_high = EXCLUDED.entry_high,
                max_buy_price = EXCLUDED.max_buy_price,
                stop_loss = EXCLUDED.stop_loss, target_1 = EXCLUDED.target_1,
                target_2 = EXCLUDED.target_2, risk_reward = EXCLUDED.risk_reward,
                position_size_pct = EXCLUDED.position_size_pct,
                horizon = EXCLUDED.horizon, trend = EXCLUDED.trend,
                intrinsic_value = EXCLUDED.intrinsic_value,
                current_price = EXCLUDED.current_price,
                main_reason = EXCLUDED.main_reason,
                prev_signal = EXCLUDED.prev_signal,
                prev_signal_color = EXCLUDED.prev_signal_color,
                signal_changed_at = EXCLUDED.signal_changed_at,
                data_freshness = EXCLUDED.data_freshness,
                computed_at = NOW(), updated_at = NOW()""",
        signal.symbol,
        signal.action,
        payload["color"],
        payload["label"],
        signal.action,
        payload["conviction"],
        payload["headline"],
        json.dumps(payload["rationale"]),
        json.dumps(payload["invalidation"]),
        payload["value_score"],
        payload["quality_score"],
        payload["momentum_score"],
        payload["risk_score"],
        payload["composite_score"],
        payload["upside_pct"],
        None,
        payload["valuation_confidence"],
        json.dumps(payload["conditions"]),
        json.dumps(payload["risk_flags"]),
        plan["entry_low"],
        plan["entry_high"],
        plan["max_buy_price"],
        plan["stop_loss"],
        plan["target_1"],
        plan["target_2"],
        plan["risk_reward"],
        plan["position_size_pct"],
        plan["horizon"],
        payload["trend"],
        payload["intrinsic_value"],
        payload["current_price"],
        payload["headline"],
        previous["action"] if changed else None,
        previous["signal_color"] if changed else None,
        datetime.now(timezone.utc) if changed else None,
        "FRESH",
    )


# ─────────────────────────────────────────────────────────────
# Full pipeline
# ─────────────────────────────────────────────────────────────
async def run_full_pipeline(
    pool: asyncpg.Pool,
    *,
    symbols: Sequence[str] | None = None,
    years: int = 10,
    redis_client=None,
    skip_history: bool = False,
    skip_fundamentals: bool = False,
) -> dict[str, dict]:
    """
    Ingest, then compute, in dependency order.

    Order matters and is not arbitrary: technicals must run before valuation
    because valuation reads the regressed beta the technical stage produces, and
    signals run last because they read both.
    """
    reports: dict[str, dict] = {}

    async with YahooProvider() as provider:
        if not skip_history:
            reports["price_history"] = (
                await backfill_price_history(
                    pool, provider, symbols=symbols, years=years
                )
            ).as_dict()

        if not skip_fundamentals:
            reports["fundamentals"] = (
                await refresh_fundamentals(pool, provider, symbols=symbols)
            ).as_dict()

        reports["quotes"] = (
            await refresh_quotes(
                pool, provider, symbols=symbols, redis_client=redis_client
            )
        ).as_dict()

    reports["technicals"] = (await run_technicals(pool, symbols=symbols)).as_dict()
    reports["valuation"] = (await run_valuation(pool, symbols=symbols)).as_dict()
    reports["signals"] = (await run_signals(pool, symbols=symbols)).as_dict()
    return reports
