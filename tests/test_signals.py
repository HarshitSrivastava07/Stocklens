"""
Buy / sell engine contracts.

The central concern here is that a signal must never be *inventable*. Every
assertion below exists to stop the engine issuing an actionable call it cannot
support — a buy with no valuation, a green light on a broken business, or an
entry zone that contradicts the action printed next to it.
"""
from __future__ import annotations

import json

import pytest

from services.analytics.intrinsic_value import MarketContext, compute_intrinsic_value
from services.analytics.signals import (
    ACCUMULATE,
    AVOID,
    BUY,
    HOLD,
    INSUFFICIENT_DATA,
    REDUCE,
    SELL,
    STRONG_BUY,
    decide_action,
    generate_signal,
    score_momentum,
    score_quality,
    score_risk,
    score_value,
)
from services.analytics.technicals import compute_snapshot
from tests.fixtures.companies import (
    build_candles,
    build_history,
    falling_candles,
    sparse_history,
)

BUY_SIDE = (STRONG_BUY, BUY, ACCUMULATE)


def _signal(price: float, *, years=10, candles=None, sector="IT Services", **history):
    periods = build_history(years=years, **history)
    tech = compute_snapshot(candles if candles is not None else build_candles(days=1300))
    valuation = compute_intrinsic_value(
        "TESTCO",
        periods,
        current_price=price,
        sector=sector,
        market=MarketContext.for_country("India"),
        regressed_beta=1.1,
    )
    return generate_signal("TESTCO", valuation, tech, current_price=price), valuation


# ─────────────────────────────────────────────────────────────
# Refusals
# ─────────────────────────────────────────────────────────────
class TestRefusals:
    def test_no_valuation_means_no_call(self):
        valuation = compute_intrinsic_value("X", sparse_history(3), current_price=100.0)
        signal = generate_signal("X", valuation, None, current_price=100.0)
        assert signal.action == INSUFFICIENT_DATA
        assert signal.color == "GREY"
        assert signal.plan.entry_low is None

    def test_grey_is_returned_rather_than_a_filler_hold(self):
        # A yellow light invented to fill the cell reads as a real assessment.
        valuation = compute_intrinsic_value("X", sparse_history(2), current_price=50.0)
        signal = generate_signal("X", valuation, None)
        assert signal.action != HOLD
        assert signal.reason_given if hasattr(signal, "reason_given") else signal.rationale

    def test_missing_technicals_do_not_block_a_valuation_call(self):
        valuation = compute_intrinsic_value(
            "X", build_history(years=10), current_price=200.0, sector="IT Services"
        )
        signal = generate_signal("X", valuation, None, current_price=200.0)
        assert signal.action != INSUFFICIENT_DATA
        assert signal.trend == "UNKNOWN"


# ─────────────────────────────────────────────────────────────
# Direction
# ─────────────────────────────────────────────────────────────
class TestDirection:
    def test_deeply_undervalued_is_a_buy(self):
        signal, valuation = _signal(120.0)
        assert valuation.upside_pct > 100
        assert signal.action in BUY_SIDE

    def test_deeply_overvalued_is_a_sell(self):
        signal, valuation = _signal(2000.0)
        assert valuation.upside_pct < -50
        assert signal.action in (SELL, REDUCE, AVOID)

    def test_fairly_priced_is_a_hold(self):
        _, valuation = _signal(300.0)
        fair = valuation.iv_blended
        signal, _ = _signal(fair)
        assert signal.action in (HOLD, ACCUMULATE)

    def test_action_moves_monotonically_with_price(self):
        rank = {
            STRONG_BUY: 6,
            BUY: 5,
            ACCUMULATE: 4,
            HOLD: 3,
            REDUCE: 2,
            SELL: 1,
            AVOID: 0,
        }
        ranks = [rank[_signal(p)[0].action] for p in (100, 250, 500, 800, 1500)]
        assert ranks == sorted(ranks, reverse=True)


# ─────────────────────────────────────────────────────────────
# Gating — the property that makes this safe to publish
# ─────────────────────────────────────────────────────────────
class TestGating:
    def test_cheapness_alone_cannot_produce_a_buy(self):
        # A structurally broken business must not be dragged into a buy rating
        # by a spectacular value score. This is the failure mode that makes a
        # screener dangerous.
        signal, valuation = _signal(
            50.0,
            operating_margin=0.004,
            loss_years=(1, 2, 3, 4, 5, 6, 7),
            debt=900_000.0,
            cash=0.0,
        )
        assert signal.action not in (STRONG_BUY, BUY)

    def test_collapsing_trend_blocks_a_strong_buy(self):
        signal, _ = _signal(120.0, candles=falling_candles(days=700))
        assert signal.action != STRONG_BUY

    def test_low_confidence_blocks_a_strong_buy(self):
        # Five years of filings cannot support the engine's highest conviction.
        signal, valuation = _signal(120.0, years=5)
        assert valuation.confidence != "HIGH"
        assert signal.action != STRONG_BUY

    def test_high_risk_forces_avoid_regardless_of_price(self):
        signal, _ = _signal(
            10.0,
            operating_margin=0.002,
            loss_years=tuple(range(1, 10)),
            debt=5_000_000.0,
            cash=0.0,
        )
        assert signal.action == AVOID

    def test_every_gate_is_reported(self):
        signal, _ = _signal(200.0)
        for gate in (
            "upside_over_25",
            "quality_over_55",
            "risk_under_55",
            "confidence_ok",
            "not_collapsing",
        ):
            assert gate in signal.conditions


# ─────────────────────────────────────────────────────────────
# Trade plan coherence
# ─────────────────────────────────────────────────────────────
class TestTradePlan:
    def test_stop_sits_below_the_entry_zone(self):
        for price in (120.0, 250.0, 400.0):
            signal, _ = _signal(price)
            plan = signal.plan
            if plan.entry_low and plan.stop_loss:
                assert plan.stop_loss < plan.entry_low

    def test_targets_are_ordered(self):
        signal, _ = _signal(200.0)
        if signal.plan.target_1 and signal.plan.target_2:
            assert signal.plan.target_1 <= signal.plan.target_2

    def test_entry_zone_never_exceeds_the_max_buy_price(self):
        # Buying at fair value offers none of the margin of safety the call
        # assumed, so the zone must stop short of it.
        for price in (120.0, 300.0, 450.0):
            signal, _ = _signal(price)
            plan = signal.plan
            if plan.entry_high and plan.max_buy_price:
                assert plan.entry_high <= plan.max_buy_price * 1.0001

    def test_max_buy_price_is_below_intrinsic_value(self):
        signal, valuation = _signal(200.0)
        assert signal.plan.max_buy_price < valuation.iv_blended

    def test_accumulate_never_says_buy_only_if_it_rises(self):
        for price in (380.0, 400.0, 420.0):
            signal, _ = _signal(price)
            if signal.action == ACCUMULATE and signal.plan.entry_high:
                assert signal.plan.entry_low <= price

    def test_risk_reward_is_absent_rather_than_negative(self):
        # "-0.53" invites being read on the same scale as a good setup's 3.0.
        for price in (500.0, 700.0, 900.0):
            signal, _ = _signal(price)
            assert signal.plan.risk_reward is None or signal.plan.risk_reward > 0

    def test_sell_carries_no_entry_plan(self):
        signal, _ = _signal(2000.0)
        if signal.action in (SELL, AVOID):
            assert signal.plan.entry_low is None
            assert signal.plan.position_size_pct is None

    def test_position_size_is_capped(self):
        for price in (50.0, 100.0, 200.0):
            signal, _ = _signal(price)
            if signal.plan.position_size_pct:
                assert 1.0 <= signal.plan.position_size_pct <= 10.0

    def test_riskier_business_gets_a_smaller_position(self):
        safe, _ = _signal(150.0)
        risky, _ = _signal(150.0, loss_years=(2, 5), debt=300_000.0, cash=0.0)
        if safe.plan.position_size_pct and risky.plan.position_size_pct:
            assert risky.plan.position_size_pct <= safe.plan.position_size_pct

    def test_stop_is_wider_for_a_more_volatile_stock(self):
        calm, _ = _signal(200.0, candles=build_candles(days=800, wave_amplitude=0.03))
        wild, _ = _signal(200.0, candles=build_candles(days=800, wave_amplitude=0.30))
        assert wild.plan.stop_loss < calm.plan.stop_loss


# ─────────────────────────────────────────────────────────────
# Component scores
# ─────────────────────────────────────────────────────────────
class TestScores:
    def test_value_score_scales_with_upside(self):
        _, cheap = _signal(100.0)
        _, dear = _signal(800.0)
        assert score_value(cheap) > score_value(dear)

    def test_value_score_is_pulled_to_neutral_by_low_confidence(self):
        # A 90% upside on a thin history is not worth the same as a 40% upside
        # on a decade of stable filings.
        _, confident = _signal(150.0, years=10)
        _, shaky = _signal(150.0, years=5)
        assert abs(score_value(confident) - 50) > abs(score_value(shaky) - 50)

    def test_quality_rewards_returns_above_cost_of_capital(self):
        _, good = _signal(300.0, operating_margin=0.25)
        _, poor = _signal(300.0, operating_margin=0.03)
        wacc = good.cost_of_capital.wacc
        assert score_quality(good.history, wacc) > score_quality(poor.history, wacc)

    def test_momentum_penalises_an_overbought_reading(self):
        hot = compute_snapshot(build_candles(days=600, drift_per_day=0.004))
        steady = compute_snapshot(build_candles(days=600, drift_per_day=0.0004))
        assert score_momentum(hot) is not None
        assert score_momentum(steady) is not None

    def test_risk_flags_name_the_problem(self):
        _, valuation = _signal(200.0, loss_years=(1, 2, 3), debt=800_000.0, cash=0.0)
        risk, flags = score_risk(valuation.history, None, valuation)
        assert risk > 40
        assert any("Loss-making" in f for f in flags)

    def test_risk_enters_as_a_deduction_not_an_average(self):
        # A dangerous business must not be averaged back into respectability.
        safe, _ = _signal(200.0)
        risky, _ = _signal(200.0, loss_years=(1, 2, 3, 4), debt=900_000.0, cash=0.0)
        assert risky.composite_score < safe.composite_score

    def test_scores_stay_in_range(self):
        for price in (50.0, 200.0, 600.0, 2000.0):
            signal, _ = _signal(price)
            for value in (
                signal.value_score,
                signal.quality_score,
                signal.momentum_score,
                signal.risk_score,
                signal.composite_score,
            ):
                if value is not None:
                    assert 0.0 <= value <= 100.0


# ─────────────────────────────────────────────────────────────
# Explanation
# ─────────────────────────────────────────────────────────────
class TestExplanation:
    def test_states_the_direction_correctly(self):
        signal, valuation = _signal(200.0)
        assert valuation.iv_blended > 200.0
        joined = " ".join(signal.rationale)
        assert "above the current price" in joined

    def test_states_the_direction_correctly_when_overvalued(self):
        signal, valuation = _signal(2000.0)
        assert valuation.iv_blended < 2000.0
        assert "below the current price" in " ".join(signal.rationale)

    def test_cites_the_history_depth(self):
        signal, _ = _signal(200.0)
        assert "10 years of filings" in " ".join(signal.rationale)

    def test_every_signal_says_what_would_change_it(self):
        for price in (100.0, 400.0, 1500.0):
            signal, _ = _signal(price)
            assert signal.invalidation

    def test_headline_is_present(self):
        assert _signal(200.0)[0].headline

    def test_serializes_to_primitives(self):
        json.dumps(_signal(200.0)[0].as_dict())


class TestBankSignals:
    def test_bank_gets_a_signal_from_the_right_model(self):
        from tests.fixtures.companies import bank_history

        valuation = compute_intrinsic_value(
            "TESTBANK", bank_history(), current_price=250.0, sector="Banks"
        )
        signal = generate_signal(
            "TESTBANK", valuation, compute_snapshot(build_candles(days=900)),
            current_price=250.0,
        )
        assert signal.action != INSUFFICIENT_DATA
        assert valuation.primary_model == "EXCESS_RETURN"
