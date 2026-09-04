"""
Ratio Engine Service
Calculates all 40+ financial ratios from financial_results.
Called nightly by scheduler at 18:00 IST.
"""
import logging
from datetime import date, timedelta
from typing import Optional

import structlog
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from dependencies.db import AsyncSessionLocal
from models.db_models import Stock, FinancialResult, FinancialRatio, AuditLog

log = structlog.get_logger("ratio_engine")


def _safe_div(a, b, default=None):
    """Safe division — returns None if denominator is 0 or None."""
    try:
        if a is None or b is None or b == 0:
            return default
        return float(a) / float(b)
    except (TypeError, ZeroDivisionError):
        return default


def _cagr(end_val, start_val, years: int) -> Optional[float]:
    """Compute CAGR: (end/start)^(1/years) - 1"""
    try:
        if start_val is None or end_val is None or start_val <= 0 or years <= 0:
            return None
        return (float(end_val) / float(start_val)) ** (1.0 / years) - 1.0
    except Exception:
        return None


def compute_ratios(
    symbol: str,
    cmp: float,
    annual_results: list,  # sorted newest first
    quarterly_results: list,
) -> dict:
    """
    Compute all ratios from financial data and CMP.
    Returns a dict of ratio values.
    """
    if not annual_results:
        return {}

    latest = annual_results[0]

    # ── Shares & Market Cap ──────────────────────────────────
    shares = latest.shares_outstanding
    market_cap = (shares * cmp) if shares and cmp else None

    # ── Enterprise Value ─────────────────────────────────────
    net_debt = None
    if latest.total_debt is not None and latest.cash_and_equiv is not None:
        net_debt = float(latest.total_debt) - float(latest.cash_and_equiv)
    ev = (market_cap + net_debt) if market_cap and net_debt is not None else None

    # ── Trailing EPS (last 4 quarters) ───────────────────────
    trailing_eps = None
    if quarterly_results and len(quarterly_results) >= 4:
        ttm_pat = sum(
            float(q.pat) for q in quarterly_results[:4] if q.pat is not None
        )
        ttm_eps = _safe_div(ttm_pat, shares)
        trailing_eps = ttm_eps

    # ── Valuation Ratios ─────────────────────────────────────
    eps_for_pe = trailing_eps or (float(latest.eps) if latest.eps else None)
    book_value = float(latest.book_value_per_share) if latest.book_value_per_share else _safe_div(latest.net_worth, shares)

    pe = _safe_div(cmp, eps_for_pe)
    pb = _safe_div(cmp, book_value)
    ps = _safe_div(market_cap, latest.revenue) if latest.revenue else None
    ev_ebitda = _safe_div(ev, latest.ebitda) if latest.ebitda else None
    ev_sales = _safe_div(ev, latest.revenue) if latest.revenue else None

    # PEG = PE / EPS growth rate (3Y)
    eps_cagr_3y = None
    if len(annual_results) >= 4:
        eps_cagr_3y = _cagr(annual_results[0].eps, annual_results[3].eps, 3)
    peg = _safe_div(pe, (eps_cagr_3y or 0) * 100) if eps_cagr_3y and eps_cagr_3y > 0 else None

    # ── Profitability ─────────────────────────────────────────
    rev = float(latest.revenue) if latest.revenue else None
    ebitda = float(latest.ebitda) if latest.ebitda else None
    ebit = float(latest.ebit) if latest.ebit else None
    pat = float(latest.pat) if latest.pat else None
    net_worth = float(latest.net_worth) if latest.net_worth else None
    total_assets = float(latest.total_assets) if latest.total_assets else None
    debt = float(latest.total_debt) if latest.total_debt else None

    roe = _safe_div(pat, net_worth)
    roa = _safe_div(pat, total_assets)

    # ROCE = EBIT / Capital Employed (Total Assets - Current Liabilities)
    # Approximation: EBIT / (Net Worth + Total Debt)
    capital_employed = (net_worth + debt) if net_worth and debt else net_worth
    roce = _safe_div(ebit, capital_employed) if ebit else None

    ebitda_margin = _safe_div(ebitda, rev)
    operating_margin = _safe_div(ebit, rev) if ebit else None
    net_margin = _safe_div(pat, rev)
    gross_margin = None  # requires gross profit data

    # ── Growth ────────────────────────────────────────────────
    rev_growth_1y = _safe_div(
        (float(annual_results[0].revenue) - float(annual_results[1].revenue)),
        float(annual_results[1].revenue)
    ) if len(annual_results) >= 2 and annual_results[1].revenue else None

    rev_cagr_3y = _cagr(annual_results[0].revenue, annual_results[min(3, len(annual_results)-1)].revenue, 3) if len(annual_results) >= 4 else None
    rev_cagr_5y = _cagr(annual_results[0].revenue, annual_results[min(5, len(annual_results)-1)].revenue, 5) if len(annual_results) >= 6 else None

    pat_growth_1y = _safe_div(
        (float(annual_results[0].pat) - float(annual_results[1].pat)),
        abs(float(annual_results[1].pat))
    ) if len(annual_results) >= 2 and annual_results[1].pat else None

    pat_cagr_3y = _cagr(annual_results[0].pat, annual_results[min(3, len(annual_results)-1)].pat, 3) if len(annual_results) >= 4 else None
    pat_cagr_5y = _cagr(annual_results[0].pat, annual_results[min(5, len(annual_results)-1)].pat, 5) if len(annual_results) >= 6 else None

    eps_cagr_5y = _cagr(annual_results[0].eps, annual_results[min(5, len(annual_results)-1)].eps, 5) if len(annual_results) >= 6 else None

    # Margin expansion (EBITDA margin change over 3Y)
    margin_expansion_3y = None
    if len(annual_results) >= 4 and annual_results[3].revenue and annual_results[3].ebitda:
        old_margin = float(annual_results[3].ebitda) / float(annual_results[3].revenue)
        margin_expansion_3y = ebitda_margin - old_margin if ebitda_margin else None

    # ── Leverage ──────────────────────────────────────────────
    debt_equity = _safe_div(debt, net_worth) if debt else 0.0
    debt_ebitda = _safe_div(debt, ebitda) if ebitda else None
    # BUG10 FIX: cast to float explicitly — Decimal subtraction can raise TypeError
    interest = (float(latest.ebit) - float(latest.pbt)) if latest.ebit and latest.pbt else None
    interest_coverage = _safe_div(ebit, interest) if interest and interest > 0 else None
    cash = float(latest.cash_and_equiv) if latest.cash_and_equiv else 0
    net_debt_val = (debt - cash) if debt else 0
    net_debt_equity = _safe_div(net_debt_val, net_worth) if net_worth else None

    # ── Cash Flow ─────────────────────────────────────────────
    cfo = float(latest.cfo) if latest.cfo else None
    capex = float(latest.capex) if latest.capex else None
    fcf = float(latest.free_cash_flow) if latest.free_cash_flow else None
    cfo_pat = _safe_div(cfo, pat) if cfo else None
    fcf_margin = _safe_div(fcf, rev) if fcf and rev else None

    # ── Dividend ──────────────────────────────────────────────
    divs = float(latest.dividends_paid) if latest.dividends_paid else None
    div_yield = _safe_div(divs, market_cap) if divs and market_cap else None
    div_payout = _safe_div(divs, pat) if divs and pat else None

    # ── Data Completeness ─────────────────────────────────────
    key_fields = [rev, ebitda, pat, net_worth, debt, cfo, shares, cmp]
    completeness = sum(1 for f in key_fields if f is not None) / len(key_fields)

    return {
        "as_of_date": date.today(),
        "market_cap": market_cap,
        "enterprise_value": ev,
        "pe": pe, "pb": pb, "ps": ps,
        "ev_ebitda": ev_ebitda, "ev_sales": ev_sales, "peg": peg,
        "roe": roe, "roce": roce, "roa": roa,
        "ebitda_margin": ebitda_margin, "operating_margin": operating_margin,
        "net_margin": net_margin, "gross_margin": gross_margin,
        "revenue_growth_1y": rev_growth_1y,
        "revenue_cagr_3y": rev_cagr_3y, "revenue_cagr_5y": rev_cagr_5y,
        "pat_growth_1y": pat_growth_1y,
        "pat_cagr_3y": pat_cagr_3y, "pat_cagr_5y": pat_cagr_5y,
        "eps_cagr_3y": eps_cagr_3y, "eps_cagr_5y": eps_cagr_5y,
        "margin_expansion_3y": margin_expansion_3y,
        "debt_equity": debt_equity, "debt_ebitda": debt_ebitda,
        "interest_coverage": interest_coverage, "net_debt_equity": net_debt_equity,
        "cfo_pat": cfo_pat, "fcf_margin": fcf_margin,
        "dividend_yield": div_yield, "dividend_payout": div_payout,
        "data_completeness": completeness,
        "computed_ok": True,
        "error_fields": [],
    }


async def run_ratio_engine():
    """Run ratio engine for all active stocks."""
    async with AsyncSessionLocal() as db:
        # Get all active stocks with quotes
        stocks_res = await db.execute(
            select(Stock).where(Stock.is_active == True)
        )
        stocks = stocks_res.scalars().all()

        success, failed = 0, 0
        for stock in stocks:
            try:
                # Get CMP from realtime_quotes
                from models.db_models import RealtimeQuote
                q_res = await db.execute(
                    select(RealtimeQuote).where(RealtimeQuote.nse_symbol == stock.nse_symbol)
                )
                quote = q_res.scalar_one_or_none()
                cmp = float(quote.ltp) if quote and quote.ltp else None
                if not cmp:
                    continue

                # Get annual results (last 10Y)
                annual_res = await db.execute(
                    select(FinancialResult)
                    .where(FinancialResult.nse_symbol == stock.nse_symbol,
                           FinancialResult.period_type == "A")
                    .order_by(FinancialResult.period_end.desc())
                    .limit(10)
                )
                annual = annual_res.scalars().all()

                # Get quarterly results (last 8Q)
                qtr_res = await db.execute(
                    select(FinancialResult)
                    .where(FinancialResult.nse_symbol == stock.nse_symbol,
                           FinancialResult.period_type == "Q")
                    .order_by(FinancialResult.period_end.desc())
                    .limit(8)
                )
                qtrs = qtr_res.scalars().all()

                if not annual:
                    continue

                ratios = compute_ratios(stock.nse_symbol, cmp, annual, qtrs)
                if not ratios:
                    continue

                # Upsert ratio record
                existing_res = await db.execute(
                    select(FinancialRatio)
                    .where(FinancialRatio.nse_symbol == stock.nse_symbol,
                           FinancialRatio.as_of_date == ratios["as_of_date"])
                )
                existing = existing_res.scalar_one_or_none()

                if existing:
                    for k, v in ratios.items():
                        if hasattr(existing, k):
                            setattr(existing, k, v)
                else:
                    ratio_obj = FinancialRatio(nse_symbol=stock.nse_symbol, **ratios)
                    db.add(ratio_obj)

                success += 1

            except Exception as e:
                log.warning(f"Ratio failed for {stock.nse_symbol}: {e}")
                failed += 1

        await db.commit()
        log.info(f"Ratio engine done: success={success}, failed={failed}")

        # Audit log
        audit = AuditLog(
            action="RATIO_ENGINE_RUN",
            details={"success": success, "failed": failed, "total": len(stocks)},
        )
        db.add(audit)
        await db.commit()
