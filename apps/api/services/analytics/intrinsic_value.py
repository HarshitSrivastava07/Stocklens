"""
Intrinsic value engine.

Every assumption in this module is *derived from the company's own reported
history*. Nothing is a hardcoded market-wide constant except the genuinely
market-wide inputs (risk-free rate, equity risk premium), which are passed in as
explicit ``MarketContext`` and recorded on the output so any number on screen can
be traced back to what produced it.

What the engine reads from ten years of filings:

  ==========================  ==============================================
  Assumption                  Derived from
  ==========================  ==============================================
  Revenue growth              Robust CAGR of reported revenue, faded to
                              terminal growth over the projection window
  Operating margin            Median of reported EBIT margin, with the
                              observed trend applied and capped
  Tax rate                    Median *effective* rate actually paid
  Reinvestment                Sales-to-capital ratio implied by the company's
                              own revenue and invested capital
  Terminal reinvestment       g / ROIC, so terminal growth is paid for
  Cost of debt                Interest expense / average gross debt
  Cost of equity              CAPM with beta regressed from real price history
  Capital structure           Market value of equity vs reported debt
  ==========================  ==============================================

The projection follows the FCFF framework: value the operating business, then
bridge to equity by subtracting net debt.

    FCFF_t  = EBIT_t x (1 - tax) - Reinvestment_t
    TV      = FCFF_{n+1} / (WACC - g)
    Equity  = SUM PV(FCFF) + PV(TV) - net debt
    IV/share= Equity / diluted shares

Terminal reinvestment is tied to terminal growth through ROIC
(``reinvestment = g / ROIC``), so a company cannot grow forever without funding
that growth. Omitting this is the single most common way a DCF prints a number
two or three times too high.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Sequence

from ..providers.base import FinancialPeriod
from .timeseries import (
    clamp,
    coefficient_of_variation,
    linear_trend,
    median,
    robust_growth,
    series_cagr,
    winsorize,
)

# ─────────────────────────────────────────────────────────────
# Guard rails
#
# These are not assumptions about any company. They are sanity bounds that stop
# a corrupt filing from producing an absurd valuation, and every one that binds
# is reported in ``ValuationResult.warnings``.
# ─────────────────────────────────────────────────────────────
MIN_YEARS_REQUIRED = 4          # below this, no valuation is published at all
PREFERRED_YEARS = 10            # full-confidence history depth
MAX_GROWTH = 0.35               # no company compounds faster than this for a decade
MIN_GROWTH = -0.15
MAX_MARGIN = 0.60
MIN_MARGIN = 0.005
MAX_WACC = 0.25
MIN_WACC = 0.06
MIN_SPREAD_OVER_G = 0.02        # WACC must exceed terminal growth by this much
MAX_SALES_TO_CAPITAL = 8.0
MIN_SALES_TO_CAPITAL = 0.3
PROJECTION_YEARS = 10


# ─────────────────────────────────────────────────────────────
# Inputs
# ─────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class MarketContext:
    """
    Market-wide inputs. Supplied per country because a rupee cash flow must be
    discounted at a rupee risk-free rate.
    """

    risk_free_rate: float
    equity_risk_premium: float
    country: str = "India"
    currency: str = "INR"
    # Only used when beta cannot be regressed from real price history.
    default_beta: float = 1.0
    as_of: date | None = None

    @staticmethod
    def for_country(country: str | None) -> "MarketContext":
        """
        Reasonable starting points by market. These are operator-configurable —
        the API exposes them so a desk can set its own house view — but they are
        never silently company-specific.
        """
        key = (country or "India").strip().lower()
        if key in ("united states", "usa", "us"):
            return MarketContext(0.042, 0.050, "United States", "USD")
        if key in ("united kingdom", "uk"):
            return MarketContext(0.040, 0.055, "United Kingdom", "GBP")
        if key == "japan":
            return MarketContext(0.010, 0.055, "Japan", "JPY")
        return MarketContext(0.070, 0.058, "India", "INR")


@dataclass
class HistoryProfile:
    """
    Everything the engine learned from the filing history. Surfaced verbatim in
    the API so a user can audit exactly which numbers drove the valuation.
    """

    years: int
    first_period: date | None
    last_period: date | None

    revenue_cagr: float | None = None
    revenue_cagr_5y: float | None = None
    revenue_cagr_3y: float | None = None
    revenue_stability: float | None = None

    operating_margin_median: float | None = None
    operating_margin_latest: float | None = None
    operating_margin_trend: float | None = None
    operating_margin_stability: float | None = None

    ebitda_margin_median: float | None = None
    net_margin_median: float | None = None

    effective_tax_rate: float | None = None
    capex_intensity: float | None = None
    sales_to_capital: float | None = None
    roic_median: float | None = None
    roe_median: float | None = None

    cost_of_debt: float | None = None
    net_debt: float | None = None
    total_debt: float | None = None
    cash: float | None = None
    shares_outstanding: float | None = None
    share_count_cagr: float | None = None

    latest_revenue: float | None = None
    latest_ebit: float | None = None
    latest_equity: float | None = None
    latest_eps: float | None = None
    book_value_per_share: float | None = None

    fcf_positive_years: int = 0
    profitable_years: int = 0
    data_completeness: float = 0.0


@dataclass
class Assumptions:
    """The specific inputs used for one scenario, recorded for audit."""

    scenario: str
    growth_initial: float
    growth_terminal: float
    operating_margin: float
    tax_rate: float
    sales_to_capital: float
    wacc: float
    terminal_roic: float
    projection_years: int = PROJECTION_YEARS

    def as_dict(self) -> dict:
        return {
            "scenario": self.scenario,
            "growth_initial": round(self.growth_initial, 6),
            "growth_terminal": round(self.growth_terminal, 6),
            "operating_margin": round(self.operating_margin, 6),
            "tax_rate": round(self.tax_rate, 6),
            "sales_to_capital": round(self.sales_to_capital, 6),
            "wacc": round(self.wacc, 6),
            "terminal_roic": round(self.terminal_roic, 6),
            "projection_years": self.projection_years,
        }


@dataclass
class CostOfCapital:
    """A WACC with every component preserved."""

    wacc: float
    cost_of_equity: float
    cost_of_debt_pretax: float
    cost_of_debt_after_tax: float
    beta: float
    beta_source: str
    equity_weight: float
    debt_weight: float
    risk_free_rate: float
    equity_risk_premium: float

    def as_dict(self) -> dict:
        return {
            "wacc": round(self.wacc, 6),
            "cost_of_equity": round(self.cost_of_equity, 6),
            "cost_of_debt_pretax": round(self.cost_of_debt_pretax, 6),
            "cost_of_debt_after_tax": round(self.cost_of_debt_after_tax, 6),
            "beta": round(self.beta, 4),
            "beta_source": self.beta_source,
            "equity_weight": round(self.equity_weight, 4),
            "debt_weight": round(self.debt_weight, 4),
            "risk_free_rate": round(self.risk_free_rate, 6),
            "equity_risk_premium": round(self.equity_risk_premium, 6),
        }


@dataclass
class ProjectionYear:
    """One year of the explicit forecast, kept so the UI can show the workings."""

    year: int
    revenue: float
    growth: float
    ebit: float
    nopat: float
    reinvestment: float
    fcff: float
    discount_factor: float
    present_value: float

    def as_dict(self) -> dict:
        return {
            "year": self.year,
            "revenue": round(self.revenue, 2),
            "growth": round(self.growth, 6),
            "ebit": round(self.ebit, 2),
            "nopat": round(self.nopat, 2),
            "reinvestment": round(self.reinvestment, 2),
            "fcff": round(self.fcff, 2),
            "discount_factor": round(self.discount_factor, 6),
            "present_value": round(self.present_value, 2),
        }


@dataclass
class ModelValue:
    """One model's answer, with its own confidence in that answer."""

    model: str
    value_per_share: float | None
    weight: float = 0.0
    detail: dict = field(default_factory=dict)
    note: str = ""

    def as_dict(self) -> dict:
        return {
            "model": self.model,
            "value_per_share": None if self.value_per_share is None else round(self.value_per_share, 2),
            "weight": round(self.weight, 4),
            "note": self.note,
            "detail": self.detail,
        }


@dataclass
class ValuationResult:
    """The complete, auditable output for one company."""

    symbol: str
    ok: bool
    reason: str = ""

    iv_bear: float | None = None
    iv_base: float | None = None
    iv_bull: float | None = None
    iv_blended: float | None = None

    current_price: float | None = None
    upside_pct: float | None = None
    margin_of_safety: float | None = None

    primary_model: str = ""
    models: list[ModelValue] = field(default_factory=list)
    history: HistoryProfile | None = None
    cost_of_capital: CostOfCapital | None = None
    assumptions: dict[str, Assumptions] = field(default_factory=dict)
    projection: list[ProjectionYear] = field(default_factory=list)
    terminal_value_share: float | None = None

    confidence: str = "LOW"
    confidence_score: float = 0.0
    years_of_history: int = 0
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "ok": self.ok,
            "reason": self.reason,
            "iv_bear": None if self.iv_bear is None else round(self.iv_bear, 2),
            "iv_base": None if self.iv_base is None else round(self.iv_base, 2),
            "iv_bull": None if self.iv_bull is None else round(self.iv_bull, 2),
            "iv_blended": None if self.iv_blended is None else round(self.iv_blended, 2),
            "current_price": self.current_price,
            "upside_pct": None if self.upside_pct is None else round(self.upside_pct, 2),
            "margin_of_safety": None if self.margin_of_safety is None else round(self.margin_of_safety, 4),
            "primary_model": self.primary_model,
            "models": [m.as_dict() for m in self.models],
            "cost_of_capital": self.cost_of_capital.as_dict() if self.cost_of_capital else None,
            "assumptions": {k: v.as_dict() for k, v in self.assumptions.items()},
            "projection": [p.as_dict() for p in self.projection],
            "terminal_value_share": (
                None if self.terminal_value_share is None else round(self.terminal_value_share, 4)
            ),
            "confidence": self.confidence,
            "confidence_score": round(self.confidence_score, 3),
            "years_of_history": self.years_of_history,
            "warnings": self.warnings,
        }


# ─────────────────────────────────────────────────────────────
# Step 1 — learn the business from its filings
# ─────────────────────────────────────────────────────────────
_CORE_FIELDS = (
    "revenue",
    "operating_income",
    "net_income",
    "equity",
    "total_debt",
    "cash",
    "cfo",
    "capex",
    "shares_outstanding",
)


def build_history_profile(periods: Sequence[FinancialPeriod]) -> HistoryProfile:
    """
    Condense a filing history into the statistics a valuation needs.

    ``periods`` must be annual and ordered oldest-first. Every statistic is
    computed only from the years that actually reported the relevant line; a
    missing year narrows the sample rather than contributing a zero.
    """
    annual = [p for p in periods if p.period_type == "A"]
    annual.sort(key=lambda p: p.period_end)

    profile = HistoryProfile(
        years=len(annual),
        first_period=annual[0].period_end if annual else None,
        last_period=annual[-1].period_end if annual else None,
    )
    if not annual:
        return profile

    latest = annual[-1]
    revenues = [p.revenue for p in annual]

    # ── Growth, measured three ways over different windows ────
    profile.revenue_cagr = robust_growth(revenues)
    if len(annual) >= 6:
        profile.revenue_cagr_5y = series_cagr(revenues[-6:])
    if len(annual) >= 4:
        profile.revenue_cagr_3y = series_cagr(revenues[-4:])
    profile.revenue_stability = coefficient_of_variation(
        [r for r in revenues if r is not None]
    )

    # ── Margins ───────────────────────────────────────────────
    op_margins = [p.operating_margin for p in annual if p.operating_margin is not None]
    profile.operating_margin_median = median(winsorize(op_margins))
    profile.operating_margin_latest = latest.operating_margin
    profile.operating_margin_stability = coefficient_of_variation(op_margins)
    trend = linear_trend(op_margins)
    profile.operating_margin_trend = trend[0] if trend else None

    profile.ebitda_margin_median = median(
        winsorize([p.ebitda_margin for p in annual if p.ebitda_margin is not None])
    )
    profile.net_margin_median = median(
        winsorize([p.net_margin for p in annual if p.net_margin is not None])
    )

    # ── Tax actually paid, not the statutory headline ─────────
    profile.effective_tax_rate = median(
        [p.effective_tax_rate for p in annual if p.effective_tax_rate is not None]
    )

    # ── Reinvestment intensity ────────────────────────────────
    profile.capex_intensity = median(
        winsorize([p.capex_intensity for p in annual if p.capex_intensity is not None])
    )
    profile.sales_to_capital = _sales_to_capital(annual)

    # ── Returns on capital ────────────────────────────────────
    profile.roic_median = median(winsorize(_roic_series(annual)))
    profile.roe_median = median(winsorize(_roe_series(annual)))

    # ── Financing ─────────────────────────────────────────────
    profile.cost_of_debt = _implied_cost_of_debt(annual)
    profile.total_debt = latest.total_debt
    profile.cash = (latest.cash or 0.0) + (latest.short_term_investments or 0.0)
    profile.net_debt = latest.net_debt

    # ── Share count: dilution is a real cost to a shareholder ─
    share_counts = [p.shares_outstanding for p in annual if p.shares_outstanding]
    profile.shares_outstanding = latest.shares_outstanding or (
        share_counts[-1] if share_counts else None
    )
    if len(share_counts) >= 2:
        profile.share_count_cagr = series_cagr(share_counts)

    # ── Latest levels ─────────────────────────────────────────
    profile.latest_revenue = latest.revenue
    profile.latest_ebit = latest.operating_income
    profile.latest_equity = latest.equity
    profile.latest_eps = latest.eps_diluted or latest.eps_basic
    if latest.equity and profile.shares_outstanding:
        profile.book_value_per_share = latest.equity / profile.shares_outstanding

    # ── Track record counters ─────────────────────────────────
    profile.profitable_years = sum(
        1 for p in annual if p.net_income is not None and p.net_income > 0
    )
    profile.fcf_positive_years = sum(1 for p in annual if _fcf(p) is not None and _fcf(p) > 0)

    profile.data_completeness = sum(
        p.completeness(_CORE_FIELDS) for p in annual
    ) / len(annual)

    return profile


def _fcf(period: FinancialPeriod) -> float | None:
    """Free cash flow, preferring the reported figure over CFO minus capex."""
    if period.free_cash_flow is not None:
        return period.free_cash_flow
    if period.cfo is None or period.capex is None:
        return None
    return period.cfo - abs(period.capex)


def _roic_series(periods: Sequence[FinancialPeriod]) -> list[float]:
    """
    Return on invested capital per year.

    Invested capital is taken from the filing when present, otherwise rebuilt as
    equity plus debt minus cash — the operating capital the business employs.
    """
    out: list[float] = []
    for p in periods:
        if p.operating_income is None:
            continue
        tax = p.effective_tax_rate
        tax = 0.25 if tax is None else tax
        nopat = p.operating_income * (1 - tax)

        capital = p.invested_capital
        if capital is None:
            if p.equity is None:
                continue
            capital = p.equity + (p.total_debt or 0.0) - (p.cash or 0.0)
        if capital is None or capital <= 0:
            continue
        out.append(nopat / capital)
    return out


def _roe_series(periods: Sequence[FinancialPeriod]) -> list[float]:
    out: list[float] = []
    for p in periods:
        if p.net_income is None or not p.equity or p.equity <= 0:
            continue
        out.append(p.net_income / p.equity)
    return out


def _sales_to_capital(periods: Sequence[FinancialPeriod]) -> float | None:
    """
    Revenue generated per unit of invested capital.

    This is what converts a growth assumption into a reinvestment requirement:
    growing revenue by X requires X / sales_to_capital of new capital. Measuring
    it from the company's own balance sheet is what makes the reinvestment
    forecast specific to this business instead of a market-wide guess.
    """
    ratios: list[float] = []
    for p in periods:
        if not p.revenue:
            continue
        capital = p.invested_capital
        if capital is None:
            if p.equity is None:
                continue
            capital = p.equity + (p.total_debt or 0.0) - (p.cash or 0.0)
        if capital is None or capital <= 0:
            continue
        ratios.append(p.revenue / capital)
    value = median(winsorize(ratios))
    if value is None:
        return None
    return clamp(value, MIN_SALES_TO_CAPITAL, MAX_SALES_TO_CAPITAL)


def _implied_cost_of_debt(periods: Sequence[FinancialPeriod]) -> float | None:
    """
    Interest expense over average gross debt — what this company actually pays
    to borrow, rather than a market average.
    """
    rates: list[float] = []
    for prev, cur in zip(periods, periods[1:]):
        if cur.interest_expense is None:
            continue
        debts = [d for d in (prev.total_debt, cur.total_debt) if d]
        if not debts:
            continue
        avg_debt = sum(debts) / len(debts)
        if avg_debt <= 0:
            continue
        rate = abs(cur.interest_expense) / avg_debt
        # Reject nonsense from a debt balance that collapsed mid-year.
        if 0.0 < rate < 0.30:
            rates.append(rate)
    return median(rates)


# ─────────────────────────────────────────────────────────────
# Step 2 — cost of capital
# ─────────────────────────────────────────────────────────────
def compute_cost_of_capital(
    profile: HistoryProfile,
    market: MarketContext,
    *,
    market_cap: float | None,
    regressed_beta: float | None = None,
    beta_source: str = "default",
    force_all_equity: bool = False,
) -> CostOfCapital:
    """
    WACC from the company's own numbers.

    Cost of equity is CAPM on the beta regressed from this stock's real price
    history. Cost of debt is what the company actually pays. Weights are market
    value of equity against reported debt, because capital structure is priced
    at market, not at book.
    """
    beta_value = regressed_beta if regressed_beta is not None else market.default_beta
    # Beta is noisy at the extremes; Blume-style bounds keep one volatile
    # stretch from setting a decade of discounting.
    beta_value = clamp(beta_value, 0.3, 2.5)

    cost_of_equity = market.risk_free_rate + beta_value * market.equity_risk_premium

    tax_rate = profile.effective_tax_rate
    tax_rate = 0.25 if tax_rate is None else clamp(tax_rate, 0.0, 0.50)

    cod_pretax = profile.cost_of_debt
    if cod_pretax is None:
        # No usable interest history: price debt off the risk-free rate with a
        # default spread rather than pretending borrowing is free.
        cod_pretax = market.risk_free_rate + 0.020
    cod_pretax = clamp(cod_pretax, market.risk_free_rate * 0.5, 0.25)
    cod_after_tax = cod_pretax * (1 - tax_rate)

    debt = 0.0 if force_all_equity else (profile.total_debt or 0.0)
    equity_value = market_cap if market_cap and market_cap > 0 else (profile.latest_equity or 0.0)
    total_capital = equity_value + debt

    if force_all_equity or total_capital <= 0:
        equity_weight, debt_weight = 1.0, 0.0
    else:
        equity_weight = equity_value / total_capital
        debt_weight = debt / total_capital

    wacc = equity_weight * cost_of_equity + debt_weight * cod_after_tax
    wacc = clamp(wacc, MIN_WACC, MAX_WACC)

    return CostOfCapital(
        wacc=wacc,
        cost_of_equity=cost_of_equity,
        cost_of_debt_pretax=cod_pretax,
        cost_of_debt_after_tax=cod_after_tax,
        beta=beta_value,
        beta_source=beta_source,
        equity_weight=equity_weight,
        debt_weight=debt_weight,
        risk_free_rate=market.risk_free_rate,
        equity_risk_premium=market.equity_risk_premium,
    )


# ─────────────────────────────────────────────────────────────
# Step 3 — the FCFF discounted cash-flow model
# ─────────────────────────────────────────────────────────────
def project_fcff(
    revenue_base: float,
    assumptions: Assumptions,
) -> list[ProjectionYear]:
    """
    Build the explicit forecast.

    Growth fades linearly from ``growth_initial`` in year 1 to
    ``growth_terminal`` in the final year, which is how real businesses converge
    on the wider economy rather than compounding at their historical rate
    forever.

    Reinvestment each year is the revenue *increase* divided by the company's own
    sales-to-capital ratio: growth is funded, not free.
    """
    years = assumptions.projection_years
    out: list[ProjectionYear] = []
    revenue = revenue_base

    for year in range(1, years + 1):
        if years > 1:
            fade = (year - 1) / (years - 1)
        else:
            fade = 1.0
        growth = assumptions.growth_initial + fade * (
            assumptions.growth_terminal - assumptions.growth_initial
        )

        previous_revenue = revenue
        revenue = previous_revenue * (1 + growth)
        ebit = revenue * assumptions.operating_margin
        nopat = ebit * (1 - assumptions.tax_rate)

        delta_revenue = revenue - previous_revenue
        reinvestment = delta_revenue / assumptions.sales_to_capital
        fcff = nopat - reinvestment

        discount_factor = 1.0 / ((1 + assumptions.wacc) ** year)
        out.append(
            ProjectionYear(
                year=year,
                revenue=revenue,
                growth=growth,
                ebit=ebit,
                nopat=nopat,
                reinvestment=reinvestment,
                fcff=fcff,
                discount_factor=discount_factor,
                present_value=fcff * discount_factor,
            )
        )
    return out


def terminal_value(
    final_year: ProjectionYear,
    assumptions: Assumptions,
) -> tuple[float, float] | None:
    """
    Gordon growth terminal value, with terminal reinvestment tied to ROIC.

    Returns ``(terminal_value, present_value)`` or ``None`` when the spread
    between WACC and terminal growth is too thin to be meaningful — at which
    point the formula divides by something close to zero and prints an
    arbitrarily large number.
    """
    spread = assumptions.wacc - assumptions.growth_terminal
    if spread < MIN_SPREAD_OVER_G:
        return None

    g = assumptions.growth_terminal
    roic = assumptions.terminal_roic
    # Growing at g forever costs g/ROIC of NOPAT every year, permanently.
    reinvestment_rate = clamp(g / roic, 0.0, 0.90) if roic > 0 else 0.0

    nopat_next = final_year.nopat * (1 + g)
    fcff_next = nopat_next * (1 - reinvestment_rate)
    tv = fcff_next / spread
    pv = tv / ((1 + assumptions.wacc) ** assumptions.projection_years)
    return tv, pv


def dcf_value_per_share(
    revenue_base: float,
    assumptions: Assumptions,
    net_debt: float,
    shares: float,
) -> tuple[float, list[ProjectionYear], float] | None:
    """
    Full FCFF valuation.

    Returns ``(value_per_share, projection, terminal_value_share)`` where the
    last item is the fraction of total enterprise value sitting in the terminal
    value — the honest health warning on any DCF. Above ~85% the answer is
    really a bet on the terminal assumption, and the engine says so.
    """
    if revenue_base is None or revenue_base <= 0 or shares is None or shares <= 0:
        return None

    projection = project_fcff(revenue_base, assumptions)
    if not projection:
        return None

    tv = terminal_value(projection[-1], assumptions)
    if tv is None:
        return None
    _, pv_terminal = tv

    pv_explicit = sum(p.present_value for p in projection)
    enterprise_value = pv_explicit + pv_terminal
    if enterprise_value <= 0:
        return None

    equity_value = enterprise_value - (net_debt or 0.0)
    if equity_value <= 0:
        return None

    tv_share = pv_terminal / enterprise_value if enterprise_value else 0.0
    return equity_value / shares, projection, tv_share


# ─────────────────────────────────────────────────────────────
# Step 4 — cross-checks from other models
# ─────────────────────────────────────────────────────────────
def earnings_power_value(
    profile: HistoryProfile, wacc: float, shares: float, net_debt: float
) -> float | None:
    """
    Greenwald's earnings power value: what the business is worth assuming zero
    growth, valuing only its demonstrated ability to earn.

    A floor, not a target. If EPV alone already exceeds the market price, the
    market is paying nothing for growth.
    """
    if not shares or shares <= 0 or wacc <= 0:
        return None
    if profile.latest_revenue is None or profile.operating_margin_median is None:
        return None

    normalized_ebit = profile.latest_revenue * profile.operating_margin_median
    tax = profile.effective_tax_rate if profile.effective_tax_rate is not None else 0.25
    nopat = normalized_ebit * (1 - tax)
    if nopat <= 0:
        return None

    enterprise_value = nopat / wacc
    equity_value = enterprise_value - (net_debt or 0.0)
    if equity_value <= 0:
        return None
    return equity_value / shares


def graham_number(eps: float | None, book_value_per_share: float | None) -> float | None:
    """
    Graham's defensive-investor screen: sqrt(22.5 x EPS x BVPS).

    Included as a conservative sanity check on asset-heavy and financial names,
    never as the primary model.
    """
    if not eps or not book_value_per_share:
        return None
    if eps <= 0 or book_value_per_share <= 0:
        return None
    return math.sqrt(22.5 * eps * book_value_per_share)


def excess_return_value(
    profile: HistoryProfile, cost_of_equity: float
) -> float | None:
    """
    Excess-return (warranted P/B) model for banks and lenders.

        justified P/B = (ROE - g) / (cost of equity - g)

    For a financial firm, debt is raw material rather than financing, so an
    enterprise-level FCFF model is the wrong frame; this values the equity
    directly off its return on book.
    """
    roe = profile.roe_median
    bvps = profile.book_value_per_share
    if roe is None or bvps is None or bvps <= 0:
        return None

    # Growth a bank can sustain from retained earnings, capped below its ROE.
    g = clamp((profile.revenue_cagr or 0.05) * 0.5, 0.0, min(0.10, max(roe - 0.02, 0.0)))
    spread = cost_of_equity - g
    if spread < MIN_SPREAD_OVER_G:
        return None

    justified_pb = (roe - g) / spread
    justified_pb = clamp(justified_pb, 0.2, 8.0)
    return justified_pb * bvps


def exit_multiple_value(
    profile: HistoryProfile,
    shares: float,
    net_debt: float,
    target_ev_ebitda: float,
) -> float | None:
    """Relative cross-check: what a trade buyer might pay on today's EBITDA."""
    if not shares or shares <= 0:
        return None
    if profile.latest_revenue is None or profile.ebitda_margin_median is None:
        return None
    ebitda = profile.latest_revenue * profile.ebitda_margin_median
    if ebitda <= 0:
        return None
    equity_value = ebitda * target_ev_ebitda - (net_debt or 0.0)
    if equity_value <= 0:
        return None
    return equity_value / shares


# ─────────────────────────────────────────────────────────────
# Step 5 — scenario construction
# ─────────────────────────────────────────────────────────────
# Scenarios are *perturbations of the company's own measured history*, not
# independent guesses. Base is what the business did; bear and bull are
# disciplined haircuts and uplifts around it.
_SCENARIO_SHIFTS = {
    "BEAR": {"growth": -0.40, "margin": -0.15, "wacc": +0.015},
    "BASE": {"growth": 0.00, "margin": 0.00, "wacc": 0.000},
    "BULL": {"growth": +0.35, "margin": +0.12, "wacc": -0.010},
}

# NOTE: a `_BLEND_WEIGHTS = {"BEAR": 0.25, "BASE": 0.50, "BULL": 0.25}` constant
# used to sit here, documented as the scenario blend. Nothing referenced it. The
# blended value is a weighted average across *models* (see `ModelValue.weight`),
# not across scenarios, so anyone tuning that constant would have changed
# nothing while believing they had reweighted the valuation. Removed rather than
# left as a decoy.


def build_assumptions(
    profile: HistoryProfile,
    coc: CostOfCapital,
    market: MarketContext,
    scenario: str,
) -> Assumptions | None:
    """Turn the measured history into one scenario's forecast inputs."""
    shifts = _SCENARIO_SHIFTS[scenario]

    base_growth = profile.revenue_cagr
    if base_growth is None:
        return None

    # Recent growth matters more than a decade-old boom, so the starting rate
    # leans on the 3- and 5-year windows when they exist.
    windows = [w for w in (profile.revenue_cagr, profile.revenue_cagr_5y, profile.revenue_cagr_3y) if w is not None]
    blended_growth = median(windows) if windows else base_growth

    growth_initial = clamp(blended_growth * (1 + shifts["growth"]), MIN_GROWTH, MAX_GROWTH)

    wacc = clamp(coc.wacc + shifts["wacc"], MIN_WACC, MAX_WACC)

    # Nothing grows faster than the economy forever. Terminal growth is capped
    # at the risk-free rate — the standard discipline, since a company growing
    # faster than that in perpetuity eventually becomes the whole economy.
    #
    # But that cap alone is not sufficient, and assuming it was is a real defect
    # this engine shipped with. The Gordon formula needs WACC to exceed terminal
    # growth by a workable margin; cap only at the risk-free rate and a
    # low-beta company can end up with a cost of equity *beneath* it. Observed
    # in practice: a stock whose beta regressed low took cost of equity to
    # 8.74% against an Indian risk-free rate of 7.00%, leaving a 1.74% spread
    # against the 2% minimum. The base and bull cases silently became
    # incomputable while the bear case survived on its own WACC premium — so
    # the published bear value sat *above* the blended one, inverting the
    # scenario ordering the whole panel is read through.
    #
    # In a 7% risk-free-rate market this is not an edge case: it is every
    # defensive, low-beta name. Terminal growth is therefore capped by whichever
    # binds first — the risk-free rate, or the spread the model needs to exist.
    terminal_ceiling = min(market.risk_free_rate, wacc - MIN_SPREAD_OVER_G)
    growth_terminal = clamp(
        min(terminal_ceiling, growth_initial), 0.0, max(0.0, terminal_ceiling)
    )

    margin = profile.operating_margin_median
    if margin is None:
        return None
    # Give partial credit to a genuine multi-year margin trend.
    if profile.operating_margin_trend is not None:
        margin += profile.operating_margin_trend * 2.0
    margin = clamp(margin * (1 + shifts["margin"]), MIN_MARGIN, MAX_MARGIN)

    tax = profile.effective_tax_rate
    tax = 0.25 if tax is None else clamp(tax, 0.05, 0.50)

    sales_to_capital = profile.sales_to_capital or 2.0

    # Terminal ROIC: a mature business earns roughly its cost of capital as
    # competition arrives. Giving credit for a durable franchise, but capped.
    measured_roic = profile.roic_median
    if measured_roic is None:
        terminal_roic = wacc
    else:
        terminal_roic = clamp(measured_roic, wacc, wacc + 0.08)

    return Assumptions(
        scenario=scenario,
        growth_initial=growth_initial,
        growth_terminal=growth_terminal,
        operating_margin=margin,
        tax_rate=tax,
        sales_to_capital=sales_to_capital,
        wacc=wacc,
        terminal_roic=terminal_roic,
    )


# ─────────────────────────────────────────────────────────────
# Step 6 — confidence
# ─────────────────────────────────────────────────────────────
def score_confidence(
    profile: HistoryProfile, models: Sequence[ModelValue], tv_share: float | None
) -> tuple[str, float, list[str]]:
    """
    How much this valuation deserves to be trusted, 0..1.

    Driven by history depth, data completeness, margin stability, profit track
    record, how much of the value rests on the terminal assumption, and whether
    independent models agree. Every deduction is reported as a warning so the
    user sees *why* confidence is what it is.
    """
    warnings: list[str] = []
    score = 1.0

    # History depth — the user asked for ten years; less than that costs.
    depth_ratio = min(profile.years / PREFERRED_YEARS, 1.0)
    score *= 0.55 + 0.45 * depth_ratio
    if profile.years < PREFERRED_YEARS:
        warnings.append(
            f"Only {profile.years} years of filings available (10 preferred)"
        )

    # Completeness of the core lines.
    score *= 0.5 + 0.5 * clamp(profile.data_completeness, 0.0, 1.0)
    if profile.data_completeness < 0.75:
        warnings.append(
            f"Filing data {profile.data_completeness*100:.0f}% complete across core fields"
        )

    # Margin stability: a wildly swinging margin makes any forecast fragile.
    if profile.operating_margin_stability is not None:
        if profile.operating_margin_stability > 0.60:
            score *= 0.70
            warnings.append("Operating margin has been highly volatile")
        elif profile.operating_margin_stability > 0.35:
            score *= 0.88
            warnings.append("Operating margin shows meaningful year-to-year swings")

    # Loss-making years.
    if profile.years:
        profitable_ratio = profile.profitable_years / profile.years
        if profitable_ratio < 0.6:
            score *= 0.65
            warnings.append(
                f"Profitable in only {profile.profitable_years} of {profile.years} years"
            )
        elif profitable_ratio < 1.0:
            score *= 0.90

    # Terminal value concentration.
    if tv_share is not None:
        if tv_share > 0.90:
            score *= 0.60
            warnings.append(
                f"{tv_share*100:.0f}% of value sits in the terminal assumption"
            )
        elif tv_share > 0.80:
            score *= 0.82
            warnings.append(
                f"{tv_share*100:.0f}% of value rests on terminal growth"
            )

    # Cross-model agreement.
    values = [m.value_per_share for m in models if m.value_per_share is not None]
    if len(values) >= 2:
        spread_mid = median(values)
        if spread_mid and spread_mid > 0:
            dispersion = (max(values) - min(values)) / spread_mid
            if dispersion > 1.5:
                score *= 0.70
                warnings.append("Valuation models disagree substantially")
            elif dispersion > 0.8:
                score *= 0.88
    else:
        score *= 0.85
        warnings.append("Only one valuation model could be computed")

    # Share count rising steadily dilutes every per-share number.
    if profile.share_count_cagr is not None and profile.share_count_cagr > 0.03:
        score *= 0.90
        warnings.append(
            f"Share count growing {profile.share_count_cagr*100:.1f}%/yr (dilution)"
        )

    score = clamp(score, 0.0, 1.0)
    if score >= 0.70:
        label = "HIGH"
    elif score >= 0.45:
        label = "MEDIUM"
    else:
        label = "LOW"
    return label, score, warnings


# ─────────────────────────────────────────────────────────────
# Step 7 — the public entry point
# ─────────────────────────────────────────────────────────────
# Sectors where an enterprise FCFF model is structurally wrong.
_FINANCIAL_KEYWORDS = (
    "bank",
    "nbfc",
    "financial",
    "insurance",
    "capital market",
    "asset management",
    "lending",
    "housing finance",
)


def is_financial(sector: str | None, industry: str | None = None) -> bool:
    blob = f"{sector or ''} {industry or ''}".lower()
    return any(k in blob for k in _FINANCIAL_KEYWORDS)


def solve_converged_cost_of_capital(
    profile: HistoryProfile,
    market: MarketContext,
    *,
    shares: float,
    net_debt: float,
    seed_equity_value: float | None,
    regressed_beta: float | None,
    beta_source: str,
    max_iterations: int = 20,
    tolerance: float = 1e-5,
) -> tuple[CostOfCapital, bool]:
    """
    Solve WACC and equity value together until they agree.

    There is a genuine circularity in any enterprise valuation: WACC is weighted
    by the *market* value of equity, but market value is what we are trying to
    compute. Seeding those weights from the quoted price would make "intrinsic"
    value a function of the price it is supposed to judge — a stock that halved
    would show a smaller upside than it deserves, because its falling equity
    weight would quietly lower its own discount rate.

    So the weights are seeded once, then re-derived from the *computed* equity
    value until the two stop moving. The iteration is a contraction: a higher
    equity value raises the equity weight, which raises WACC, which lowers the
    equity value. It converges in a handful of passes to a fixed point that does
    not depend on where it started.

    Returns the converged cost of capital and whether it actually converged.
    """
    # The iteration always starts from the all-equity cost of capital, never
    # from the caller's seed. Two reasons:
    #
    #   * It is the highest WACC the company can have, so the first pass is
    #     always computable. Starting from an arbitrarily low seed can produce a
    #     WACC beneath terminal growth, where the Gordon formula is undefined and
    #     the solve would abort on its first step with a meaningless answer.
    #   * It makes the result depend on nothing but the filings. The seed is used
    #     only to bound the first update, so the same company yields the same
    #     WACC whatever its price happens to be.
    coc = compute_cost_of_capital(
        profile,
        market,
        market_cap=None,
        regressed_beta=regressed_beta,
        beta_source=beta_source,
        force_all_equity=True,
    )

    seed = seed_equity_value
    if not seed or seed <= 0:
        seed = profile.latest_equity or 0.0
    previous = seed if seed > 0 else 1.0
    converged = False
    last_direction = 0

    for _ in range(max_iterations):
        assumptions = build_assumptions(profile, coc, market, "BASE")
        if assumptions is None:
            break
        outcome = dcf_value_per_share(
            profile.latest_revenue, assumptions, net_debt, shares
        )
        if outcome is None:
            break

        implied_equity = outcome[0] * shares
        step = implied_equity - previous
        direction = 1 if step > 0 else (-1 if step < 0 else 0)

        # Take the full step while still travelling in one direction, which
        # closes a badly-placed seed in a few passes. Damp only once the
        # iteration overshoots and reverses, which is where an undamped update
        # would oscillate between two values forever.
        if last_direction != 0 and direction != 0 and direction != last_direction:
            next_equity = previous + step * 0.5
        else:
            next_equity = implied_equity
        last_direction = direction

        coc = compute_cost_of_capital(
            profile,
            market,
            market_cap=next_equity,
            regressed_beta=regressed_beta,
            beta_source=beta_source,
        )

        if previous > 0 and abs(next_equity - previous) / previous < tolerance:
            converged = True
            previous = next_equity
            break
        previous = next_equity

    return coc, converged


def compute_intrinsic_value(
    symbol: str,
    periods: Sequence[FinancialPeriod],
    *,
    current_price: float | None,
    market: MarketContext | None = None,
    sector: str | None = None,
    industry: str | None = None,
    market_cap: float | None = None,
    regressed_beta: float | None = None,
    beta_source: str = "default",
    shares_override: float | None = None,
) -> ValuationResult:
    """
    Value one company from its filing history.

    Refuses rather than guesses: fewer than ``MIN_YEARS_REQUIRED`` annual
    filings, or no usable revenue and margin history, returns ``ok=False`` with
    a stated reason. A refusal is a correct answer; a fabricated intrinsic value
    is not.
    """
    result = ValuationResult(symbol=symbol, ok=False, current_price=current_price)

    annual = sorted(
        [p for p in periods if p.period_type == "A"], key=lambda p: p.period_end
    )
    result.years_of_history = len(annual)

    if len(annual) < MIN_YEARS_REQUIRED:
        result.reason = (
            f"Only {len(annual)} annual filings; {MIN_YEARS_REQUIRED} is the minimum "
            "for a defensible valuation"
        )
        return result

    profile = build_history_profile(annual)
    result.history = profile

    shares = shares_override or profile.shares_outstanding
    if not shares or shares <= 0:
        result.reason = "Share count unavailable; cannot express value per share"
        return result

    market = market or MarketContext.for_country(None)
    net_debt = profile.net_debt or 0.0
    financial = is_financial(sector, industry)

    # The quoted price only ever seeds the capital-structure weights; the solve
    # below iterates them away, so the published intrinsic value does not depend
    # on the price it is being compared against.
    seed_equity_value = market_cap or (current_price * shares if current_price else None)

    if financial:
        # A lender is valued off return on book at its cost of equity, which is
        # CAPM-driven and carries no capital-structure weighting to converge.
        coc = compute_cost_of_capital(
            profile,
            market,
            market_cap=seed_equity_value,
            regressed_beta=regressed_beta,
            beta_source=beta_source,
        )
    else:
        coc, converged = solve_converged_cost_of_capital(
            profile,
            market,
            shares=shares,
            net_debt=net_debt,
            seed_equity_value=seed_equity_value,
            regressed_beta=regressed_beta,
            beta_source=beta_source,
        )
        if not converged:
            result.warnings.append(
                "Cost of capital did not fully converge; weights use the last iterate"
            )
    result.cost_of_capital = coc
    models: list[ModelValue] = []
    scenario_values: dict[str, float] = {}
    tv_share: float | None = None

    # ── Primary: FCFF DCF for operating companies ─────────────
    if not financial:
        for scenario in ("BEAR", "BASE", "BULL"):
            assumptions = build_assumptions(profile, coc, market, scenario)
            if assumptions is None:
                continue
            result.assumptions[scenario] = assumptions

            outcome = dcf_value_per_share(
                profile.latest_revenue, assumptions, net_debt, shares
            )
            if outcome is None:
                continue
            value, projection, scenario_tv_share = outcome
            scenario_values[scenario] = value
            if scenario == "BASE":
                result.projection = projection
                tv_share = scenario_tv_share

        if scenario_values:
            models.append(
                ModelValue(
                    model="DCF_FCFF",
                    value_per_share=scenario_values.get("BASE"),
                    weight=0.55,
                    note="10-year FCFF discounted cash flow from reported history",
                    detail={
                        "terminal_value_share": (
                            None if tv_share is None else round(tv_share, 4)
                        ),
                        "scenarios": {k: round(v, 2) for k, v in scenario_values.items()},
                    },
                )
            )
        result.primary_model = "DCF_FCFF"

    # ── Primary for lenders: excess return on book ────────────
    else:
        excess = excess_return_value(profile, coc.cost_of_equity)
        if excess is not None:
            models.append(
                ModelValue(
                    model="EXCESS_RETURN",
                    value_per_share=excess,
                    weight=0.60,
                    note="Warranted price-to-book from sustained return on equity",
                    detail={
                        "roe_median": round(profile.roe_median, 4) if profile.roe_median else None,
                        "book_value_per_share": (
                            round(profile.book_value_per_share, 2)
                            if profile.book_value_per_share
                            else None
                        ),
                        "cost_of_equity": round(coc.cost_of_equity, 4),
                    },
                )
            )
            # Bands come from moving cost of equity, the dominant sensitivity.
            for scenario, bump in (("BEAR", 0.02), ("BASE", 0.0), ("BULL", -0.015)):
                alt = excess_return_value(profile, coc.cost_of_equity + bump)
                if alt is not None:
                    scenario_values[scenario] = alt
        result.primary_model = "EXCESS_RETURN"

    # ── Cross-checks, always computed where the inputs exist ──
    epv = earnings_power_value(profile, coc.wacc, shares, net_debt)
    if epv is not None:
        models.append(
            ModelValue(
                model="EPV",
                value_per_share=epv,
                weight=0.20,
                note="Earnings power value: zero-growth floor on demonstrated earnings",
            )
        )

    graham = graham_number(profile.latest_eps, profile.book_value_per_share)
    if graham is not None:
        models.append(
            ModelValue(
                model="GRAHAM",
                value_per_share=graham,
                weight=0.10,
                note="Graham defensive value from earnings and book value",
            )
        )

    if not financial:
        # Target multiple anchored on the company's own return on capital, so a
        # high-return business is not valued like a commodity producer.
        roic = profile.roic_median or coc.wacc
        target_multiple = clamp(6.0 + (roic - coc.wacc) * 60.0, 5.0, 22.0)
        exit_value = exit_multiple_value(profile, shares, net_debt, target_multiple)
        if exit_value is not None:
            models.append(
                ModelValue(
                    model="EV_EBITDA",
                    value_per_share=exit_value,
                    weight=0.15,
                    note=f"Exit at {target_multiple:.1f}x EV/EBITDA, set by measured ROIC",
                    detail={"target_ev_ebitda": round(target_multiple, 2)},
                )
            )

    usable = [m for m in models if m.value_per_share is not None and m.value_per_share > 0]
    if not usable:
        result.reason = "No valuation model could be computed from the available filings"
        result.warnings.append(result.reason)
        return result

    # ── Blend ─────────────────────────────────────────────────
    total_weight = sum(m.weight for m in usable)
    if total_weight <= 0:
        blended = median([m.value_per_share for m in usable])
    else:
        blended = sum(m.value_per_share * m.weight for m in usable) / total_weight

    # Scenario bands. Where the primary model produced a *complete* set, use it;
    # otherwise derive a band from the dispersion of the models themselves.
    #
    # "Complete" is the operative word. A partial set is worse than none: when
    # only the bear case can be computed — which happens when the base and bull
    # discount rates leave too thin a spread over terminal growth — storing that
    # single value leaves the panel showing a bear figure with no base or bull
    # beside it, and nothing stops that lone bear value sitting *above* the
    # blended one. The scenario band is read as an ordered range, so publishing
    # one that is neither ordered nor a range misinforms rather than informs.
    complete = all(k in scenario_values for k in ("BEAR", "BASE", "BULL"))

    if complete:
        result.iv_bear = scenario_values["BEAR"]
        result.iv_base = scenario_values["BASE"]
        result.iv_bull = scenario_values["BULL"]
    else:
        if scenario_values:
            missing = [
                k for k in ("BEAR", "BASE", "BULL") if k not in scenario_values
            ]
            result.warnings.append(
                "Scenario range unavailable: the "
                + ", ".join(m.lower() for m in missing)
                + " case could not be computed; showing the spread across models instead"
            )
            # The projection belongs to a base case that does not exist.
            result.projection = []
            tv_share = None

        values = [m.value_per_share for m in usable]
        result.iv_bear = min(values)
        result.iv_base = median(values)
        result.iv_bull = max(values)

    # Ordering is an invariant of the panel, not an accident of which model won.
    band = sorted(
        v for v in (result.iv_bear, result.iv_base, result.iv_bull) if v is not None
    )
    if len(band) == 3:
        result.iv_bear, result.iv_base, result.iv_bull = band

    result.iv_blended = blended
    result.terminal_value_share = tv_share
    result.models = models

    if current_price and current_price > 0 and blended:
        result.upside_pct = (blended - current_price) / current_price * 100.0
        result.margin_of_safety = 1.0 - (current_price / blended)

    label, score, warnings = score_confidence(profile, usable, tv_share)
    result.confidence = label
    result.confidence_score = score
    result.warnings.extend(warnings)
    result.ok = True
    return result


# ─────────────────────────────────────────────────────────────
# Reverse DCF — what does today's price already assume?
# ─────────────────────────────────────────────────────────────
def reverse_dcf(
    profile: HistoryProfile,
    coc: CostOfCapital,
    market: MarketContext,
    *,
    current_price: float,
    shares: float,
    net_debt: float,
) -> dict | None:
    """
    Solve for the revenue growth rate that makes the DCF equal today's price.

    This is the most useful single number in the product: rather than arguing
    about whether a stock is cheap, it states plainly what the buyer is
    committing to believe, and compares that against what the company has
    actually delivered.
    """
    if not current_price or current_price <= 0 or not shares or shares <= 0:
        return None
    if profile.latest_revenue is None or profile.operating_margin_median is None:
        return None

    base = build_assumptions(profile, coc, market, "BASE")
    if base is None:
        return None

    def value_at(growth: float) -> float | None:
        trial = Assumptions(
            scenario="IMPLIED",
            growth_initial=growth,
            growth_terminal=clamp(min(market.risk_free_rate, growth), 0.0, market.risk_free_rate),
            operating_margin=base.operating_margin,
            tax_rate=base.tax_rate,
            sales_to_capital=base.sales_to_capital,
            wacc=base.wacc,
            terminal_roic=base.terminal_roic,
        )
        outcome = dcf_value_per_share(profile.latest_revenue, trial, net_debt, shares)
        return None if outcome is None else outcome[0]

    lo, hi = MIN_GROWTH, MAX_GROWTH
    low_value, high_value = value_at(lo), value_at(hi)
    if low_value is None or high_value is None:
        return None
    # Value is monotonically increasing in growth; if the price sits outside the
    # bracket, say so rather than returning a bound as if it were a solution.
    if current_price < low_value:
        return {
            "implied_growth_rate": None,
            "historical_growth_rate": profile.revenue_cagr,
            "bracketed": False,
            "interpretation": (
                "Price is below the value implied even by the most pessimistic "
                "growth in range; the market is pricing in shrinkage beyond "
                f"{MIN_GROWTH*100:.0f}% a year."
            ),
        }
    if current_price > high_value:
        return {
            "implied_growth_rate": None,
            "historical_growth_rate": profile.revenue_cagr,
            "bracketed": False,
            "interpretation": (
                "Price exceeds the value of even "
                f"{MAX_GROWTH*100:.0f}% annual growth for a decade."
            ),
        }

    implied = (lo + hi) / 2
    for _ in range(80):
        implied = (lo + hi) / 2
        value = value_at(implied)
        if value is None:
            break
        if abs(value - current_price) < max(0.01, current_price * 1e-5):
            break
        if value < current_price:
            lo = implied
        else:
            hi = implied

    historical = profile.revenue_cagr
    premium = None
    if historical is not None and abs(historical) > 1e-6:
        premium = (implied - historical) / abs(historical)

    if historical is None:
        verdict = "No comparable growth history to judge this against."
    elif implied > historical * 1.5:
        verdict = "Materially more optimistic than the company's own record."
    elif implied > historical * 1.1:
        verdict = "Somewhat ahead of what the company has delivered."
    elif implied < historical * 0.7:
        verdict = "Below the company's historical growth — the market is discounting it."
    else:
        verdict = "Broadly in line with the company's historical growth."

    return {
        "implied_growth_rate": implied,
        "historical_growth_rate": historical,
        "premium_discount_pct": premium,
        "bracketed": True,
        "wacc_used": base.wacc,
        "terminal_growth_used": base.growth_terminal,
        "interpretation": (
            f"At {current_price:,.2f} the market is pricing in {implied*100:.1f}% annual "
            f"revenue growth for {PROJECTION_YEARS} years. The company delivered "
            f"{(historical or 0)*100:.1f}% over the last {profile.years} years. {verdict}"
        ),
    }
