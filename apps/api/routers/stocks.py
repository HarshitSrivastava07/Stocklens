"""
Stocks Router — full stock data including signals, ratios, valuation
Now supports global stocks across multiple exchanges.
"""
from typing import Optional, List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func, or_
from sqlalchemy.orm import selectinload

from dependencies.db import get_db
from models.db_models import (
    Stock, FinancialRatio, IntrinsicValue, MLScores,
    Signal, RiskFlag, FinancialResult, ShareholdingPattern,
    ReverseDcfOutput, SectorClassification, RealtimeQuote, MLClusterResult
)

# Currency symbol map for display convenience
CURRENCY_SYMBOLS = {
    "INR": "₹", "USD": "$", "GBP": "£", "EUR": "€",
    "JPY": "¥", "HKD": "HK$", "AUD": "A$", "SGD": "S$",
}

def _f(v) -> float | None:
    return float(v) if v is not None else None

router = APIRouter()


# ─────────────────────────────────────────────────────────────
# GET /stocks — paginated list with signals merged
# ─────────────────────────────────────────────────────────────
@router.get("")
async def list_stocks(
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    sector: Optional[str] = Query(None),
    instrument_type: Optional[str] = Query(None, regex="^(EQ|ETF|INDEX|FO)$"),
    signal_color: Optional[str] = Query(None, regex="^(GREEN|YELLOW|RED|GREY)$"),
    is_nifty50: Optional[bool] = Query(None),
    min_upside: Optional[float] = Query(None),
    include_signals: bool = Query(False),
    # ── New global filters ──
    exchange: Optional[str] = Query(None, description="Filter by exchange: NSE, NYSE, NASDAQ, LSE, TSE, HKEX, XETRA, ASX"),
    country: Optional[str] = Query(None, description="Filter by country: India, USA, UK, Japan, etc."),
    currency: Optional[str] = Query(None, description="Filter by currency: INR, USD, GBP, EUR, JPY"),
    search: Optional[str] = Query(None, description="Search by symbol or company name"),
    db: AsyncSession = Depends(get_db),
):
    # Base query
    where_clauses = [Stock.is_active == True]

    if instrument_type:
        where_clauses.append(Stock.instrument_type == instrument_type)
    if is_nifty50 is not None:
        where_clauses.append(Stock.is_nifty50 == is_nifty50)
    if exchange:
        where_clauses.append(Stock.exchange == exchange.upper())
    if country:
        where_clauses.append(Stock.country == country)
    if currency:
        where_clauses.append(Stock.currency == currency.upper())
    if search:
        pattern = f"%{search.upper()}%"
        where_clauses.append(
            or_(
                func.upper(Stock.nse_symbol).like(pattern),
                func.upper(Stock.company_name).like(pattern),
                func.upper(Stock.yahoo_ticker).like(pattern),
            )
        )

    query = (
        select(
            Stock,
            SectorClassification.sector.label("sector_name"),
            SectorClassification.industry,
            Signal.signal_color,
            Signal.signal_label,
            Signal.main_reason,
            RealtimeQuote.ltp,
            RealtimeQuote.change_pct,
            RealtimeQuote.change_abs,
            RealtimeQuote.week_52_high,
            RealtimeQuote.week_52_low,
            RealtimeQuote.market_cap,
            RealtimeQuote.data_source.label("data_freshness"),
            RealtimeQuote.is_stale,
            IntrinsicValue.iv_blended,
            IntrinsicValue.upside_pct,
            IntrinsicValue.margin_of_safety,
            IntrinsicValue.primary_model,
            IntrinsicValue.valuation_confidence,
            MLScores.fundamental_score,
            MLScores.growth_outlook_score,
            MLScores.risk_score,
            MLClusterResult.cluster_label,
        )
        .outerjoin(SectorClassification, Stock.sector_id == SectorClassification.id)
        .outerjoin(Signal, Stock.nse_symbol == Signal.nse_symbol)
        .outerjoin(RealtimeQuote, Stock.nse_symbol == RealtimeQuote.nse_symbol)
        .outerjoin(IntrinsicValue, Stock.nse_symbol == IntrinsicValue.nse_symbol)
        .outerjoin(MLScores, Stock.nse_symbol == MLScores.nse_symbol)
        .outerjoin(MLClusterResult, Stock.nse_symbol == MLClusterResult.nse_symbol)
        .where(and_(*where_clauses))
        .order_by(Stock.exchange, Stock.nse_symbol)
        .limit(limit)
        .offset(offset)
    )

    result = await db.execute(query)
    rows_data = result.all()

    # Count total (with same filters)
    count_query = select(func.count()).select_from(Stock).where(and_(*where_clauses))
    total = await db.scalar(count_query)

    def stock_row(row) -> dict:
        s = row.Stock
        curr = s.currency or "INR"
        raw_ltp = float(row.ltp) if row.ltp else None
        data_src = row.data_freshness or "UNKNOWN"  # actually data_source column
        is_mock  = data_src in ("MOCK", "SEED")
        # Never serve a mock price as real — return None so UI shows N/A
        ltp      = None if (is_mock or not raw_ltp or raw_ltp <= 0) else raw_ltp

        return {
            "nse_symbol":      s.nse_symbol,
            "company_name":    s.company_name,
            "sector":          row.sector_name,
            "industry":        row.industry,
            "instrument_type": s.instrument_type,
            "market_cap_category": s.market_cap_category,
            "is_nifty50":      s.is_nifty50,
            "is_nifty500":     s.is_nifty500,
            "exchange":        s.exchange,
            "currency":        curr,
            "currency_symbol": CURRENCY_SYMBOLS.get(curr, curr),
            "yahoo_ticker":    s.yahoo_ticker,
            "country":         s.country,
            "signal_color":    row.signal_color or "GREY",
            "signal_label":    row.signal_label or "Insufficient Data",
            "main_reason":     row.main_reason,
            "ltp":             ltp,
            "is_mock_price":   is_mock or ltp is None,
            "change_pct":      None if is_mock else (float(row.change_pct) if row.change_pct else None),
            "change_abs":      None if is_mock else (float(row.change_abs) if row.change_abs else None),
            "week_52_high":    None if is_mock else (float(row.week_52_high) if row.week_52_high else None),
            "week_52_low":     None if is_mock else (float(row.week_52_low) if row.week_52_low else None),
            "market_cap":      None if is_mock else (float(row.market_cap) if row.market_cap else None),
            "data_freshness":  "UNAVAILABLE" if is_mock else (data_src or "UNKNOWN"),
            "is_stale":        row.is_stale if row.is_stale is not None else True,
            "iv_blended":      float(row.iv_blended) if row.iv_blended else None,
            "upside_pct":      float(row.upside_pct) if row.upside_pct else None,
            "margin_of_safety": float(row.margin_of_safety) if row.margin_of_safety else None,
            "primary_model":   row.primary_model,
            "valuation_confidence": row.valuation_confidence,
            "fundamental_score": row.fundamental_score,
            "growth_score":    row.growth_outlook_score,
            "risk_score":      row.risk_score,
            "cluster_label":   row.cluster_label,
        }

    rows = [stock_row(r) for r in rows_data]

    # Post-query filters (can't easily do in SQL for these)
    if signal_color:
        rows = [r for r in rows if r["signal_color"] == signal_color]
    if min_upside is not None:
        rows = [r for r in rows if (r["upside_pct"] or -999) >= min_upside]
    if sector:
        rows = [r for r in rows if r["sector"] == sector]

    return {"stocks": rows, "total": total, "limit": limit, "offset": offset}


# ─────────────────────────────────────────────────────────────
# GET /stocks/{symbol} — full stock profile
# ─────────────────────────────────────────────────────────────
@router.get("/{symbol}")
async def get_stock(symbol: str, db: AsyncSession = Depends(get_db)):
    symbol = symbol.upper().strip()

    result = await db.execute(
        select(Stock)
        .where(Stock.nse_symbol == symbol)
        .options(
            selectinload(Stock.sector),
            selectinload(Stock.realtime_quote),
            selectinload(Stock.signal),
            selectinload(Stock.intrinsic_value),
            selectinload(Stock.ml_scores),
            selectinload(Stock.risk_flags),
        )
    )
    stock = result.scalar_one_or_none()
    if not stock:
        raise HTTPException(status_code=404, detail=f"Stock {symbol} not found")

    sig = stock.signal
    iv = stock.intrinsic_value
    ml = stock.ml_scores
    q = stock.realtime_quote

    return {
        "nse_symbol": stock.nse_symbol,
        "bse_code": stock.bse_code,
        "isin": stock.isin,
        "company_name": stock.company_name,
        "instrument_type": stock.instrument_type,
        "market_cap_category": stock.market_cap_category,
        "face_value": float(stock.face_value) if stock.face_value else None,
        "exchange": stock.exchange,
        "currency": stock.currency,
        "currency_symbol": CURRENCY_SYMBOLS.get(stock.currency or "INR", "₹"),
        "country": stock.country,
        "sector": {
            "macro_sector": stock.sector.macro_sector if stock.sector else None,
            "sector": stock.sector.sector if stock.sector else None,
            "industry": stock.sector.industry if stock.sector else None,
        },
        "is_nifty50": stock.is_nifty50,
        "is_nifty500": stock.is_nifty500,
        "is_fno": stock.is_fno,
        "quote": {
            "ltp": float(q.ltp) if q and q.ltp else None,
            "open": float(q.open) if q and q.open else None,
            "high": float(q.high) if q and q.high else None,
            "low": float(q.low) if q and q.low else None,
            "close": float(q.close) if q and q.close else None,
            "volume": q.volume if q else None,
            "change_abs": float(q.change_abs) if q and q.change_abs else None,
            "change_pct": float(q.change_pct) if q and q.change_pct else None,
            "week_52_high": float(q.week_52_high) if q and q.week_52_high else None,
            "week_52_low": float(q.week_52_low) if q and q.week_52_low else None,
            "is_stale": q.is_stale if q else True,
            "last_updated": q.last_updated.isoformat() if q and q.last_updated else None,
        } if q else None,
        "signal": {
            "signal": sig.signal if sig else "INSUFFICIENT_DATA",
            "signal_color": sig.signal_color if sig else "GREY",
            "signal_label": sig.signal_label if sig else "Insufficient Data",
            "main_reason": sig.main_reason if sig else None,
            "conditions": sig.conditions if sig else {},
            "blocking_flags": sig.blocking_flags if sig else [],
            "data_freshness": sig.data_freshness if sig else "UNKNOWN",
        },
        "intrinsic_value": {
            "iv_bear": float(iv.iv_bear) if iv and iv.iv_bear else None,
            "iv_base": float(iv.iv_base) if iv and iv.iv_base else None,
            "iv_bull": float(iv.iv_bull) if iv and iv.iv_bull else None,
            "iv_blended": float(iv.iv_blended) if iv and iv.iv_blended else None,
            "cmp": float(iv.cmp) if iv and iv.cmp else None,
            "upside_pct": float(iv.upside_pct) if iv and iv.upside_pct else None,
            "margin_of_safety": float(iv.margin_of_safety) if iv and iv.margin_of_safety else None,
            "primary_model": iv.primary_model if iv else None,
            "valuation_confidence": iv.valuation_confidence if iv else None,
            "updated_at": iv.updated_at.isoformat() if iv and iv.updated_at else None,
        } if iv else None,
        "ml_scores": {
            "risk_score": ml.risk_score if ml else None,
            "risk_level": ml.risk_level if ml else None,
            "risk_drivers": ml.risk_drivers if ml else [],
            "fundamental_score": ml.fundamental_score if ml else None,
            "growth_outlook_score": ml.growth_outlook_score if ml else None,
            "valuation_confidence_score": ml.valuation_confidence_score if ml else None,
            "valuation_confidence": ml.valuation_confidence if ml else None,
        } if ml else None,
        "risk_flags": [
            {
                "flag_type": f.flag_type,
                "severity": f.severity,
                "description": f.description,
                "detected_at": f.detected_at.isoformat() if f.detected_at else None,
            }
            for f in stock.risk_flags
            if f.is_active
        ],
    }


# ─────────────────────────────────────────────────────────────
# GET /stocks/{symbol}/financials
# ─────────────────────────────────────────────────────────────
@router.get("/{symbol}/financials")
async def get_financials(
    symbol: str,
    period_type: str = Query("A", regex="^(Q|A)$"),
    limit: int = Query(10, ge=1, le=40),
    db: AsyncSession = Depends(get_db),
):
    symbol = symbol.upper().strip()
    result = await db.execute(
        select(FinancialResult)
        .where(
            and_(
                FinancialResult.nse_symbol == symbol,
                FinancialResult.period_type == period_type,
            )
        )
        .order_by(FinancialResult.period_end.desc())
        .limit(limit)
    )
    records = result.scalars().all()

    def ser(r: FinancialResult) -> dict:
        return {
            "period_end": r.period_end.isoformat() if r.period_end else None,
            "period_type": r.period_type,
            "revenue": float(r.revenue) if r.revenue else None,
            "ebitda": float(r.ebitda) if r.ebitda else None,
            "pat": float(r.pat) if r.pat else None,
            "eps": float(r.eps) if r.eps else None,
            "total_debt": float(r.total_debt) if r.total_debt else None,
            "cash_and_equiv": float(r.cash_and_equiv) if r.cash_and_equiv else None,
            "cfo": float(r.cfo) if r.cfo else None,
            "free_cash_flow": float(r.free_cash_flow) if r.free_cash_flow else None,
            "net_worth": float(r.net_worth) if r.net_worth else None,
            "shares_outstanding": r.shares_outstanding,
            "roe_bank": float(r.roe_bank) if r.roe_bank else None,
            "nim": float(r.nim) if r.nim else None,
            "gnpa_pct": float(r.gnpa_pct) if r.gnpa_pct else None,
            "confidence_level": r.confidence_level,
            "data_source": r.data_source,
            "is_verified": r.is_verified,
        }

    return {"symbol": symbol, "period_type": period_type, "results": [ser(r) for r in records]}


# ─────────────────────────────────────────────────────────────
# GET /stocks/{symbol}/ratios
# ─────────────────────────────────────────────────────────────
@router.get("/{symbol}/ratios")
async def get_ratios(symbol: str, db: AsyncSession = Depends(get_db)):
    symbol = symbol.upper().strip()
    result = await db.execute(
        select(FinancialRatio)
        .where(FinancialRatio.nse_symbol == symbol)
        .order_by(FinancialRatio.as_of_date.desc())
        .limit(1)
    )
    ratio = result.scalar_one_or_none()
    if not ratio:
        raise HTTPException(status_code=404, detail="Ratios not yet computed")

    return {
        "symbol": symbol,
        "as_of_date": ratio.as_of_date.isoformat() if ratio.as_of_date else None,
        "computed_at": ratio.computed_at.isoformat() if ratio.computed_at else None,
        "data_completeness": float(ratio.data_completeness) if ratio.data_completeness else None,
        "valuation": {
            "pe": _f(ratio.pe), "pb": _f(ratio.pb), "ps": _f(ratio.ps),
            "ev_ebitda": _f(ratio.ev_ebitda), "ev_sales": _f(ratio.ev_sales),
            "peg": _f(ratio.peg), "market_cap": _f(ratio.market_cap),
            "enterprise_value": _f(ratio.enterprise_value),
        },
        "profitability": {
            "roe": _f(ratio.roe), "roce": _f(ratio.roce), "roa": _f(ratio.roa),
            "roic": _f(ratio.roic), "gross_margin": _f(ratio.gross_margin),
            "ebitda_margin": _f(ratio.ebitda_margin),
            "operating_margin": _f(ratio.operating_margin),
            "net_margin": _f(ratio.net_margin),
        },
        "growth": {
            "revenue_growth_1y": _f(ratio.revenue_growth_1y),
            "revenue_cagr_3y": _f(ratio.revenue_cagr_3y),
            "revenue_cagr_5y": _f(ratio.revenue_cagr_5y),
            "pat_growth_1y": _f(ratio.pat_growth_1y),
            "pat_cagr_3y": _f(ratio.pat_cagr_3y),
            "pat_cagr_5y": _f(ratio.pat_cagr_5y),
            "eps_cagr_3y": _f(ratio.eps_cagr_3y),
            "eps_cagr_5y": _f(ratio.eps_cagr_5y),
            "margin_expansion_3y": _f(ratio.margin_expansion_3y),
        },
        "leverage": {
            "debt_equity": _f(ratio.debt_equity), "debt_ebitda": _f(ratio.debt_ebitda),
            "interest_coverage": _f(ratio.interest_coverage),
            "net_debt_equity": _f(ratio.net_debt_equity),
        },
        "efficiency": {
            "current_ratio": _f(ratio.current_ratio), "quick_ratio": _f(ratio.quick_ratio),
            "cfo_pat": _f(ratio.cfo_pat), "fcf_margin": _f(ratio.fcf_margin),
            "asset_turnover": _f(ratio.asset_turnover),
            "receivable_days": _f(ratio.receivable_days),
            "inventory_days": _f(ratio.inventory_days),
            "payable_days": _f(ratio.payable_days),
        },
        "dividend": {
            "dividend_yield": _f(ratio.dividend_yield),
            "dividend_payout": _f(ratio.dividend_payout),
        },
    }

    
