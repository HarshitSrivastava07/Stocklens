"""
Technical indicator contracts.

Indicators are checked against hand-computed values where a closed form exists,
and against structural invariants everywhere else. The recurring concern is that
an indicator must never quietly substitute a shorter window when history is
short — a 50-day average mislabelled as a 200-day one is a silent lie on a chart
somebody trades from.
"""
from __future__ import annotations

from datetime import date

import pytest

from services.analytics.technicals import (
    BollingerBands,
    atr,
    bollinger,
    classify_trend,
    compute_snapshot,
    ema,
    macd,
    regress_beta_against,
    rsi,
    sma,
    true_range,
)
from services.providers.base import Candle
from tests.fixtures.companies import build_candles, falling_candles


class TestSma:
    def test_known_values(self):
        out = sma([1, 2, 3, 4, 5], 3)
        assert out == [None, None, 2.0, 3.0, 4.0]

    def test_leading_positions_are_none_not_truncated(self):
        # Returning a shorter list is how off-by-one errors reach a chart.
        out = sma(list(range(10)), 5)
        assert len(out) == 10
        assert out[:4] == [None] * 4

    def test_gap_invalidates_the_window(self):
        # A missing session must not be bridged as though it never happened.
        out = sma([1, 2, 3, None, 5, 6, 7], 3)
        assert out[2] == 2.0
        assert out[3] is None
        assert out[4] is None and out[5] is None
        assert out[6] == 6.0

    def test_rejects_non_positive_period(self):
        with pytest.raises(ValueError):
            sma([1, 2, 3], 0)


class TestEma:
    def test_seeded_with_the_first_full_sma(self):
        # Seeding with the first close leaves a visible artefact at the left edge.
        values = [10, 11, 12, 13, 14]
        out = ema(values, 3)
        assert out[:2] == [None, None]
        assert out[2] == pytest.approx(11.0)  # SMA of 10,11,12

    def test_applies_the_standard_multiplier(self):
        out = ema([10, 11, 12, 13], 3)
        # multiplier 2/(3+1) = 0.5; (13 - 11) * 0.5 + 11 = 12.0
        assert out[3] == pytest.approx(12.0)

    def test_tracks_price_more_closely_than_sma(self):
        # On a straight line the two converge exactly, so the difference only
        # shows where the series changes pace. Here price is flat then jumps.
        values = [10.0] * 30 + [20.0] * 5
        assert ema(values, 10)[-1] > sma(values, 10)[-1]

    def test_equals_sma_on_a_constant_series(self):
        values = [42.0] * 30
        assert ema(values, 10)[-1] == pytest.approx(42.0)
        assert sma(values, 10)[-1] == pytest.approx(42.0)


class TestRsi:
    def test_matches_wilder_hand_computation(self):
        # 14 changes: gains sum 18 (avg 1.285714), losses sum 5 (avg 0.357143)
        # RS = 3.6 -> RSI = 100 - 100/4.6 = 78.260869...
        closes = [100, 102, 101, 103, 105, 104, 106, 108, 107, 109, 111, 110, 112, 114, 113]
        assert rsi(closes, 14)[-1] == pytest.approx(78.260869, rel=1e-6)

    def test_unbroken_gains_reach_100(self):
        assert rsi(list(range(1, 40)), 14)[-1] == pytest.approx(100.0)

    def test_unbroken_losses_reach_0(self):
        assert rsi(list(range(40, 1, -1)), 14)[-1] == pytest.approx(0.0)

    def test_none_until_the_period_is_satisfied(self):
        out = rsi(list(range(1, 20)), 14)
        assert out[:14] == [None] * 14
        assert out[14] is not None

    def test_bounded_zero_to_hundred(self):
        for series in (build_candles(days=400), falling_candles(days=400)):
            closes = [c.close for c in series]
            for value in rsi(closes, 14):
                if value is not None:
                    assert 0.0 <= value <= 100.0


class TestMacd:
    def test_line_is_fast_minus_slow(self):
        closes = [float(v) for v in range(1, 80)]
        result = macd(closes, 12, 26, 9)
        fast, slow = ema(closes, 12), ema(closes, 26)
        for i in range(len(closes)):
            if fast[i] is not None and slow[i] is not None:
                assert result.macd[i] == pytest.approx(fast[i] - slow[i])

    def test_histogram_is_line_minus_signal(self):
        result = macd([float(v) for v in range(1, 80)])
        for line, signal, hist in zip(result.macd, result.signal, result.histogram):
            if line is not None and signal is not None:
                assert hist == pytest.approx(line - signal)

    def test_signal_is_not_shifted_by_leading_nones(self):
        # Feeding the leading Nones into the signal EMA would offset it.
        result = macd([float(v) for v in range(1, 80)])
        first_line = next(i for i, v in enumerate(result.macd) if v is not None)
        first_signal = next(i for i, v in enumerate(result.signal) if v is not None)
        assert first_signal == first_line + 8  # 9-period seed


class TestAtr:
    def test_true_range_takes_the_widest_measure(self):
        # Gap up: yesterday closed 100, today ranges 108-105.
        tr = true_range([102, 108], [98, 105], [100, 106])
        assert tr[0] == pytest.approx(4.0)  # first bar: plain range
        assert tr[1] == pytest.approx(8.0)  # high 108 vs prior close 100

    def test_atr_is_positive_and_smoothed(self):
        candles = build_candles(days=200)
        out = atr(
            [c.high for c in candles],
            [c.low for c in candles],
            [c.close for c in candles],
            14,
        )
        assert out[-1] > 0
        assert out[:13] == [None] * 13


class TestBollinger:
    def test_bands_straddle_the_middle(self):
        candles = build_candles(days=200)
        closes = [c.close for c in candles]
        bands = bollinger(closes, 20, 2.0)
        assert bands.upper[-1] > bands.middle[-1] > bands.lower[-1]

    def test_percent_b_is_position_within_the_bands(self):
        closes = [c.close for c in build_candles(days=200)]
        bands = bollinger(closes, 20)
        pb = bands.percent_b[-1]
        expected = (closes[-1] - bands.lower[-1]) / (bands.upper[-1] - bands.lower[-1])
        assert pb == pytest.approx(expected)


class TestSnapshot:
    def test_full_history_populates_every_window(self):
        snap = compute_snapshot(build_candles(days=1400))
        assert snap.sma_20 and snap.sma_50 and snap.sma_200
        assert snap.rsi_14 is not None
        assert snap.atr_14 and snap.atr_pct
        assert snap.return_1y is not None
        assert snap.return_3y_cagr is not None
        assert snap.candles_used == 1400

    def test_short_history_leaves_long_windows_null_and_says_so(self):
        # A stock listed three months ago has no 200-day average. Substituting a
        # shorter window would mislabel it.
        snap = compute_snapshot(build_candles(days=60))
        assert snap.sma_20 is not None
        assert snap.sma_200 is None
        assert snap.return_1y is None
        assert snap.return_3y_cagr is None
        assert any("sessions of history" in w for w in snap.warnings)

    def test_empty_history_is_handled(self):
        snap = compute_snapshot([])
        assert snap.price is None
        assert snap.trend == "UNKNOWN"
        assert snap.warnings

    def test_52_week_range_comes_from_traded_prices(self):
        candles = build_candles(days=400)
        snap = compute_snapshot(candles)
        recent = [c.close for c in candles[-252:]]
        assert snap.week_52_high == pytest.approx(max(recent))
        assert snap.week_52_low == pytest.approx(min(recent))

    def test_serializes_to_primitives(self):
        import json

        json.dumps(compute_snapshot(build_candles(days=400)).as_dict())


class TestTrend:
    def test_rising_series_is_an_uptrend(self):
        assert compute_snapshot(build_candles(days=1300)).trend in (
            "UPTREND",
            "STRONG_UPTREND",
        )

    def test_falling_series_is_a_downtrend(self):
        assert compute_snapshot(falling_candles(days=600)).trend in (
            "DOWNTREND",
            "STRONG_DOWNTREND",
        )

    def test_unknown_without_enough_history(self):
        from services.analytics.technicals import TechnicalSnapshot

        assert classify_trend(TechnicalSnapshot(price=100.0)) == "UNKNOWN"


class TestBeta:
    def test_regresses_against_an_index(self):
        stock = build_candles(days=800, wave_amplitude=0.20)
        index = build_candles(days=800, start_price=20000.0, wave_amplitude=0.10)
        value, n = regress_beta_against(stock, index)
        assert value is not None and n > 100
        # Twice the amplitude on the same cycle is roughly twice the sensitivity.
        assert 1.2 < value < 3.0

    def test_aligns_on_shared_dates_only(self):
        # A naive zip would pair the stock's Monday with the index's Tuesday
        # whenever one market took a holiday the other did not.
        stock = build_candles(days=300)
        index = [c for i, c in enumerate(build_candles(days=300)) if i % 7]
        value, n = regress_beta_against(stock, index)
        assert n <= 300
        assert value is not None

    def test_refuses_when_overlap_is_too_short(self):
        stock = build_candles(days=20)
        index = build_candles(days=20, start_price=5000.0)
        value, n = regress_beta_against(stock, index)
        assert value is None
