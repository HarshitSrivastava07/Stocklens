"""
Synthetic-but-realistic company filing histories.

These are *test inputs*, not product data — they exist so the valuation engine's
behaviour can be pinned down precisely. Nothing here is ever imported by the
running application; the application only ever sees real filings fetched from a
provider.
"""
from __future__ import annotations

from datetime import date

from services.providers.base import FinancialPeriod


def build_history(
    *,
    years: int = 10,
    start_revenue: float = 100_000.0,
    growth: float = 0.12,
    operating_margin: float = 0.18,
    ebitda_margin: float = 0.24,
    tax_rate: float = 0.25,
    debt: float = 40_000.0,
    cash: float = 15_000.0,
    shares: float = 1_000.0,
    equity_start: float = 80_000.0,
    capital_turnover: float = 1.5,
    interest_rate: float = 0.08,
    first_year_end: date = date(2015, 3, 31),
    margin_drift: float = 0.0,
    loss_years: tuple[int, ...] = (),
) -> list[FinancialPeriod]:
    """
    A coherent annual filing history where the balance sheet, income statement
    and cash-flow statement agree with each other.

    ``loss_years`` are 1-based indexes forced to a loss, for testing how the
    engine handles a patchy record.
    """
    periods: list[FinancialPeriod] = []
    revenue = start_revenue
    equity = equity_start

    for i in range(years):
        year_index = i + 1
        period_end = date(
            first_year_end.year + i, first_year_end.month, first_year_end.day
        )
        margin = operating_margin + margin_drift * i

        if year_index in loss_years:
            ebit = -abs(revenue * 0.05)
            net_income = ebit
            pretax = ebit
            tax_provision = 0.0
        else:
            ebit = revenue * margin
            interest = debt * interest_rate
            pretax = ebit - interest
            tax_provision = max(0.0, pretax * tax_rate)
            net_income = pretax - tax_provision

        ebitda = revenue * ebitda_margin
        depreciation = ebitda - ebit if ebitda > ebit else revenue * 0.04
        invested_capital = revenue / capital_turnover
        capex = revenue * 0.06
        cfo = net_income + depreciation

        periods.append(
            FinancialPeriod(
                period_end=period_end,
                period_type="A",
                revenue=revenue,
                operating_income=ebit,
                ebitda=ebitda,
                depreciation=depreciation,
                interest_expense=debt * interest_rate,
                pretax_income=pretax,
                tax_provision=tax_provision,
                net_income=net_income,
                eps_diluted=net_income / shares,
                total_assets=invested_capital + cash,
                equity=equity,
                total_debt=debt,
                cash=cash,
                invested_capital=invested_capital,
                shares_outstanding=shares,
                cfo=cfo,
                capex=capex,
                free_cash_flow=cfo - capex,
                source="TEST",
                currency="INR",
            )
        )

        revenue *= 1 + growth
        equity += max(net_income, 0.0) * 0.65  # retain 65%, pay out the rest

    return periods


def bank_history(years: int = 10) -> list[FinancialPeriod]:
    """A lender: high leverage, ROE-driven, no meaningful EV/EBITDA frame."""
    periods: list[FinancialPeriod] = []
    nii = 50_000.0
    equity = 200_000.0
    shares = 2_000.0

    for i in range(years):
        net_income = equity * 0.155
        periods.append(
            FinancialPeriod(
                period_end=date(2015 + i, 3, 31),
                period_type="A",
                revenue=nii,
                operating_income=nii * 0.42,
                ebitda=nii * 0.45,
                pretax_income=net_income / 0.75,
                tax_provision=net_income / 0.75 * 0.25,
                net_income=net_income,
                eps_diluted=net_income / shares,
                equity=equity,
                total_debt=equity * 8.0,
                cash=equity * 0.9,
                shares_outstanding=shares,
                total_assets=equity * 10.0,
                source="TEST",
                currency="INR",
            )
        )
        nii *= 1.14
        equity += net_income * 0.75

    return periods


def sparse_history(years: int = 3) -> list[FinancialPeriod]:
    """Too short to value, and missing most lines."""
    return [
        FinancialPeriod(
            period_end=date(2022 + i, 3, 31),
            period_type="A",
            revenue=1000.0 * (1.1**i),
            source="TEST",
        )
        for i in range(years)
    ]


def build_candles(
    *,
    days: int = 1300,
    start_price: float = 100.0,
    drift_per_day: float = 0.0004,
    wave_amplitude: float = 0.10,
    wave_period: int = 180,
    start: date = date(2020, 1, 1),
) -> list:
    """
    A deterministic price series: exponential drift with a sine cycle.

    Deterministic on purpose — a random walk makes indicator assertions flaky,
    and the point of these tests is to pin exact behaviour. Real price data is
    exercised by ``scripts/verify_live.py`` against the live source.
    """
    import math

    from services.providers.base import Candle

    out: list[Candle] = []
    day = start
    for i in range(days):
        # Skip weekends so the series looks like real trading sessions.
        while day.weekday() >= 5:
            day = date.fromordinal(day.toordinal() + 1)

        trend = start_price * ((1 + drift_per_day) ** i)
        cycle = 1 + wave_amplitude * math.sin(2 * math.pi * i / wave_period)
        close = trend * cycle
        high = close * 1.012
        low = close * 0.988
        open_ = (high + low) / 2

        out.append(
            Candle(
                date=day,
                open=round(open_, 2),
                high=round(high, 2),
                low=round(low, 2),
                close=round(close, 2),
                adj_close=round(close, 2),
                volume=1_000_000 + (i % 50) * 10_000,
            )
        )
        day = date.fromordinal(day.toordinal() + 1)
    return out


def falling_candles(days: int = 600, start_price: float = 500.0) -> list:
    """A sustained decline, for testing downtrend handling."""
    return build_candles(
        days=days,
        start_price=start_price,
        drift_per_day=-0.0015,
        wave_amplitude=0.03,
        wave_period=60,
    )
