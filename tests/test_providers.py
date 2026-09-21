"""Provider parsing contracts. No network: parsers are tested against recorded shapes."""
from __future__ import annotations

import asyncio
from datetime import date

import pytest

from services.providers.base import (
    AsyncRateLimiter,
    FinancialPeriod,
    NotFound,
    MalformedResponse,
    RateLimited,
    UpstreamUnavailable,
    epoch_to_date,
    retry_async,
    to_float,
    to_int,
)
from services.providers.yahoo import YahooProvider
from tests.fixtures.yahoo_payloads import (
    chart_history_payload,
    chart_quote_payload,
    empty_chart_payload,
    error_chart_payload,
    fundamentals_payload,
)


# ─────────────────────────────────────────────────────────────
# Coercion helpers
# ─────────────────────────────────────────────────────────────
class TestCoercion:
    def test_unwraps_yahoo_raw_dict(self):
        assert to_float({"raw": 12.5, "fmt": "12.50"}) == 12.5

    def test_rejects_nan_and_inf(self):
        # A NaN reaching the DCF produces a NaN intrinsic value on screen.
        assert to_float(float("nan")) is None
        assert to_float(float("inf")) is None
        assert to_float(float("-inf")) is None

    def test_rejects_bool(self):
        # bool is an int subclass; True must not silently become 1.0.
        assert to_float(True) is None

    def test_handles_numeric_strings(self):
        assert to_float("42.75") == 42.75
        assert to_int("42.75") == 42

    def test_none_and_garbage(self):
        assert to_float(None) is None
        assert to_float("not a number") is None
        assert to_float({}) is None

    def test_epoch_conversion(self):
        assert epoch_to_date(1704412800) == date(2024, 1, 5)
        assert epoch_to_date(None) is None
        assert epoch_to_date("garbage") is None


# ─────────────────────────────────────────────────────────────
# Quote parsing
# ─────────────────────────────────────────────────────────────
class TestQuoteParsing:
    def test_parses_price_and_change(self):
        q = YahooProvider._parse_quote(
            chart_quote_payload(), "RELIANCE.NS", "RELIANCE"
        )
        assert q.symbol == "RELIANCE"
        assert q.price == 2945.50
        assert q.previous_close == 2910.25
        assert q.currency == "INR"
        assert q.volume == 8_452_113
        assert q.change_abs == pytest.approx(35.25)
        assert q.change_pct == pytest.approx(35.25 / 2910.25 * 100)

    def test_falls_back_to_indicator_arrays_when_meta_is_null(self):
        # Outside market hours Yahoo nulls regularMarketOpen/DayHigh/DayLow.
        q = YahooProvider._parse_quote(
            chart_quote_payload(null_meta_ohlc=True), "RELIANCE.NS", "RELIANCE"
        )
        assert q.open == 2915.0
        assert q.day_high == 2951.8
        assert q.day_low == 2908.1

    def test_change_is_none_without_previous_close(self):
        # Never fabricate a 0% move out of a missing baseline.
        q = YahooProvider._parse_quote(
            chart_quote_payload(prev_close=None), "RELIANCE.NS", "RELIANCE"
        )
        assert q.previous_close is None
        assert q.change_abs is None
        assert q.change_pct is None

    def test_delisted_symbol_raises_not_found(self):
        with pytest.raises(NotFound):
            YahooProvider._parse_quote(error_chart_payload(), "DEAD.NS", "DEAD")

    def test_empty_result_raises_not_found(self):
        with pytest.raises(NotFound):
            YahooProvider._parse_quote(empty_chart_payload(), "X.NS", "X")

    def test_zero_price_is_rejected(self):
        with pytest.raises(MalformedResponse):
            YahooProvider._parse_quote(
                chart_quote_payload(price=0.0), "X.NS", "X"
            )


# ─────────────────────────────────────────────────────────────
# History parsing
# ─────────────────────────────────────────────────────────────
class TestHistoryParsing:
    def test_drops_halted_sessions(self):
        candles = YahooProvider._parse_candles(chart_history_payload(), "AAPL")
        # Five timestamps, one all-null halt row -> four usable bars.
        assert len(candles) == 4
        assert all(c.close is not None for c in candles)
        assert date(2024, 1, 9) not in [c.date for c in candles]

    def test_ordered_oldest_first(self):
        candles = YahooProvider._parse_candles(chart_history_payload(), "AAPL")
        assert [c.date for c in candles] == sorted(c.date for c in candles)
        assert candles[0].date == date(2024, 1, 5)

    def test_keeps_adjusted_close_separate_from_raw(self):
        candles = YahooProvider._parse_candles(chart_history_payload(), "AAPL")
        first = candles[0]
        assert first.close == 181.18
        assert first.adj_close == 180.32
        assert first.adj_close != first.close

    def test_volume_and_ohlc_present(self):
        candles = YahooProvider._parse_candles(chart_history_payload(), "AAPL")
        c = candles[0]
        assert (c.open, c.high, c.low) == (181.99, 182.76, 180.17)
        assert c.volume == 62_303_300


# ─────────────────────────────────────────────────────────────
# Fundamentals parsing
# ─────────────────────────────────────────────────────────────
class TestFundamentalsParsing:
    def _collect(self, prefix="annual"):
        into: dict = {}
        currency = YahooProvider._merge_timeseries(
            fundamentals_payload(prefix), prefix, into
        )
        return into, currency

    def test_extracts_every_period(self):
        into, currency = self._collect()
        assert sorted(into) == [
            date(2022, 9, 30),
            date(2023, 9, 30),
            date(2024, 9, 30),
        ]
        assert currency == "USD"

    def test_maps_yahoo_names_to_model_fields(self):
        into, _ = self._collect()
        fy24 = into[date(2024, 9, 30)]
        assert fy24["revenue"] == 391_035e6
        assert fy24["net_income"] == 93_736e6
        assert fy24["equity"] == 56_950e6
        assert fy24["cfo"] == 118_254e6
        assert fy24["eps_diluted"] == 6.08

    def test_capex_stored_as_positive_magnitude(self):
        # Source reports -9.447B; downstream ratios expect a magnitude.
        into, _ = self._collect()
        assert into[date(2024, 9, 30)]["capex"] == 9_447e6

    def test_quarterly_prefix_is_honoured(self):
        into, _ = self._collect("quarterly")
        assert into[date(2024, 9, 30)]["revenue"] == 391_035e6

    def test_unknown_types_are_ignored_not_crashed(self):
        payload = fundamentals_payload()
        payload["timeseries"]["result"].append(
            {
                "meta": {"symbol": ["AAPL"], "type": ["annualSomeFieldWeDoNotModel"]},
                "annualSomeFieldWeDoNotModel": [
                    {"asOfDate": "2024-09-30", "reportedValue": {"raw": 1.0}}
                ],
            }
        )
        into: dict = {}
        YahooProvider._merge_timeseries(payload, "annual", into)
        assert "some_field" not in into[date(2024, 9, 30)]

    def test_derived_properties_on_parsed_period(self):
        into, _ = self._collect()
        fy24 = FinancialPeriod(
            period_end=date(2024, 9, 30), period_type="A", **into[date(2024, 9, 30)]
        )
        # EBITDA 134.661B / revenue 391.035B
        assert fy24.ebitda_margin == pytest.approx(0.34437, rel=1e-4)
        # tax 29.749B / pretax 123.485B
        assert fy24.effective_tax_rate == pytest.approx(0.24092, rel=1e-4)
        # debt 119.059B - cash 29.943B
        assert fy24.net_debt == pytest.approx(89_116e6)

    def test_effective_tax_rate_rejects_absurd_values(self):
        # A one-off credit or writeback can produce a nonsense rate; it must not
        # be fed into a DCF.
        p = FinancialPeriod(
            period_end=date(2024, 3, 31),
            period_type="A",
            pretax_income=100.0,
            tax_provision=95.0,
        )
        assert p.effective_tax_rate is None

    def test_loss_making_year_has_no_tax_rate(self):
        p = FinancialPeriod(
            period_end=date(2024, 3, 31),
            period_type="A",
            pretax_income=-50.0,
            tax_provision=2.0,
        )
        assert p.effective_tax_rate is None


# ─────────────────────────────────────────────────────────────
# Rate limiting and retry
# ─────────────────────────────────────────────────────────────
class TestRateLimiter:
    async def test_burst_then_throttle(self):
        limiter = AsyncRateLimiter(rate_per_sec=50.0, burst=5)
        loop = asyncio.get_running_loop()
        start = loop.time()
        for _ in range(5):
            await limiter.acquire()
        assert loop.time() - start < 0.05  # burst is immediate

        start = loop.time()
        for _ in range(5):
            await limiter.acquire()
        # 5 more at 50/s must take at least ~0.1s
        assert loop.time() - start >= 0.08


class TestRetry:
    async def test_retries_then_succeeds(self):
        calls = {"n": 0}

        async def flaky():
            calls["n"] += 1
            if calls["n"] < 3:
                raise UpstreamUnavailable("boom")
            return "ok"

        out = await retry_async(flaky, attempts=4, base_delay=0.001)
        assert out == "ok"
        assert calls["n"] == 3

    async def test_does_not_retry_not_found(self):
        # Re-asking for a delisted symbol only burns the rate budget.
        calls = {"n": 0}

        async def missing():
            calls["n"] += 1
            raise NotFound("gone")

        with pytest.raises(NotFound):
            await retry_async(missing, attempts=4, base_delay=0.001)
        assert calls["n"] == 1

    async def test_raises_after_exhausting_attempts(self):
        async def always_fails():
            raise RateLimited("slow down", retry_after=0.001)

        with pytest.raises(RateLimited):
            await retry_async(always_fails, attempts=3, base_delay=0.001)
