"""
Valuation engine contracts.

Two kinds of test here:

  * **Known-answer** — a DCF computed by hand, to the cent, so the arithmetic is
    pinned rather than merely self-consistent.
  * **Property** — invariants that must hold for any input (value rises with
    growth, falls with discount rate, refuses rather than guesses).
"""
from __future__ import annotations

from datetime import date

import pytest

from services.analytics.intrinsic_value import (
    MIN_SPREAD_OVER_G,
    MIN_YEARS_REQUIRED,
    Assumptions,
    MarketContext,
    build_assumptions,
    build_history_profile,
    compute_cost_of_capital,
    compute_intrinsic_value,
    dcf_value_per_share,
    earnings_power_value,
    excess_return_value,
    graham_number,
    is_financial,
    project_fcff,
    reverse_dcf,
    score_confidence,
    terminal_value,
)
from services.providers.base import FinancialPeriod
from tests.fixtures.companies import bank_history, build_history, sparse_history


def _assumptions(**overrides) -> Assumptions:
    base = dict(
        scenario="TEST",
        growth_initial=0.10,
        growth_terminal=0.05,
        operating_margin=0.20,
        tax_rate=0.25,
        sales_to_capital=2.0,
        wacc=0.10,
        terminal_roic=0.125,
        projection_years=2,
    )
    base.update(overrides)
    return Assumptions(**base)


# ─────────────────────────────────────────────────────────────
# Known-answer arithmetic
# ─────────────────────────────────────────────────────────────
class TestDcfKnownAnswer:
    """
    Hand-computed two-year case.

      Y1: g=10%  rev=1100  ebit=220  nopat=165  reinv=100/2=50   fcff=115
          pv = 115/1.10 = 104.545454...
      Y2: g=5%   rev=1155  ebit=231  nopat=173.25 reinv=55/2=27.5 fcff=145.75
          pv = 145.75/1.21 = 120.454545...
      PV(explicit) = 225.00 exactly

      Terminal: spread = 10% - 5% = 5%; reinvestment rate = g/ROIC = 0.05/0.125 = 40%
          NOPAT(n+1) = 173.25 x 1.05 = 181.9125
          FCFF(n+1)  = 181.9125 x 0.60 = 109.1475
          TV         = 109.1475 / 0.05 = 2182.95
          PV(TV)     = 2182.95 / 1.21 = 1804.090909...

      EV = 2029.090909;  equity = EV - 200 net debt = 1829.090909
      IV/share on 100 shares = 18.290909...
    """

    def test_projection_matches_hand_computation(self):
        proj = project_fcff(1000.0, _assumptions())
        assert len(proj) == 2
        assert proj[0].growth == pytest.approx(0.10)
        assert proj[0].fcff == pytest.approx(115.0)
        assert proj[0].present_value == pytest.approx(104.545454, rel=1e-6)
        assert proj[1].growth == pytest.approx(0.05)
        assert proj[1].fcff == pytest.approx(145.75)
        assert proj[1].present_value == pytest.approx(120.454545, rel=1e-6)
        assert sum(p.present_value for p in proj) == pytest.approx(225.0)

    def test_terminal_value_matches_hand_computation(self):
        proj = project_fcff(1000.0, _assumptions())
        tv, pv = terminal_value(proj[-1], _assumptions())
        assert tv == pytest.approx(2182.95)
        assert pv == pytest.approx(1804.090909, rel=1e-6)

    def test_value_per_share_matches_hand_computation(self):
        value, proj, tv_share = dcf_value_per_share(
            1000.0, _assumptions(), net_debt=200.0, shares=100.0
        )
        assert value == pytest.approx(18.290909, rel=1e-6)
        assert tv_share == pytest.approx(1804.090909 / 2029.090909, rel=1e-6)

    def test_growth_fades_linearly_across_the_window(self):
        proj = project_fcff(1000.0, _assumptions(projection_years=5))
        growths = [p.growth for p in proj]
        # 10% down to 5% in four equal steps of 1.25pp.
        assert growths == pytest.approx([0.10, 0.0875, 0.075, 0.0625, 0.05])

    def test_reinvestment_is_funded_by_sales_to_capital(self):
        # Growing revenue by 100 with a 2.0 sales-to-capital ratio costs 50.
        proj = project_fcff(1000.0, _assumptions())
        assert proj[0].reinvestment == pytest.approx(50.0)
        assert proj[0].fcff == pytest.approx(proj[0].nopat - proj[0].reinvestment)


class TestTerminalValueDiscipline:
    def test_refuses_when_wacc_barely_exceeds_growth(self):
        # Spread of 0.5pp makes the Gordon formula explode; refusing is correct.
        proj = project_fcff(1000.0, _assumptions(wacc=0.055))
        assert terminal_value(proj[-1], _assumptions(wacc=0.055)) is None

    def test_terminal_growth_is_paid_for_out_of_nopat(self):
        # Doubling terminal ROIC halves the reinvestment drag, raising the TV.
        proj = project_fcff(1000.0, _assumptions())
        cheap_growth, _ = terminal_value(proj[-1], _assumptions(terminal_roic=0.25))
        dear_growth, _ = terminal_value(proj[-1], _assumptions(terminal_roic=0.0625))
        assert cheap_growth > dear_growth

    def test_zero_terminal_growth_needs_no_reinvestment(self):
        a = _assumptions(growth_terminal=0.0)
        proj = project_fcff(1000.0, a)
        tv, _ = terminal_value(proj[-1], a)
        # FCFF(n+1) == NOPAT(n+1) when nothing is reinvested.
        assert tv == pytest.approx(proj[-1].nopat / a.wacc)


# ─────────────────────────────────────────────────────────────
# Monotonicity — the model must behave like a valuation
# ─────────────────────────────────────────────────────────────
class TestMonotonicity:
    def _value(self, **overrides) -> float:
        return dcf_value_per_share(
            1000.0, _assumptions(**overrides), net_debt=200.0, shares=100.0
        )[0]

    def test_value_rises_with_growth(self):
        assert self._value(growth_initial=0.15) > self._value(growth_initial=0.05)

    def test_value_falls_as_discount_rate_rises(self):
        assert self._value(wacc=0.09) > self._value(wacc=0.15)

    def test_value_rises_with_margin(self):
        assert self._value(operating_margin=0.25) > self._value(operating_margin=0.12)

    def test_value_falls_as_tax_rises(self):
        assert self._value(tax_rate=0.15) > self._value(tax_rate=0.40)

    def test_capital_hungry_growth_is_worth_less(self):
        # Same growth, but needing twice the capital to fund it.
        assert self._value(sales_to_capital=4.0) > self._value(sales_to_capital=1.0)

    def test_net_debt_reduces_equity_value_one_for_one(self):
        light = dcf_value_per_share(1000.0, _assumptions(), net_debt=0.0, shares=100.0)[0]
        heavy = dcf_value_per_share(1000.0, _assumptions(), net_debt=500.0, shares=100.0)[0]
        assert light - heavy == pytest.approx(5.0)  # 500 spread over 100 shares


# ─────────────────────────────────────────────────────────────
# History profiling recovers what the company actually did
# ─────────────────────────────────────────────────────────────
class TestHistoryProfile:
    def test_recovers_every_planted_parameter(self):
        profile = build_history_profile(
            build_history(
                years=10,
                growth=0.12,
                operating_margin=0.18,
                tax_rate=0.25,
                capital_turnover=1.5,
                interest_rate=0.08,
                debt=40_000.0,
                cash=15_000.0,
            )
        )
        assert profile.years == 10
        assert profile.revenue_cagr == pytest.approx(0.12, rel=1e-3)
        assert profile.operating_margin_median == pytest.approx(0.18, rel=1e-3)
        assert profile.effective_tax_rate == pytest.approx(0.25, rel=1e-3)
        assert profile.sales_to_capital == pytest.approx(1.5, rel=1e-3)
        assert profile.cost_of_debt == pytest.approx(0.08, rel=1e-3)
        assert profile.net_debt == pytest.approx(25_000.0)

    def test_detects_a_genuine_margin_trend(self):
        improving = build_history_profile(
            build_history(years=10, operating_margin=0.10, margin_drift=0.005)
        )
        flat = build_history_profile(build_history(years=10, operating_margin=0.10))
        assert improving.operating_margin_trend > 0
        assert abs(flat.operating_margin_trend) < 1e-6

    def test_counts_loss_years(self):
        profile = build_history_profile(build_history(years=10, loss_years=(3, 7)))
        assert profile.profitable_years == 8

    def test_dilution_is_measured(self):
        periods = build_history(years=6)
        for i, period in enumerate(periods):
            period.shares_outstanding = 1000.0 * (1.05**i)  # 5%/yr issuance
        profile = build_history_profile(periods)
        assert profile.share_count_cagr == pytest.approx(0.05, rel=1e-3)

    def test_quarterly_periods_are_excluded(self):
        periods = build_history(years=5)
        periods.append(
            FinancialPeriod(period_end=date(2024, 6, 30), period_type="Q", revenue=1.0)
        )
        assert build_history_profile(periods).years == 5


# ─────────────────────────────────────────────────────────────
# Cost of capital
# ─────────────────────────────────────────────────────────────
class TestCostOfCapital:
    def _coc(self, **kwargs):
        profile = build_history_profile(build_history(years=10))
        market = MarketContext(risk_free_rate=0.07, equity_risk_premium=0.058)
        params = dict(market_cap=300_000.0, regressed_beta=1.1, beta_source="regressed")
        params.update(kwargs)
        return compute_cost_of_capital(profile, market, **params)

    def test_capm_cost_of_equity(self):
        coc = self._coc()
        # 7% + 1.1 x 5.8% = 13.38%
        assert coc.cost_of_equity == pytest.approx(0.1338, rel=1e-4)

    def test_cost_of_debt_comes_from_interest_actually_paid(self):
        coc = self._coc()
        assert coc.cost_of_debt_pretax == pytest.approx(0.08, rel=1e-3)
        assert coc.cost_of_debt_after_tax == pytest.approx(0.08 * 0.75, rel=1e-3)

    def test_weights_use_market_value_of_equity(self):
        coc = self._coc(market_cap=360_000.0)
        # Equity 360k against 40k of debt.
        assert coc.equity_weight == pytest.approx(0.9)
        assert coc.debt_weight == pytest.approx(0.1)

    def test_wacc_is_the_weighted_average(self):
        coc = self._coc(market_cap=360_000.0)
        expected = 0.9 * coc.cost_of_equity + 0.1 * coc.cost_of_debt_after_tax
        assert coc.wacc == pytest.approx(expected, rel=1e-6)

    def test_higher_beta_costs_more(self):
        assert self._coc(regressed_beta=1.8).wacc > self._coc(regressed_beta=0.6).wacc

    def test_extreme_beta_is_bounded(self):
        # One volatile stretch must not set a decade of discounting.
        assert self._coc(regressed_beta=9.0).beta == pytest.approx(2.5)
        assert self._coc(regressed_beta=0.01).beta == pytest.approx(0.3)

    def test_falls_back_to_default_beta_and_says_so(self):
        coc = self._coc(regressed_beta=None, beta_source="default")
        assert coc.beta_source == "default"
        assert coc.beta == pytest.approx(1.0)


# ─────────────────────────────────────────────────────────────
# Refusals — the engine must decline rather than invent
# ─────────────────────────────────────────────────────────────
class TestRefusals:
    def test_refuses_short_history(self):
        result = compute_intrinsic_value(
            "SHORT", sparse_history(3), current_price=100.0
        )
        assert result.ok is False
        assert result.iv_blended is None
        assert str(MIN_YEARS_REQUIRED) in result.reason

    def test_refuses_without_share_count(self):
        periods = build_history(years=10)
        for period in periods:
            period.shares_outstanding = None
        result = compute_intrinsic_value("NOSHARES", periods, current_price=100.0)
        assert result.ok is False
        assert "share count" in result.reason.lower()

    def test_refuses_when_no_model_can_be_computed(self):
        periods = build_history(years=10)
        for period in periods:
            period.operating_income = None
            period.ebitda = None
            period.net_income = None
            period.eps_diluted = None
            period.equity = None
        result = compute_intrinsic_value("EMPTY", periods, current_price=100.0)
        assert result.ok is False

    def test_never_returns_a_negative_intrinsic_value(self):
        # A deeply indebted, barely profitable company should refuse, not print
        # a negative price target.
        periods = build_history(
            years=10, operating_margin=0.01, debt=5_000_000.0, cash=0.0
        )
        result = compute_intrinsic_value("LEVERED", periods, current_price=100.0)
        assert result.iv_blended is None or result.iv_blended > 0


# ─────────────────────────────────────────────────────────────
# End-to-end
# ─────────────────────────────────────────────────────────────
class TestEndToEnd:
    def _value(self, **kwargs):
        params = dict(
            current_price=300.0,
            market=MarketContext.for_country("India"),
            sector="IT Services",
            regressed_beta=1.1,
            beta_source="regressed",
        )
        params.update(kwargs)
        return compute_intrinsic_value("TESTCO", build_history(years=10), **params)

    def test_produces_a_complete_result(self):
        result = self._value()
        assert result.ok
        assert result.primary_model == "DCF_FCFF"
        assert result.iv_blended and result.iv_blended > 0
        assert result.years_of_history == 10
        assert len(result.projection) == 10
        assert result.cost_of_capital is not None
        assert set(result.assumptions) == {"BEAR", "BASE", "BULL"}

    def test_scenarios_are_ordered(self):
        result = self._value()
        assert result.iv_bear < result.iv_base < result.iv_bull

    def test_upside_and_margin_of_safety_agree_with_price(self):
        result = self._value(current_price=300.0)
        expected_upside = (result.iv_blended - 300.0) / 300.0 * 100
        assert result.upside_pct == pytest.approx(expected_upside)
        assert result.margin_of_safety == pytest.approx(1 - 300.0 / result.iv_blended)

    def test_several_independent_models_contribute(self):
        models = {m.model for m in self._value().models}
        assert "DCF_FCFF" in models
        assert "EPV" in models
        assert len(models) >= 3

    def test_every_assumption_is_reported_for_audit(self):
        payload = self._value().as_dict()
        base = payload["assumptions"]["BASE"]
        for key in (
            "growth_initial",
            "growth_terminal",
            "operating_margin",
            "tax_rate",
            "sales_to_capital",
            "wacc",
            "terminal_roic",
        ):
            assert key in base
        assert payload["cost_of_capital"]["beta_source"] == "regressed"

    def test_price_does_not_move_the_valuation(self):
        # Intrinsic value must be independent of the quote; only upside moves.
        cheap = self._value(current_price=100.0)
        dear = self._value(current_price=900.0)
        assert cheap.iv_blended == pytest.approx(dear.iv_blended)
        assert cheap.upside_pct > dear.upside_pct

    def test_result_serializes_to_json_safe_primitives(self):
        import json

        json.dumps(self._value().as_dict())


class TestFinancialsUseADifferentModel:
    def test_bank_is_detected(self):
        assert is_financial("Banks", None)
        assert is_financial(None, "Private Sector Bank")
        assert not is_financial("IT Services", "Software")

    def test_bank_valued_on_excess_return_not_fcff(self):
        result = compute_intrinsic_value(
            "TESTBANK", bank_history(), current_price=250.0, sector="Banks"
        )
        assert result.ok
        assert result.primary_model == "EXCESS_RETURN"
        assert {m.model for m in result.models}.isdisjoint({"DCF_FCFF", "EV_EBITDA"})

    def test_warranted_pb_rises_with_roe(self):
        strong = build_history_profile(bank_history())
        weak = build_history_profile(bank_history())
        weak.roe_median = 0.06
        assert excess_return_value(strong, 0.13) > excess_return_value(weak, 0.13)


# ─────────────────────────────────────────────────────────────
# Cross-check models
# ─────────────────────────────────────────────────────────────
class TestCrossCheckModels:
    def test_epv_is_a_perpetuity_on_normalized_earnings(self):
        profile = build_history_profile(build_history(years=10))
        value = earnings_power_value(profile, wacc=0.12, shares=1000.0, net_debt=25_000.0)
        nopat = profile.latest_revenue * profile.operating_margin_median * 0.75
        assert value == pytest.approx((nopat / 0.12 - 25_000.0) / 1000.0, rel=1e-6)

    def test_graham_number_formula(self):
        assert graham_number(10.0, 40.0) == pytest.approx((22.5 * 10 * 40) ** 0.5)

    def test_graham_refuses_loss_making_or_negative_book(self):
        assert graham_number(-1.0, 40.0) is None
        assert graham_number(10.0, -5.0) is None
        assert graham_number(None, 40.0) is None


# ─────────────────────────────────────────────────────────────
# Confidence
# ─────────────────────────────────────────────────────────────
class TestConfidence:
    def _score(self, periods, tv_share=0.6):
        profile = build_history_profile(periods)
        result = compute_intrinsic_value(
            "X", periods, current_price=300.0, sector="IT Services"
        )
        return result

    def test_ten_years_beats_five(self):
        assert (
            self._score(build_history(years=10)).confidence_score
            > self._score(build_history(years=5)).confidence_score
        )

    def test_short_history_is_flagged_in_words(self):
        result = self._score(build_history(years=5))
        assert any("years of filings" in w for w in result.warnings)

    def test_volatile_margins_reduce_confidence(self):
        steady = build_history(years=10, operating_margin=0.18)
        erratic = build_history(years=10, operating_margin=0.18)
        for i, period in enumerate(erratic):
            swing = 0.30 if i % 2 else 0.02
            period.operating_income = period.revenue * swing
        assert (
            self._score(steady).confidence_score > self._score(erratic).confidence_score
        )

    def test_loss_years_reduce_confidence(self):
        clean = self._score(build_history(years=10))
        patchy = self._score(build_history(years=10, loss_years=(2, 4, 6, 8)))
        assert patchy.confidence_score < clean.confidence_score

    def test_terminal_heavy_valuation_is_flagged(self):
        profile = build_history_profile(build_history(years=10))
        _, _, warnings = score_confidence(profile, [], tv_share=0.95)
        assert any("terminal" in w.lower() for w in warnings)

    def test_confidence_label_tracks_score(self):
        result = self._score(build_history(years=10))
        assert result.confidence in ("HIGH", "MEDIUM", "LOW")
        if result.confidence == "HIGH":
            assert result.confidence_score >= 0.70


# ─────────────────────────────────────────────────────────────
# Reverse DCF
# ─────────────────────────────────────────────────────────────
class TestReverseDcf:
    def _setup(self, price: float):
        periods = build_history(years=10)
        profile = build_history_profile(periods)
        market = MarketContext.for_country("India")
        coc = compute_cost_of_capital(
            profile, market, market_cap=300_000.0, regressed_beta=1.1
        )
        return (
            reverse_dcf(
                profile,
                coc,
                market,
                current_price=price,
                shares=1000.0,
                net_debt=25_000.0,
            ),
            profile,
            coc,
            market,
        )

    def test_solution_round_trips_through_the_forward_model(self):
        # The implied growth, fed back into the DCF, must reproduce the price.
        result, profile, coc, market = self._setup(400.0)
        assert result["bracketed"] is True
        implied = result["implied_growth_rate"]

        base = build_assumptions(profile, coc, market, "BASE")
        trial = Assumptions(
            scenario="CHECK",
            growth_initial=implied,
            growth_terminal=min(market.risk_free_rate, implied),
            operating_margin=base.operating_margin,
            tax_rate=base.tax_rate,
            sales_to_capital=base.sales_to_capital,
            wacc=base.wacc,
            terminal_roic=base.terminal_roic,
        )
        value, _, _ = dcf_value_per_share(
            profile.latest_revenue, trial, 25_000.0, 1000.0
        )
        assert value == pytest.approx(400.0, rel=1e-3)

    def test_higher_price_implies_higher_expected_growth(self):
        low, *_ = self._setup(250.0)
        high, *_ = self._setup(600.0)
        assert high["implied_growth_rate"] > low["implied_growth_rate"]

    def test_unbracketable_price_says_so_instead_of_guessing(self):
        result, *_ = self._setup(500_000.0)
        assert result["bracketed"] is False
        assert result["implied_growth_rate"] is None

    def test_interpretation_states_both_numbers(self):
        result, *_ = self._setup(400.0)
        assert "%" in result["interpretation"]
        assert "historical" in result["interpretation"].lower() or "delivered" in result["interpretation"].lower()


# ─────────────────────────────────────────────────────────────
# Cost-of-capital convergence
# ─────────────────────────────────────────────────────────────
class TestWaccConvergence:
    """
    WACC weights depend on the market value of equity, which is what the model
    computes. Seeding those weights from the quoted price would let the price
    influence its own verdict. These tests pin the fixed-point solve that
    removes that dependency.
    """

    def _solve(self, seed: float | None):
        from services.analytics.intrinsic_value import solve_converged_cost_of_capital

        profile = build_history_profile(build_history(years=10))
        market = MarketContext.for_country("India")
        return solve_converged_cost_of_capital(
            profile,
            market,
            shares=1000.0,
            net_debt=25_000.0,
            seed_equity_value=seed,
            regressed_beta=1.1,
            beta_source="regressed",
        )

    def test_converges(self):
        _, converged = self._solve(300_000.0)
        assert converged is True

    def test_reaches_the_same_fixed_point_from_any_seed(self):
        low, _ = self._solve(10_000.0)
        high, _ = self._solve(5_000_000.0)
        assert low.wacc == pytest.approx(high.wacc, rel=1e-3)
        assert low.equity_weight == pytest.approx(high.equity_weight, rel=1e-3)

    def test_survives_a_missing_seed(self):
        coc, _ = self._solve(None)
        assert coc.wacc > 0

    def test_valuation_is_independent_of_the_quoted_price(self):
        def blended(price: float) -> float:
            return compute_intrinsic_value(
                "TESTCO",
                build_history(years=10),
                current_price=price,
                market=MarketContext.for_country("India"),
                sector="IT Services",
                regressed_beta=1.1,
            ).iv_blended

        values = [blended(p) for p in (50.0, 300.0, 1500.0, 9000.0)]
        for value in values[1:]:
            assert value == pytest.approx(values[0], rel=1e-3)

    def test_upside_still_responds_to_price(self):
        # Independence of IV must not flatten the thing the user actually reads.
        def upside(price: float) -> float:
            return compute_intrinsic_value(
                "TESTCO",
                build_history(years=10),
                current_price=price,
                sector="IT Services",
                regressed_beta=1.1,
            ).upside_pct

        assert upside(100.0) > upside(500.0) > upside(2000.0)


# ─────────────────────────────────────────────────────────────
# Low-discount-rate regime
# ─────────────────────────────────────────────────────────────
class TestLowCostOfCapital:
    """
    A cost of equity close to the risk-free rate is not an edge case.

    In a 7% risk-free-rate market like India, any defensive low-beta name —
    utilities, FMCG staples, or simply a stock whose beta regresses low — lands
    here. Capping terminal growth at the risk-free rate alone then leaves the
    Gordon formula without a workable spread, and the base and bull cases
    silently become incomputable while the bear case survives on its own WACC
    premium.

    That produced a published bear value sitting *above* the blended one, with
    no base or bull beside it: a scenario band that is neither ordered nor a
    range, on a panel read as both.
    """

    def _value(self, beta: float, rf: float = 0.07):
        return compute_intrinsic_value(
            "DEFENSIVE",
            build_history(years=10),
            current_price=300.0,
            market=MarketContext(risk_free_rate=rf, equity_risk_premium=0.058),
            sector="Utilities",
            regressed_beta=beta,
            beta_source="regressed",
        )

    def test_terminal_growth_never_starves_the_spread(self):
        for beta in (0.3, 0.5, 0.8, 1.2, 2.0):
            result = self._value(beta)
            for name, assumptions in result.assumptions.items():
                spread = assumptions.wacc - assumptions.growth_terminal
                assert spread >= MIN_SPREAD_OVER_G - 1e-9, (
                    f"beta {beta} scenario {name}: spread {spread:.5f} "
                    f"below the {MIN_SPREAD_OVER_G} minimum"
                )

    def test_all_three_scenarios_compute_on_a_low_beta_stock(self):
        # The exact regime that produced bear-only output.
        result = self._value(0.3)
        assert result.ok
        assert result.iv_bear is not None
        assert result.iv_base is not None
        assert result.iv_bull is not None

    def test_scenario_band_is_always_ordered(self):
        for beta in (0.3, 0.6, 1.0, 1.6, 2.5):
            result = self._value(beta)
            if result.iv_blended is None:
                continue
            assert result.iv_bear <= result.iv_base <= result.iv_bull, (
                f"beta {beta}: band {result.iv_bear}, {result.iv_base}, "
                f"{result.iv_bull} is not ordered"
            )

    def test_terminal_growth_still_respects_the_risk_free_cap(self):
        # The spread rule must not let terminal growth drift *above* the
        # risk-free rate on a high-WACC stock.
        result = self._value(2.5)
        for assumptions in result.assumptions.values():
            assert assumptions.growth_terminal <= 0.07 + 1e-9

    def test_a_partial_scenario_set_is_never_published(self):
        # Whatever the inputs, the three bands are all present or all derived
        # from model dispersion — never a lone survivor.
        for beta in (0.3, 0.4, 0.5, 0.9, 1.5, 2.5):
            for rf in (0.02, 0.04, 0.07, 0.09):
                result = self._value(beta, rf=rf)
                present = [
                    v for v in (result.iv_bear, result.iv_base, result.iv_bull)
                    if v is not None
                ]
                assert len(present) in (0, 3), (
                    f"beta {beta} rf {rf}: {len(present)} of 3 scenario values set"
                )

    def test_bear_never_exceeds_the_blended_value(self):
        for beta in (0.3, 0.7, 1.1, 2.0):
            result = self._value(beta)
            if result.iv_blended and result.iv_bear:
                assert result.iv_bear <= result.iv_bull

    def test_projection_is_dropped_when_there_is_no_base_case(self):
        # A ten-year projection belongs to a base case; keeping it when that
        # case could not be computed attaches workings to a number that is not
        # there.
        for beta in (0.3, 0.5, 1.0):
            result = self._value(beta)
            if result.iv_base is None:
                assert result.projection == []
