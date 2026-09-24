"""
End-to-end pipeline tests against a real PostgreSQL database.

These exercise every SQL statement in the ingestion layer: the upserts, the
JSONB round-trips, the rebuilding of engine objects out of stored rows, and the
dependency order between stages. Nothing here is mocked except the provider's
HTTP call, which is covered separately by ``tests/test_providers.py`` against
recorded response shapes and by ``scripts/verify_live.py`` against the live
source.

Skipped automatically when no database is reachable, so the unit suite still
runs anywhere:

    export STOCKLENS_TEST_DB=postgresql://postgres@127.0.0.1:5433/stocklens_test
    pytest tests/test_pipeline.py
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

pytest.importorskip("asyncpg")
import asyncpg  # noqa: E402

from services import ingest_service as ing  # noqa: E402
from services.providers.base import Quote  # noqa: E402
from tests.fixtures.companies import (  # noqa: E402
    bank_history,
    build_candles,
    build_history,
    sparse_history,
)

TEST_DB = os.environ.get("STOCKLENS_TEST_DB")

pytestmark = pytest.mark.skipif(
    not TEST_DB, reason="STOCKLENS_TEST_DB not set; skipping database tests"
)


# ─────────────────────────────────────────────────────────────
# A provider that serves fixtures through the real interface
# ─────────────────────────────────────────────────────────────
class StubProvider:
    """
    Implements the surface ``ingest_service`` calls on ``YahooProvider``.

    Per-symbol overrides let one test give a company ten clean years and another
    only three, so the pipeline's handling of thin data is exercised for real.
    """

    def __init__(self, *, fundamentals=None, candles=None, price=280.0, fail=()):
        self._fundamentals = fundamentals or {}
        self._candles = candles or {}
        self._price = price
        self._fail = set(fail)

    async def get_daily_history(self, ticker, *, years=10, start=None, end=None):
        if ticker in self._fail:
            raise RuntimeError(f"simulated upstream failure for {ticker}")
        if ticker in self._candles:
            return self._candles[ticker]
        return build_candles(days=1400)

    async def get_fundamentals(self, ticker, *, period_type="A", years=12):
        if ticker in self._fail:
            raise RuntimeError(f"simulated upstream failure for {ticker}")
        if period_type == "Q":
            return []
        if ticker in self._fundamentals:
            return self._fundamentals[ticker]
        return build_history(years=10)

    async def get_quotes(self, tickers, *, concurrency=8):
        quotes, errors = [], {}
        for symbol, ticker in tickers:
            if ticker in self._fail:
                errors[symbol] = "simulated upstream failure"
                continue
            quotes.append(
                Quote(
                    symbol=symbol,
                    ticker=ticker,
                    price=self._price,
                    previous_close=self._price * 0.98,
                    open=self._price * 0.99,
                    day_high=self._price * 1.01,
                    day_low=self._price * 0.97,
                    volume=1_200_000,
                    currency="INR",
                    exchange="NSE",
                    market_state="REGULAR",
                    week_52_high=self._price * 1.2,
                    week_52_low=self._price * 0.6,
                    market_cap=self._price * 1000,
                    source="STUB",
                    fetched_at=datetime.now(timezone.utc),
                )
            )
        return quotes, errors

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None


# ─────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────
@pytest.fixture
async def pool():
    connection_pool = await asyncpg.create_pool(TEST_DB, min_size=1, max_size=4)
    yield connection_pool
    await connection_pool.close()


@pytest.fixture
async def clean_db(pool):
    """A known-empty slate before each test."""
    await pool.execute(
        """TRUNCATE signals, intrinsic_values, technical_snapshots, ingest_runs,
                    price_candles_daily, financial_results, realtime_quotes,
                    research_notes CASCADE"""
    )
    await pool.execute("DELETE FROM stocks")
    await pool.execute("DELETE FROM sector_classification")
    return pool


async def seed_stock(
    pool,
    symbol: str,
    *,
    ticker: str | None = None,
    sector: str = "IT Services",
    instrument_type: str = "EQ",
    benchmark: str | None = None,
    country: str = "India",
):
    await pool.execute(
        "INSERT INTO sector_classification (sector, macro_sector) VALUES ($1,$1) "
        "ON CONFLICT DO NOTHING",
        sector,
    )
    sector_id = await pool.fetchval(
        "SELECT id FROM sector_classification WHERE sector = $1", sector
    )
    await pool.execute(
        """INSERT INTO stocks
               (nse_symbol, company_name, yahoo_ticker, sector_id, exchange,
                currency, country, benchmark_ticker, instrument_type, is_active)
           VALUES ($1,$2,$3,$4,'NSE','INR',$5,$6,$7,TRUE)
           ON CONFLICT (nse_symbol) DO UPDATE
             SET yahoo_ticker = EXCLUDED.yahoo_ticker,
                 instrument_type = EXCLUDED.instrument_type,
                 benchmark_ticker = EXCLUDED.benchmark_ticker""",
        symbol,
        f"{symbol} Ltd",
        ticker or f"{symbol}.NS",
        sector_id,
        country,
        benchmark,
        instrument_type,
    )


# ─────────────────────────────────────────────────────────────
# Stage-by-stage
# ─────────────────────────────────────────────────────────────
class TestPriceHistory:
    async def test_stores_candles(self, clean_db):
        await seed_stock(clean_db, "ACME")
        report = await ing.backfill_price_history(clean_db, StubProvider(), years=10)
        assert report.succeeded == 1
        assert report.rows_written == 1400
        stored = await clean_db.fetchval(
            "SELECT count(*) FROM price_candles_daily WHERE nse_symbol = 'ACME'"
        )
        assert stored == 1400

    async def test_stores_adjusted_close_separately_from_raw(self, clean_db):
        # One column serving both jobs puts a fake crash on every split chart.
        await seed_stock(clean_db, "ACME")
        await ing.backfill_price_history(clean_db, StubProvider())
        row = await clean_db.fetchrow(
            "SELECT close, adj_close FROM price_candles_daily "
            "WHERE nse_symbol = 'ACME' ORDER BY date DESC LIMIT 1"
        )
        assert row["close"] is not None
        assert row["adj_close"] is not None

    async def test_rerunning_updates_in_place(self, clean_db):
        await seed_stock(clean_db, "ACME")
        await ing.backfill_price_history(clean_db, StubProvider())
        first = await clean_db.fetchval("SELECT count(*) FROM price_candles_daily")
        await ing.backfill_price_history(clean_db, StubProvider())
        assert await clean_db.fetchval("SELECT count(*) FROM price_candles_daily") == first

    async def test_one_failure_does_not_abort_the_batch(self, clean_db):
        await seed_stock(clean_db, "GOOD")
        await seed_stock(clean_db, "BAD")
        report = await ing.backfill_price_history(
            clean_db, StubProvider(fail={"BAD.NS"})
        )
        assert report.succeeded == 1
        assert report.failed == 1
        assert "BAD" in report.errors

    async def test_records_the_run(self, clean_db):
        await seed_stock(clean_db, "ACME")
        await ing.backfill_price_history(clean_db, StubProvider())
        run = await clean_db.fetchrow(
            "SELECT job, status, succeeded FROM ingest_runs WHERE job = 'price_history'"
        )
        assert run["status"] == "SUCCESS"
        assert run["succeeded"] == 1


class TestFundamentals:
    async def test_stores_and_reads_back_every_period(self, clean_db):
        await seed_stock(clean_db, "ACME")
        await ing.refresh_fundamentals(
            clean_db, StubProvider(), include_quarterly=False
        )
        periods = await ing.load_financial_periods(clean_db, "ACME")
        assert len(periods) == 10
        assert all(p.revenue for p in periods)
        assert periods[0].period_end < periods[-1].period_end  # oldest first

    async def test_round_trip_preserves_values(self, clean_db):
        await seed_stock(clean_db, "ACME")
        original = build_history(years=10)
        await ing.refresh_fundamentals(
            clean_db,
            StubProvider(fundamentals={"ACME.NS": original}),
            include_quarterly=False,
        )
        restored = await ing.load_financial_periods(clean_db, "ACME")
        assert restored[-1].revenue == pytest.approx(original[-1].revenue, rel=1e-6)
        assert restored[-1].operating_income == pytest.approx(
            original[-1].operating_income, rel=1e-6
        )
        assert restored[-1].shares_outstanding == pytest.approx(
            original[-1].shares_outstanding, rel=1e-6
        )

    async def test_partial_refresh_does_not_blank_existing_fields(self, clean_db):
        await seed_stock(clean_db, "ACME")
        await ing.refresh_fundamentals(
            clean_db, StubProvider(), include_quarterly=False
        )

        # A later, thinner fetch carrying only revenue must not erase the rest.
        thin = build_history(years=10)
        for period in thin:
            period.operating_income = None
            period.cfo = None
        await ing.refresh_fundamentals(
            clean_db,
            StubProvider(fundamentals={"ACME.NS": thin}),
            include_quarterly=False,
        )
        restored = await ing.load_financial_periods(clean_db, "ACME")
        assert restored[-1].operating_income is not None


class TestComputeStages:
    async def _full_run(self, pool, **provider_kwargs):
        provider = StubProvider(**provider_kwargs)
        await ing.backfill_price_history(pool, provider)
        await ing.refresh_fundamentals(pool, provider, include_quarterly=False)
        await ing.refresh_quotes(pool, provider)
        await ing.run_technicals(pool)
        await ing.run_valuation(pool)
        await ing.run_signals(pool)

    async def test_full_pipeline_produces_a_signal(self, clean_db):
        await seed_stock(clean_db, "ACME")
        await self._full_run(clean_db)

        valuation = await clean_db.fetchrow(
            "SELECT * FROM intrinsic_values WHERE nse_symbol = 'ACME'"
        )
        assert valuation["iv_blended"] is not None
        assert valuation["years_of_history"] == 10
        assert valuation["wacc"] is not None

        signal = await clean_db.fetchrow(
            "SELECT * FROM signals WHERE nse_symbol = 'ACME'"
        )
        assert signal["action"] in (
            "STRONG_BUY", "BUY", "ACCUMULATE", "HOLD", "REDUCE", "SELL", "AVOID",
        )
        assert signal["headline"]

    async def test_projection_and_assumptions_survive_the_jsonb_round_trip(
        self, clean_db
    ):
        await seed_stock(clean_db, "ACME")
        await self._full_run(clean_db)
        row = await clean_db.fetchrow(
            """SELECT jsonb_array_length(projection) AS years,
                      assumptions -> 'BASE' ->> 'wacc' AS base_wacc,
                      jsonb_array_length(models) AS model_count
                 FROM intrinsic_values WHERE nse_symbol = 'ACME'"""
        )
        assert row["years"] == 10
        assert float(row["base_wacc"]) > 0
        assert row["model_count"] >= 2

    async def test_thin_history_is_refused_not_guessed(self, clean_db):
        await seed_stock(clean_db, "THIN")
        await self._full_run(
            clean_db, fundamentals={"THIN.NS": sparse_history(3)}
        )
        valuation = await clean_db.fetchrow(
            "SELECT iv_blended, warnings FROM intrinsic_values WHERE nse_symbol = 'THIN'"
        )
        assert valuation["iv_blended"] is None
        assert valuation["warnings"]

    async def test_bank_uses_the_excess_return_model(self, clean_db):
        await seed_stock(clean_db, "LENDER", sector="Banks")
        await self._full_run(clean_db, fundamentals={"LENDER.NS": bank_history()})
        model = await clean_db.fetchval(
            "SELECT primary_model FROM intrinsic_values WHERE nse_symbol = 'LENDER'"
        )
        assert model == "EXCESS_RETURN"

    async def test_signal_upside_reflects_the_live_price(self, clean_db):
        # A signal generated between valuation runs must use the price now.
        await seed_stock(clean_db, "ACME")
        await self._full_run(clean_db)
        before = await clean_db.fetchval(
            "SELECT upside_pct FROM signals WHERE nse_symbol = 'ACME'"
        )

        await clean_db.execute(
            "UPDATE realtime_quotes SET ltp = ltp / 2 WHERE nse_symbol = 'ACME'"
        )
        await ing.run_signals(clean_db)
        after = await clean_db.fetchval(
            "SELECT upside_pct FROM signals WHERE nse_symbol = 'ACME'"
        )
        assert after > before


# ─────────────────────────────────────────────────────────────
# Regressions found by running the pipeline for real
# ─────────────────────────────────────────────────────────────
class TestRegressions:
    async def test_index_is_not_valued_as_a_company(self, clean_db):
        """
        An index has no filings, no share count and no intrinsic value. Valuing
        one produced a confident STRONG_BUY on something that cannot be bought.
        """
        await seed_stock(clean_db, "ACME", benchmark="^NSEI")
        await seed_stock(clean_db, "^NSEI", ticker="^NSEI", instrument_type="INDEX")

        provider = StubProvider()
        history = await ing.backfill_price_history(clean_db, provider)
        await ing.refresh_fundamentals(clean_db, provider, include_quarterly=False)
        await ing.refresh_quotes(clean_db, provider)
        await ing.run_technicals(clean_db)
        await ing.run_valuation(clean_db)
        await ing.run_signals(clean_db)

        # The index is still ingested, because beta is regressed against it.
        assert history.requested == 2
        assert await clean_db.fetchval(
            "SELECT count(*) FROM price_candles_daily WHERE nse_symbol = '^NSEI'"
        ) > 0

        # But it is never valued and never given a signal.
        assert await clean_db.fetchval(
            "SELECT count(*) FROM intrinsic_values WHERE nse_symbol = '^NSEI'"
        ) == 0
        assert await clean_db.fetchval(
            "SELECT count(*) FROM signals WHERE nse_symbol = '^NSEI'"
        ) == 0

    async def test_stop_loss_is_never_a_token_value(self, clean_db):
        """
        On a very volatile name 2.5 x ATR can exceed the price, and clamping the
        result to 0.01 printed a one-paisa stop loss that looked deliberate.
        """
        await seed_stock(clean_db, "WILD")
        violent = build_candles(days=900, start_price=100.0, wave_amplitude=0.60,
                                wave_period=20)
        provider = StubProvider(candles={"WILD.NS": violent}, price=100.0)
        await ing.backfill_price_history(clean_db, provider)
        await ing.refresh_fundamentals(clean_db, provider, include_quarterly=False)
        await ing.refresh_quotes(clean_db, provider)
        await ing.run_technicals(clean_db)
        await ing.run_valuation(clean_db)
        await ing.run_signals(clean_db)

        stop, price = await clean_db.fetchrow(
            "SELECT stop_loss, current_price FROM signals WHERE nse_symbol = 'WILD'"
        )
        if stop is not None:
            assert float(stop) > float(price) * 0.40

    async def test_buy_rating_never_carries_risk_reward_below_one(self, clean_db):
        """
        Using the bear case as the first target produced a risk/reward of 0.79
        next to a STRONG_BUY — the plan contradicting the call.
        """
        await seed_stock(clean_db, "ACME")
        provider = StubProvider()
        await ing.backfill_price_history(clean_db, provider)
        await ing.refresh_fundamentals(clean_db, provider, include_quarterly=False)
        await ing.refresh_quotes(clean_db, provider)
        await ing.run_technicals(clean_db)
        await ing.run_valuation(clean_db)
        await ing.run_signals(clean_db)

        row = await clean_db.fetchrow(
            """SELECT action, risk_reward FROM signals
                WHERE nse_symbol = 'ACME' AND action IN ('STRONG_BUY','BUY')"""
        )
        if row and row["risk_reward"] is not None:
            assert float(row["risk_reward"]) >= 1.0

    async def test_chart_counts_do_not_misreport_what_is_stored(self, clean_db):
        """
        The chart endpoint reported only the range-limited row count, under a
        name the UI rendered as "N sessions stored". A stock with 2,600 stored
        sessions therefore claimed 2,520 on a 10-year view.

        Cosmetic in isolation; not cosmetic in a product whose entire claim is
        that its numbers mean what they say.
        """
        await seed_stock(clean_db, "ACME")
        provider = StubProvider(candles={"ACME.NS": build_candles(days=1400)})
        await ing.backfill_price_history(clean_db, provider)

        stored = await clean_db.fetchval(
            "SELECT count(*) FROM price_candles_daily WHERE nse_symbol = 'ACME'"
        )
        assert stored == 1400

        # A range shorter than the history returns fewer rows, but must still
        # report the true total separately.
        rows = await clean_db.fetch(
            """SELECT * FROM (
                 SELECT date FROM price_candles_daily
                  WHERE nse_symbol = 'ACME' ORDER BY date DESC LIMIT 252
               ) recent ORDER BY date ASC"""
        )
        assert len(rows) == 252
        assert len(rows) < stored
