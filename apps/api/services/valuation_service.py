"""
Sector-Specific Valuation Engine
Selects correct valuation model per sector and computes bear/base/bull intrinsic values.
"""
import logging
from datetime import date
from typing import Optional

import structlog
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from dependencies.db import AsyncSessionLocal
from models.db_models import (
    Stock, FinancialResult, FinancialRatio, SectorClassification,
    ValuationRun, ValuationAssumption, IntrinsicValue,
    ReverseDcfOutput, AuditLog
)

log = structlog.get_logger("valuation_engine")

# ─────────────────────────────────────────────────────────────
# Sector → Model mapping
# ─────────────────────────────────────────────────────────────
SECTOR_MODEL_MAP = {
    "Banks": "PB_ROE",
    "NBFCs": "PB_ROE",
    "Insurance": "PE_MULTIPLES",
    "IT Services": "DCF_FCFE",
    "Software Products": "DCF_FCFE",
    "FMCG": "DCF_PE",
    "Retail": "DCF_PE",
    "Pharma": "DCF_EV_EBITDA",
    "Hospitals": "EV_EBITDA",
    "Diagnostics": "EV_EBITDA",
    "Auto": "EV_EBITDA_FCFF",
    "Auto Ancillaries": "EV_EBITDA_FCFF",
    "Capital Goods": "EV_EBITDA_FCFF",
    "Metals": "EV_EBITDA_MIDCYCLE",
    "Cement": "EV_EBITDA_MIDCYCLE",
    "Chemicals": "DCF_EV_EBITDA",
    "Oil & Gas": "EV_EBITDA_FCFF",
    "Power": "DCF_REGULATED",
    "Renewables": "DCF_REGULATED",
    "Realty": "NAV_DCF",
    "Telecom": "DCF_EV_EBITDA",
}

# Default assumptions by scenario
DEFAULT_ASSUMPTIONS = {
    "BEAR": {
        "revenue_growth_yr1_5": 0.06,
        "revenue_growth_yr6_10": 0.04,
        "terminal_growth_rate": 0.03,
        "ebitda_margin_target": None,  # use actual - 2%
        "wacc": 0.14,
        "tax_rate": 0.25,
        "capex_pct_revenue": 0.06,
        "wc_change_pct_revenue": 0.02,
    },
    "BASE": {
        "revenue_growth_yr1_5": 0.12,
        "revenue_growth_yr6_10": 0.08,
        "terminal_growth_rate": 0.04,
        "ebitda_margin_target": None,  # use actual
        "wacc": 0.12,
        "tax_rate": 0.25,
        "capex_pct_revenue": 0.05,
        "wc_change_pct_revenue": 0.015,
    },
    "BULL": {
        "revenue_growth_yr1_5": 0.18,
        "revenue_growth_yr6_10": 0.12,
        "terminal_growth_rate": 0.05,
        "ebitda_margin_target": None,  # use actual + 2%
        "wacc": 0.11,
        "tax_rate": 0.22,
        "capex_pct_revenue": 0.04,
        "wc_change_pct_revenue": 0.01,
    },
}


def _safe(v, default=None):
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


# ─────────────────────────────────────────────────────────────
# DCF Engine
# ─────────────────────────────────────────────────────────────
def run_dcf(
    revenue: float,
    ebitda_margin: float,
    shares: int,
    net_debt: float,
    assumptions: dict,
    tax_rate: float = 0.25,
) -> Optional[float]:
    """
    10-year DCF returning intrinsic value per share.
    Free Cash Flow = EBITDA × (1 - tax) - Capex - ΔWC
    """
    try:
        wacc = assumptions["wacc"]
        tgr = assumptions["terminal_growth_rate"]
        g1 = assumptions["revenue_growth_yr1_5"]
        g2 = assumptions["revenue_growth_yr6_10"]
        capex_pct = assumptions["capex_pct_revenue"]
        wc_pct = assumptions["wc_change_pct_revenue"]
        # BUG12 FIX: use `is not None` instead of `or` to handle 0.0 margin correctly
        margin = assumptions["ebitda_margin_target"] if assumptions["ebitda_margin_target"] is not None else ebitda_margin

        pv_fcfs = 0.0
        rev = revenue
        for yr in range(1, 11):
            g = g1 if yr <= 5 else g2
            rev *= (1 + g)
            ebitda = rev * margin
            ebit = ebitda * 0.85  # assume D&A = 15% EBITDA
            nopat = ebit * (1 - tax_rate)
            capex = rev * capex_pct
            delta_wc = rev * wc_pct
            fcf = nopat + (ebitda - ebit) - capex - delta_wc  # + D&A back
            pv = fcf / (1 + wacc) ** yr
            pv_fcfs += pv

        # Terminal value (Gordon Growth)
        terminal_fcf = rev * margin * 0.85 * (1 - tax_rate) * (1 + tgr)
        terminal_value = terminal_fcf / (wacc - tgr)
        pv_terminal = terminal_value / (1 + wacc) ** 10

        equity_value = pv_fcfs + pv_terminal - net_debt
        if equity_value <= 0 or shares <= 0:
            return None
        return equity_value / shares

    except Exception as e:
        log.warning(f"DCF error: {e}")
        return None


def run_pb_roe_model(
    book_value_per_share: float,
    roe: float,
    cost_of_equity: float = 0.13,
) -> Optional[float]:
    """
    P/B justified = ROE / Cost of Equity × Book Value
    Used for Banks/NBFCs.
    """
    try:
        justified_pb = roe / cost_of_equity
        justified_pb = max(0.5, min(justified_pb, 6.0))  # cap at reasonable range
        return justified_pb * book_value_per_share
    except Exception:
        return None


def run_ev_ebitda_model(
    ebitda: float,
    net_debt: float,
    shares: int,
    target_multiple: float,
) -> Optional[float]:
    """
    IV = (EBITDA × target_multiple - net_debt) / shares
    """
    try:
        equity_value = ebitda * target_multiple - net_debt
        if equity_value <= 0 or shares <= 0:
            return None
        return equity_value / shares
    except Exception:
        return None


def run_pe_model(eps: float, target_pe: float) -> Optional[float]:
    try:
        return eps * target_pe if eps and eps > 0 else None
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────
# Reverse DCF
# ─────────────────────────────────────────────────────────────
def compute_reverse_dcf(
    cmp: float,
    eps: float,
    revenue: float,
    ebitda_margin: float,
    net_debt: float,
    shares: int,
    historical_growth: float,
) -> dict:
    """
    Binary search for implied growth rate that makes DCF = CMP.
    """
    lo, hi = -0.10, 0.60
    for _ in range(50):
        mid = (lo + hi) / 2
        assumptions = {**DEFAULT_ASSUMPTIONS["BASE"], "revenue_growth_yr1_5": mid, "revenue_growth_yr6_10": mid / 2}
        iv = run_dcf(revenue, ebitda_margin, shares, net_debt, assumptions)
        if iv is None:
            break
        if abs(iv - cmp) < 1:
            break
        if iv < cmp:
            lo = mid
        else:
            hi = mid

    implied = (lo + hi) / 2
    premium = ((implied - historical_growth) / abs(historical_growth)) if historical_growth else None

    return {
        "implied_growth_rate": implied,
        "historical_growth_rate": historical_growth,
        "premium_discount_pct": premium,
        "interpretation": (
            f"Current price implies {implied*100:.1f}% annual revenue growth for 10 years. "
            f"Historical 3Y CAGR is {(historical_growth or 0)*100:.1f}%. "
            + ("Appears aggressively priced." if implied > (historical_growth or 0) * 1.5 else
               "Priced reasonably relative to history.")
        ),
    }


# ─────────────────────────────────────────────────────────────
# Main Valuation Engine
# ─────────────────────────────────────────────────────────────
async def run_valuation_engine():
    async with AsyncSessionLocal() as db:
        # BUG8 FIX: Must eagerly load stock.sector to avoid MissingGreenlet in async
        from sqlalchemy.orm import selectinload
        stocks_res = await db.execute(
            select(Stock)
            .where(Stock.is_active == True)
            .options(selectinload(Stock.sector))
        )
        stocks = stocks_res.scalars().all()

        success, skipped, failed = 0, 0, 0

        for stock in stocks:
            try:
                # Get CMP
                from models.db_models import RealtimeQuote
                q_res = await db.execute(select(RealtimeQuote).where(RealtimeQuote.nse_symbol == stock.nse_symbol))
                quote = q_res.scalar_one_or_none()
                cmp = _safe(quote.ltp) if quote else None
                if not cmp:
                    skipped += 1
                    continue

                # Get latest ratios
                ratio_res = await db.execute(
                    select(FinancialRatio).where(FinancialRatio.nse_symbol == stock.nse_symbol)
                    .order_by(FinancialRatio.as_of_date.desc()).limit(1)
                )
                ratio = ratio_res.scalar_one_or_none()

                # Get latest annual result
                fin_res = await db.execute(
                    select(FinancialResult)
                    .where(FinancialResult.nse_symbol == stock.nse_symbol, FinancialResult.period_type == "A")
                    .order_by(FinancialResult.period_end.desc()).limit(5)
                )
                annual = fin_res.scalars().all()
                if not annual:
                    skipped += 1
                    continue

                latest = annual[0]
                revenue = _safe(latest.revenue)
                ebitda = _safe(latest.ebitda)
                pat = _safe(latest.pat)
                shares = latest.shares_outstanding or 1
                net_worth = _safe(latest.net_worth)
                total_debt = _safe(latest.total_debt, 0)
                cash = _safe(latest.cash_and_equiv, 0)
                net_debt = total_debt - cash
                eps = _safe(latest.eps)
                book_value = _safe(latest.book_value_per_share) or (_safe(latest.net_worth, 0) / shares if shares else None)

                ebitda_margin = (ebitda / revenue) if ebitda and revenue else 0.10
                roe = _safe(ratio.roe) if ratio else None
                rev_cagr_3y = _safe(ratio.revenue_cagr_3y) if ratio else None

                # Determine model by sector
                sector_name = stock.sector.sector if stock.sector else "default"
                model = SECTOR_MODEL_MAP.get(sector_name, "DCF_FCFF")
                primary_model = model

                iv_bear, iv_base, iv_bull = None, None, None

                for scenario in ["BEAR", "BASE", "BULL"]:
                    assume = dict(DEFAULT_ASSUMPTIONS[scenario])
                    if assume["ebitda_margin_target"] is None:
                        adjust = {"BEAR": -0.02, "BASE": 0.0, "BULL": 0.02}[scenario]
                        assume["ebitda_margin_target"] = ebitda_margin + adjust

                    iv = None

                    if model in ("DCF_FCFE", "DCF_FCFF", "DCF_PE", "DCF_EV_EBITDA", "DCF_REGULATED") and revenue and shares:
                        iv = run_dcf(revenue, ebitda_margin, shares, net_debt, assume)

                    elif model == "PB_ROE" and roe and book_value:
                        cost_eq = {"BEAR": 0.15, "BASE": 0.13, "BULL": 0.12}[scenario]
                        iv = run_pb_roe_model(book_value, roe, cost_eq)

                    elif model in ("EV_EBITDA", "EV_EBITDA_FCFF", "EV_EBITDA_MIDCYCLE") and ebitda and shares:
                        # Sector target multiples
                        base_multiple = {"IT Services": 20, "FMCG": 18, "Pharma": 16,
                                         "Banks": 12, "Auto": 10, "Metals": 7, "Cement": 9}.get(sector_name, 12)
                        mult_adj = {"BEAR": 0.8, "BASE": 1.0, "BULL": 1.25}[scenario]
                        iv = run_ev_ebitda_model(ebitda, net_debt, shares, base_multiple * mult_adj)

                    elif model == "PE_MULTIPLES" and eps:
                        base_pe = 18
                        pe_adj = {"BEAR": 0.8, "BASE": 1.0, "BULL": 1.3}[scenario]
                        iv = run_pe_model(eps, base_pe * pe_adj)

                    if scenario == "BEAR":
                        iv_bear = iv
                    elif scenario == "BASE":
                        iv_base = iv
                    else:
                        iv_bull = iv

                # Blended = 25% bear + 50% base + 25% bull
                valid = [v for v in [iv_bear, iv_base, iv_bull] if v is not None]
                if not valid:
                    skipped += 1
                    continue

                if all(v is not None for v in [iv_bear, iv_base, iv_bull]):
                    iv_blended = 0.25 * iv_bear + 0.50 * iv_base + 0.25 * iv_bull
                else:
                    iv_blended = sum(valid) / len(valid)

                upside_pct = ((iv_blended - cmp) / cmp) * 100
                mos = 1 - (cmp / iv_blended) if iv_blended else None

                # Write valuation run
                run = ValuationRun(
                    nse_symbol=stock.nse_symbol,
                    models_used=[model],
                    iv_bear=iv_bear,
                    iv_base=iv_base,
                    iv_bull=iv_bull,
                    iv_blended=iv_blended,
                    cmp=cmp,
                    upside_pct=upside_pct,
                    margin_of_safety=mos,
                    sector_id=stock.sector_id,
                    primary_model=primary_model,
                    status="SUCCESS",
                )
                db.add(run)

                # Upsert intrinsic_values (latest per stock)
                iv_res = await db.execute(select(IntrinsicValue).where(IntrinsicValue.nse_symbol == stock.nse_symbol))
                iv_record = iv_res.scalar_one_or_none()
                if iv_record:
                    iv_record.iv_bear = iv_bear
                    iv_record.iv_base = iv_base
                    iv_record.iv_bull = iv_bull
                    iv_record.iv_blended = iv_blended
                    iv_record.cmp = cmp
                    iv_record.upside_pct = upside_pct
                    iv_record.margin_of_safety = mos
                    iv_record.primary_model = primary_model
                else:
                    db.add(IntrinsicValue(
                        nse_symbol=stock.nse_symbol,
                        iv_bear=iv_bear, iv_base=iv_base, iv_bull=iv_bull,
                        iv_blended=iv_blended, cmp=cmp, upside_pct=upside_pct,
                        margin_of_safety=mos, primary_model=primary_model,
                    ))

                # Reverse DCF
                if revenue and eps and shares:
                    rdcf = compute_reverse_dcf(cmp, eps, revenue, ebitda_margin, net_debt, shares, rev_cagr_3y or 0.10)
                    rdcf_obj = ReverseDcfOutput(
                        nse_symbol=stock.nse_symbol,
                        cmp=cmp,
                        implied_growth_rate=rdcf["implied_growth_rate"],
                        historical_growth_rate=rdcf["historical_growth_rate"],
                        premium_discount_pct=rdcf["premium_discount_pct"],
                        interpretation=rdcf["interpretation"],
                        wacc_used=DEFAULT_ASSUMPTIONS["BASE"]["wacc"],
                        terminal_growth_used=DEFAULT_ASSUMPTIONS["BASE"]["terminal_growth_rate"],
                    )
                    db.add(rdcf_obj)

                success += 1

            except Exception as e:
                log.error(f"Valuation failed for {stock.nse_symbol}: {e}")
                failed += 1

        await db.commit()
        log.info(f"Valuation engine done: success={success}, skipped={skipped}, failed={failed}")

        audit = AuditLog(
            action="VALUATION_ENGINE_RUN",
            details={"success": success, "skipped": skipped, "failed": failed},
        )
        db.add(audit)
        await db.commit()
