"""
Screener Router — pre-built screens with real DB queries + custom screener
"""
from typing import Optional
from fastapi import APIRouter, Depends, Body
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_
from sqlalchemy.orm import selectinload
from dependencies.db import get_db
from models.db_models import Stock, Signal, IntrinsicValue, MLScores, FinancialRatio, SectorClassification

router = APIRouter()

PREBUILT_SCREENERS = [
    {"id": "green_undervalued",           "name": "🟢 Green Undervalued",         "description": "Signal=GREEN AND Upside ≥ 25%"},
    {"id": "high_roce",                   "name": "High ROCE Stocks",              "description": "ROCE ≥ 20% AND low debt"},
    {"id": "low_debt_compounders",        "name": "Low Debt Compounders",          "description": "D/E < 0.5 AND ROCE ≥ 15%"},
    {"id": "garp",                        "name": "GARP",                          "description": "PEG < 1 AND Growth > 15%"},
    {"id": "cashflow_kings",              "name": "Cash Flow Kings",               "description": "CFO/PAT ≥ 1 AND FCF > 0"},
    {"id": "deep_value",                  "name": "Deep Value",                    "description": "Upside ≥ 40% AND Risk ≤ 50"},
    {"id": "dividend_quality",            "name": "Dividend Quality",              "description": "Yield > 2% AND Payout < 50%"},
    {"id": "high_ml_confidence",          "name": "High ML Confidence Undervalued","description": "ML Confidence=HIGH AND Upside ≥ 20%"},
    {"id": "nifty50_green",               "name": "Nifty50 Opportunities",         "description": "Nifty50 stocks with Green signal"},
    {"id": "turnaround",                  "name": "Turnaround Candidates",         "description": "PAT improving AND low debt"},
    {"id": "quality_compounder_cluster",  "name": "Quality Compounder Cluster",    "description": "ML cluster = QUALITY_COMPOUNDER"},
]


@router.get("/prebuilt")
async def list_prebuilt():
    return {"screeners": PREBUILT_SCREENERS}


@router.get("/prebuilt/{screener_id}")
async def run_prebuilt(screener_id: str, db: AsyncSession = Depends(get_db)):
    # BUG11 FIX: Use a subquery to get the latest FinancialRatio row per symbol.
    # A plain JOIN on financial_ratios returns ALL date rows, causing duplicates.
    from sqlalchemy import func as sqlfunc
    latest_ratio_subq = (
        select(
            FinancialRatio,
            sqlfunc.row_number().over(
                partition_by=FinancialRatio.nse_symbol,
                order_by=FinancialRatio.as_of_date.desc(),
            ).label("_rn")
        ).subquery()
    )
    LatestRatio = aliased(FinancialRatio, latest_ratio_subq)

    # Build base query
    base = (
        select(
            Stock.nse_symbol, Stock.company_name, Stock.market_cap_category,
            Stock.is_nifty50, Stock.is_nifty500,
            SectorClassification.sector,
            Signal.signal_color, Signal.signal_label, Signal.main_reason,
            IntrinsicValue.iv_blended, IntrinsicValue.upside_pct, IntrinsicValue.margin_of_safety,
            MLScores.risk_score, MLScores.fundamental_score, MLScores.growth_outlook_score,
            MLScores.valuation_confidence, MLScores.cluster_label,
            LatestRatio.roce, LatestRatio.debt_equity, LatestRatio.cfo_pat,
            LatestRatio.fcf_margin, LatestRatio.dividend_yield, LatestRatio.dividend_payout,
            LatestRatio.peg, LatestRatio.revenue_cagr_3y, LatestRatio.pe,
        )
        .join(Signal, Signal.nse_symbol == Stock.nse_symbol, isouter=True)
        .join(IntrinsicValue, IntrinsicValue.nse_symbol == Stock.nse_symbol, isouter=True)
        .join(MLScores, MLScores.nse_symbol == Stock.nse_symbol, isouter=True)
        .join(LatestRatio, and_(LatestRatio.nse_symbol == Stock.nse_symbol, latest_ratio_subq.c._rn == 1), isouter=True)
        .join(SectorClassification, SectorClassification.id == Stock.sector_id, isouter=True)
        .where(Stock.is_active == True)
    )

    # Apply screener-specific filters
    if screener_id == "green_undervalued":
        base = base.where(
            Signal.signal_color == "GREEN",
            IntrinsicValue.upside_pct >= 25,
        )
    elif screener_id == "high_roce":
        base = base.where(FinancialRatio.roce >= 0.20)
    elif screener_id == "low_debt_compounders":
        base = base.where(
            or_(FinancialRatio.debt_equity <= 0.5, FinancialRatio.debt_equity == None),
            FinancialRatio.roce >= 0.15,
        )
    elif screener_id == "garp":
        base = base.where(
            FinancialRatio.peg <= 1.5,
            FinancialRatio.revenue_cagr_3y >= 0.12,
        )
    elif screener_id == "cashflow_kings":
        base = base.where(
            FinancialRatio.cfo_pat >= 1.0,
            FinancialRatio.fcf_margin > 0,
        )
    elif screener_id == "deep_value":
        base = base.where(
            IntrinsicValue.upside_pct >= 40,
            MLScores.risk_score <= 50,
        )
    elif screener_id == "dividend_quality":
        base = base.where(
            FinancialRatio.dividend_yield >= 0.02,
            FinancialRatio.dividend_payout <= 0.50,
        )
    elif screener_id == "high_ml_confidence":
        base = base.where(
            MLScores.valuation_confidence == "HIGH",
            IntrinsicValue.upside_pct >= 20,
        )
    elif screener_id == "nifty50_green":
        base = base.where(
            Stock.is_nifty50 == True,
            Signal.signal_color == "GREEN",
        )
    elif screener_id == "turnaround":
        base = base.where(
            MLScores.fundamental_score >= 40,
            or_(FinancialRatio.debt_equity <= 1.0, FinancialRatio.debt_equity == None),
            Signal.signal_color.in_(["GREEN", "YELLOW"]),
        )
    elif screener_id == "quality_compounder_cluster":
        base = base.where(MLScores.cluster_label == "QUALITY_COMPOUNDER")
    else:
        return {"screener_id": screener_id, "results": [], "error": "Unknown screener ID"}

    base = base.order_by(IntrinsicValue.upside_pct.desc().nullslast()).limit(200)
    result = await db.execute(base)
    rows = result.all()

    return {
        "screener_id": screener_id,
        "count": len(rows),
        "results": [
            {
                "nse_symbol": r.nse_symbol,
                "company_name": r.company_name,
                "sector": r.sector,
                "market_cap_category": r.market_cap_category,
                "is_nifty50": r.is_nifty50,
                "signal_color": r.signal_color or "GREY",
                "signal_label": r.signal_label or "Insufficient Data",
                "iv_blended": float(r.iv_blended) if r.iv_blended else None,
                "upside_pct": float(r.upside_pct) if r.upside_pct else None,
                "margin_of_safety": float(r.margin_of_safety) if r.margin_of_safety else None,
                "risk_score": r.risk_score,
                "fundamental_score": r.fundamental_score,
                "growth_score": r.growth_outlook_score,
                "valuation_confidence": r.valuation_confidence,
                "cluster_label": r.cluster_label,
                "roce": float(r.roce) if r.roce else None,
                "debt_equity": float(r.debt_equity) if r.debt_equity else None,
                "cfo_pat": float(r.cfo_pat) if r.cfo_pat else None,
            }
            for r in rows
        ],
    }


@router.post("/custom")
async def run_custom(conditions: dict = Body(...), db: AsyncSession = Depends(get_db)):
    """
    Custom screener with flexible conditions.
    Accepts: {min_upside, max_risk, min_fundamental, signal_colors, min_roce, max_de, sectors, market_cap_categories}
    """
    filters = []

    min_upside = conditions.get("min_upside")
    if min_upside is not None:
        filters.append(IntrinsicValue.upside_pct >= float(min_upside))

    max_upside = conditions.get("max_upside")
    if max_upside is not None:
        filters.append(IntrinsicValue.upside_pct <= float(max_upside))

    max_risk = conditions.get("max_risk")
    if max_risk is not None:
        filters.append(MLScores.risk_score <= int(max_risk))

    min_fundamental = conditions.get("min_fundamental")
    if min_fundamental is not None:
        filters.append(MLScores.fundamental_score >= int(min_fundamental))

    signal_colors = conditions.get("signal_colors")
    if signal_colors:
        filters.append(Signal.signal_color.in_(signal_colors))

    min_roce = conditions.get("min_roce")
    if min_roce is not None:
        filters.append(FinancialRatio.roce >= float(min_roce) / 100)

    max_de = conditions.get("max_de")
    if max_de is not None:
        filters.append(or_(FinancialRatio.debt_equity <= float(max_de), FinancialRatio.debt_equity == None))

    sectors = conditions.get("sectors")
    if sectors:
        filters.append(SectorClassification.sector.in_(sectors))

    cap_cats = conditions.get("market_cap_categories")
    if cap_cats:
        filters.append(Stock.market_cap_category.in_(cap_cats))

    is_nifty50 = conditions.get("is_nifty50")
    if is_nifty50 is not None:
        filters.append(Stock.is_nifty50 == bool(is_nifty50))

    base = (
        select(
            Stock.nse_symbol, Stock.company_name, Stock.market_cap_category,
            SectorClassification.sector,
            Signal.signal_color, Signal.signal_label,
            IntrinsicValue.upside_pct, IntrinsicValue.iv_blended,
            MLScores.risk_score, MLScores.fundamental_score,
        )
        .join(Signal, Signal.nse_symbol == Stock.nse_symbol, isouter=True)
        .join(IntrinsicValue, IntrinsicValue.nse_symbol == Stock.nse_symbol, isouter=True)
        .join(MLScores, MLScores.nse_symbol == Stock.nse_symbol, isouter=True)
        .join(FinancialRatio, FinancialRatio.nse_symbol == Stock.nse_symbol, isouter=True)
        .join(SectorClassification, SectorClassification.id == Stock.sector_id, isouter=True)
        .where(Stock.is_active == True, *filters)
        .order_by(IntrinsicValue.upside_pct.desc().nullslast())
        .limit(500)
    )
    result = await db.execute(base)
    rows = result.all()

    return {
        "conditions": conditions,
        "count": len(rows),
        "results": [
            {
                "nse_symbol": r.nse_symbol,
                "company_name": r.company_name,
                "sector": r.sector,
                "market_cap_category": r.market_cap_category,
                "signal_color": r.signal_color or "GREY",
                "signal_label": r.signal_label or "Insufficient Data",
                "upside_pct": float(r.upside_pct) if r.upside_pct else None,
                "iv_blended": float(r.iv_blended) if r.iv_blended else None,
                "risk_score": r.risk_score,
                "fundamental_score": r.fundamental_score,
            }
            for r in rows
        ],
    }
