"""
Per-stock research workspace.

Everything the stock detail page needs: the price chart over any range, the full
valuation with its workings exposed, the trade plan, ten years of fundamentals,
peer comparison, and the user's own notes.

The principle running through this router is that **a number is always served
with its provenance**. A valuation carries the assumptions that produced it, the
depth of history behind it and the warnings against it; a chart says how many
sessions it actually has. Nothing is rounded up into looking more certain than
it is.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from dependencies.db import get_db

router = APIRouter()
log = logging.getLogger("research")


# ─────────────────────────────────────────────────────────────
# Chart ranges
# ─────────────────────────────────────────────────────────────
# Trading days, not calendar days, because that is what the candle table holds.
_RANGES: dict[str, int | None] = {
    "1M": 22,
    "3M": 66,
    "6M": 132,
    "YTD": None,      # computed from the calendar
    "1Y": 252,
    "3Y": 756,
    "5Y": 1260,
    "10Y": 2520,
    "MAX": None,
}

# Above this many points a daily series is resampled, because a browser cannot
# usefully draw 2,500 candles across 900 pixels and shipping them wastes the
# user's bandwidth to render sub-pixel detail.
_MAX_POINTS = 420


def _num(value: Any) -> float | None:
    return None if value is None else float(value)


def _jsonb(value: Any, fallback):
    if value is None:
        return fallback
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


async def _require_stock(db: AsyncSession, symbol: str) -> dict:
    row = (
        await db.execute(
            text(
                """SELECT s.nse_symbol, s.company_name, s.exchange, s.currency,
                          s.country, s.industry, s.business_summary, s.website,
                          s.employees, s.instrument_type, s.is_active,
                          sc.sector AS sector_name, sc.macro_sector
                     FROM stocks s
                LEFT JOIN sector_classification sc ON sc.id = s.sector_id
                    WHERE s.nse_symbol = :symbol"""
            ),
            {"symbol": symbol},
        )
    ).mappings().first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown symbol: {symbol}"
        )
    return dict(row)


# ─────────────────────────────────────────────────────────────
# Chart
# ─────────────────────────────────────────────────────────────
@router.get("/{symbol}/chart")
async def get_chart(
    symbol: str,
    range: str = Query("1Y", description="1M 3M 6M YTD 1Y 3Y 5Y 10Y MAX"),
    adjusted: bool = Query(
        True,
        description="Use split/dividend-adjusted closes. Off shows raw traded prices.",
    ),
    db: AsyncSession = Depends(get_db),
):
    """
    Daily OHLCV for charting, resampled when the range is long.

    Two distinct price series exist and the caller chooses. Raw close is what the
    stock actually traded at, which is what a price axis should show. Adjusted
    close is corrected for splits and dividends, which is the only series that
    gives a correct return across a corporate action. Serving one as the other
    puts a fabricated 50% crash on the chart of any stock that has split.
    """
    symbol = symbol.upper().strip()
    range_key = range.upper().strip()
    if range_key not in _RANGES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown range '{range}'. Use one of: {', '.join(_RANGES)}",
        )
    await _require_stock(db, symbol)

    # Fixed-length ranges take the last N stored sessions rather than filtering
    # on a calendar window. Two reasons, both of which bite in production:
    #
    #   * A stock whose data stopped updating six months ago would return an
    #     empty 1M chart under a calendar filter, even though half a decade of
    #     real history is sitting in the table. An empty chart reads as "this
    #     stock does not trade", which is a different and wrong statement.
    #   * A single row with a bad future date — a provider timezone glitch — would
    #     drag every short range into returning years of data.
    #
    # Counting back from the newest stored session is immune to both, and to
    # holidays and trading halts, which a calendar window silently miscounts.
    params: dict[str, Any] = {"symbol": symbol}

    if range_key == "YTD":
        # Year-to-date is inherently calendar-based.
        query = """SELECT date, open, high, low, close, adj_close, volume
                     FROM price_candles_daily
                    WHERE nse_symbol = :symbol AND date >= :start
                    ORDER BY date ASC"""
        params["start"] = date(date.today().year, 1, 1)
    elif _RANGES[range_key]:
        query = """SELECT * FROM (
                     SELECT date, open, high, low, close, adj_close, volume
                       FROM price_candles_daily
                      WHERE nse_symbol = :symbol
                      ORDER BY date DESC
                      LIMIT :sessions
                   ) recent ORDER BY date ASC"""
        params["sessions"] = _RANGES[range_key]
    else:  # MAX
        query = """SELECT date, open, high, low, close, adj_close, volume
                     FROM price_candles_daily
                    WHERE nse_symbol = :symbol
                    ORDER BY date ASC"""

    rows = (await db.execute(text(query), params)).mappings().all()

    # How many sessions exist in total, as distinct from how many this range
    # asked for. The endpoint previously returned only the latter under the name
    # "sessions_available", which the chart rendered as "N sessions stored" — so
    # a stock with 2,600 stored sessions reported 2,520 on a 10Y view. Small,
    # but this product's whole claim is that its numbers mean what they say.
    total_stored = (
        await db.execute(
            text(
                "SELECT count(*) AS n FROM price_candles_daily WHERE nse_symbol = :symbol"
            ),
            {"symbol": symbol},
        )
    ).scalar_one()

    if not rows:
        return {
            "symbol": symbol,
            "range": range_key,
            "candles": [],
            "count": 0,
            "resampled": None,
            "message": (
                "No price history stored for this symbol. "
                "Run the ingestion pipeline to populate it."
            ),
        }

    series: list[dict] = []
    for row in rows:
        raw_close = _num(row["close"])
        adj = _num(row["adj_close"])
        close = (adj if adjusted and adj is not None else raw_close)
        if close is None:
            continue
        # When showing adjusted prices, scale OHL by the same factor the close
        # was adjusted by, so the candle body stays consistent with its close.
        factor = 1.0
        if adjusted and adj is not None and raw_close:
            factor = adj / raw_close
        series.append(
            {
                "date": row["date"].isoformat(),
                "open": _scale(_num(row["open"]), factor),
                "high": _scale(_num(row["high"]), factor),
                "low": _scale(_num(row["low"]), factor),
                "close": round(close, 4),
                "volume": int(row["volume"]) if row["volume"] is not None else None,
            }
        )

    resampled = None
    if len(series) > _MAX_POINTS:
        bucket = "monthly" if len(series) > 1200 else "weekly"
        series = _resample(series, bucket)
        resampled = bucket

    return {
        "symbol": symbol,
        "range": range_key,
        "adjusted": adjusted,
        "candles": series,
        "count": len(series),
        "sessions_in_range": len(rows),
        "sessions_stored": total_stored,
        # Kept so an existing client does not break; prefer the two above.
        "sessions_available": len(rows),
        "resampled": resampled,
        "first_date": rows[0]["date"].isoformat(),
        "last_date": rows[-1]["date"].isoformat(),
    }


def _scale(value: float | None, factor: float) -> float | None:
    return None if value is None else round(value * factor, 4)


def _resample(series: list[dict], bucket: str) -> list[dict]:
    """
    Aggregate daily bars into weekly or monthly ones.

    Open comes from the first session in the bucket and close from the last;
    taking either from the wrong end silently inverts candles.
    """
    grouped: dict[str, list[dict]] = {}
    for point in series:
        day = date.fromisoformat(point["date"])
        if bucket == "monthly":
            key = f"{day.year}-{day.month:02d}"
        else:
            iso = day.isocalendar()
            key = f"{iso[0]}-W{iso[1]:02d}"
        grouped.setdefault(key, []).append(point)

    out: list[dict] = []
    for key in sorted(grouped):
        points = grouped[key]
        highs = [p["high"] for p in points if p["high"] is not None]
        lows = [p["low"] for p in points if p["low"] is not None]
        volumes = [p["volume"] for p in points if p["volume"] is not None]
        out.append(
            {
                "date": points[-1]["date"],
                "open": points[0]["open"],
                "high": max(highs) if highs else None,
                "low": min(lows) if lows else None,
                "close": points[-1]["close"],
                "volume": sum(volumes) if volumes else None,
            }
        )
    return out


# ─────────────────────────────────────────────────────────────
# Overview — one call for the whole detail page
# ─────────────────────────────────────────────────────────────
@router.get("/{symbol}/overview")
async def get_overview(symbol: str, db: AsyncSession = Depends(get_db)):
    """
    Everything the stock page shows above the fold, in one round trip.

    Each block reports its own availability rather than being omitted, so the UI
    can render "not computed yet" in place of a value instead of silently
    showing an empty card the user cannot interpret.
    """
    symbol = symbol.upper().strip()
    stock = await _require_stock(db, symbol)

    quote = (
        await db.execute(
            text(
                """SELECT ltp, open, high, low, close, change_abs, change_pct, volume,
                          week_52_high, week_52_low, market_cap, data_source,
                          is_stale, last_updated
                     FROM realtime_quotes WHERE nse_symbol = :symbol"""
            ),
            {"symbol": symbol},
        )
    ).mappings().first()

    valuation = (
        await db.execute(
            text("SELECT * FROM intrinsic_values WHERE nse_symbol = :symbol"),
            {"symbol": symbol},
        )
    ).mappings().first()

    signal = (
        await db.execute(
            text("SELECT * FROM signals WHERE nse_symbol = :symbol"),
            {"symbol": symbol},
        )
    ).mappings().first()

    technical = (
        await db.execute(
            text("SELECT * FROM technical_snapshots WHERE nse_symbol = :symbol"),
            {"symbol": symbol},
        )
    ).mappings().first()

    coverage = (
        await db.execute(
            text(
                """SELECT
                     (SELECT count(*) FROM price_candles_daily
                       WHERE nse_symbol = :symbol) AS sessions,
                     (SELECT min(date) FROM price_candles_daily
                       WHERE nse_symbol = :symbol) AS first_session,
                     (SELECT count(*) FROM financial_results
                       WHERE nse_symbol = :symbol AND period_type = 'A') AS annual_filings,
                     (SELECT count(*) FROM research_notes
                       WHERE nse_symbol = :symbol) AS notes"""
            ),
            {"symbol": symbol},
        )
    ).mappings().first()

    return {
        "stock": {
            "symbol": stock["nse_symbol"],
            "name": stock["company_name"],
            "sector": stock["sector_name"],
            "macro_sector": stock["macro_sector"],
            "industry": stock["industry"],
            "exchange": stock["exchange"],
            "currency": stock["currency"],
            "country": stock["country"],
            "website": stock["website"],
            "employees": stock["employees"],
            "summary": stock["business_summary"],
        },
        "quote": _quote_block(quote),
        "valuation": _valuation_block(valuation),
        "signal": _signal_block(signal),
        "technicals": _technical_block(technical),
        "coverage": {
            "price_sessions": coverage["sessions"],
            "first_session": (
                coverage["first_session"].isoformat()
                if coverage["first_session"]
                else None
            ),
            "annual_filings": coverage["annual_filings"],
            "research_notes": coverage["notes"],
            "has_full_decade": (coverage["annual_filings"] or 0) >= 10,
        },
    }


def _quote_block(row) -> dict:
    if row is None:
        return {"available": False, "reason": "No quote stored for this symbol."}

    age_minutes = None
    if row["last_updated"]:
        delta = datetime.now(timezone.utc) - row["last_updated"]
        age_minutes = round(delta.total_seconds() / 60, 1)

    return {
        "available": True,
        "price": _num(row["ltp"]),
        "open": _num(row["open"]),
        "high": _num(row["high"]),
        "low": _num(row["low"]),
        "previous_close": _num(row["close"]),
        "change_abs": _num(row["change_abs"]),
        "change_pct": _num(row["change_pct"]),
        "volume": row["volume"],
        "week_52_high": _num(row["week_52_high"]),
        "week_52_low": _num(row["week_52_low"]),
        "market_cap": _num(row["market_cap"]),
        "source": row["data_source"],
        # Surfaced rather than hidden: a price the user cannot tell is two days
        # old is more dangerous than a visibly missing one.
        "is_stale": bool(row["is_stale"]),
        "age_minutes": age_minutes,
        "last_updated": row["last_updated"].isoformat() if row["last_updated"] else None,
    }


def _valuation_block(row) -> dict:
    if row is None:
        return {"available": False, "reason": "No valuation computed yet."}

    warnings = _jsonb(row["warnings"], [])
    if row["iv_blended"] is None:
        return {
            "available": False,
            "reason": warnings[0] if warnings else "Valuation could not be computed.",
            "warnings": warnings,
            "years_of_history": row["years_of_history"],
        }

    return {
        "available": True,
        "intrinsic_value": _num(row["iv_blended"]),
        "bear": _num(row["iv_bear"]),
        "base": _num(row["iv_base"]),
        "bull": _num(row["iv_bull"]),
        "price_at_valuation": _num(row["cmp"]),
        "upside_pct": _num(row["upside_pct"]),
        "margin_of_safety": _num(row["margin_of_safety"]),
        "primary_model": row["primary_model"],
        "confidence": row["valuation_confidence"],
        "confidence_score": _num(row["confidence_score"]),
        "years_of_history": row["years_of_history"],
        "terminal_value_share": _num(row["terminal_value_share"]),
        "cost_of_capital": {
            "wacc": _num(row["wacc"]),
            "cost_of_equity": _num(row["cost_of_equity"]),
            "cost_of_debt": _num(row["cost_of_debt"]),
            "beta": _num(row["beta"]),
            "beta_source": row["beta_source"],
        },
        "models": _jsonb(row["models"], []),
        "warnings": warnings,
        "computed_at": row["computed_at"].isoformat() if row["computed_at"] else None,
    }


def _signal_block(row) -> dict:
    if row is None or row["action"] is None:
        return {"available": False, "reason": "No signal computed yet."}
    return {
        "available": True,
        "action": row["action"],
        "label": row["signal_label"],
        "color": row["signal_color"],
        "conviction": _num(row["conviction"]),
        "headline": row["headline"],
        "rationale": _jsonb(row["rationale"], []),
        "invalidation": _jsonb(row["invalidation"], []),
        "risk_flags": _jsonb(row["blocking_flags"], []),
        "scores": {
            "value": _num(row["value_score"]),
            "quality": _num(row["quality_score"]),
            "momentum": _num(row["momentum_score"]),
            "risk": _num(row["risk_score"]),
            "composite": _num(row["composite_score"]),
        },
        "plan": {
            "entry_low": _num(row["entry_low"]),
            "entry_high": _num(row["entry_high"]),
            "max_buy_price": _num(row["max_buy_price"]),
            "stop_loss": _num(row["stop_loss"]),
            "target_1": _num(row["target_1"]),
            "target_2": _num(row["target_2"]),
            "risk_reward": _num(row["risk_reward"]),
            "position_size_pct": _num(row["position_size_pct"]),
            "horizon": row["horizon"],
        },
        "trend": row["trend"],
        "computed_at": row["computed_at"].isoformat() if row["computed_at"] else None,
    }


def _technical_block(row) -> dict:
    if row is None:
        return {"available": False, "reason": "No technical snapshot computed yet."}
    fields = (
        "sma_20", "sma_50", "sma_200", "ema_12", "ema_26", "rsi_14",
        "macd_line", "macd_signal", "macd_histogram", "atr_14", "atr_pct",
        "bollinger_percent_b", "week_52_high", "week_52_low",
        "pct_from_52w_high", "pct_from_52w_low", "return_1m", "return_3m",
        "return_6m", "return_1y", "return_3y_cagr", "return_5y_cagr",
        "volatility_1y", "max_drawdown_5y", "avg_volume_20d", "volume_ratio",
        "beta",
    )
    block = {"available": True, "trend": row["trend"], "candles_used": row["candles_used"]}
    for field in fields:
        block[field] = _num(row[field])
    block["warnings"] = _jsonb(row["warnings"], [])
    return block


# ─────────────────────────────────────────────────────────────
# Valuation detail — the full audit trail
# ─────────────────────────────────────────────────────────────
@router.get("/{symbol}/valuation")
async def get_valuation_detail(symbol: str, db: AsyncSession = Depends(get_db)):
    """
    The complete workings behind the intrinsic value.

    Every assumption, the ten-year projection year by year, each model's
    independent answer, the measured history the assumptions came from, and the
    reverse DCF. A user should be able to disagree with a specific number rather
    than with an opaque verdict.
    """
    symbol = symbol.upper().strip()
    await _require_stock(db, symbol)

    row = (
        await db.execute(
            text("SELECT * FROM intrinsic_values WHERE nse_symbol = :symbol"),
            {"symbol": symbol},
        )
    ).mappings().first()

    if row is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "No valuation stored for this symbol. "
                "Run the pipeline to compute one."
            ),
        )

    return {
        "symbol": symbol,
        "summary": _valuation_block(row),
        "assumptions": _jsonb(row["assumptions"], {}),
        "projection": _jsonb(row["projection"], []),
        "models": _jsonb(row["models"], []),
        "history_profile": _jsonb(row["history_profile"], {}),
        "reverse_dcf": _jsonb(row["reverse_dcf"], {}),
        "warnings": _jsonb(row["warnings"], []),
    }


# ─────────────────────────────────────────────────────────────
# Fundamentals — ten years, as reported
# ─────────────────────────────────────────────────────────────
@router.get("/{symbol}/fundamentals")
async def get_fundamentals(
    symbol: str,
    period_type: str = Query("A", regex="^(A|Q)$"),
    db: AsyncSession = Depends(get_db),
):
    """
    Reported filings, oldest first, with derived margins and growth.

    Derived figures are computed here rather than stored, so they can never
    drift out of step with the filings they come from. A year that did not
    report a line shows ``null`` for it, not zero.
    """
    symbol = symbol.upper().strip()
    await _require_stock(db, symbol)

    rows = (
        await db.execute(
            text(
                """SELECT period_end, revenue, ebitda, ebit, pbt, tax, pat,
                          eps, eps_diluted, depreciation, interest_expense,
                          total_assets, total_liabilities, net_worth, total_debt,
                          cash_and_equiv, investments, invested_capital,
                          working_capital, cfo, capex, free_cash_flow,
                          dividends_paid, shares_outstanding, book_value_per_share,
                          currency, data_source
                     FROM financial_results
                    WHERE nse_symbol = :symbol AND period_type = :period_type
                    ORDER BY period_end ASC"""
            ),
            {"symbol": symbol, "period_type": period_type},
        )
    ).mappings().all()

    periods: list[dict] = []
    previous_revenue: float | None = None
    for row in rows:
        revenue = _num(row["revenue"])
        ebit = _num(row["ebit"])
        ebitda = _num(row["ebitda"])
        pat = _num(row["pat"])

        periods.append(
            {
                "period_end": row["period_end"].isoformat(),
                "currency": row["currency"],
                "source": row["data_source"],
                "revenue": revenue,
                "ebitda": ebitda,
                "ebit": ebit,
                "pbt": _num(row["pbt"]),
                "tax": _num(row["tax"]),
                "pat": pat,
                "eps": _num(row["eps_diluted"]) or _num(row["eps"]),
                "depreciation": _num(row["depreciation"]),
                "interest_expense": _num(row["interest_expense"]),
                "total_assets": _num(row["total_assets"]),
                "net_worth": _num(row["net_worth"]),
                "total_debt": _num(row["total_debt"]),
                "cash": _num(row["cash_and_equiv"]),
                "invested_capital": _num(row["invested_capital"]),
                "cfo": _num(row["cfo"]),
                "capex": _num(row["capex"]),
                "free_cash_flow": _num(row["free_cash_flow"]),
                "shares_outstanding": _num(row["shares_outstanding"]),
                "book_value_per_share": _num(row["book_value_per_share"]),
                # Derived, never stored.
                "ebitda_margin": _ratio(ebitda, revenue),
                "operating_margin": _ratio(ebit, revenue),
                "net_margin": _ratio(pat, revenue),
                "revenue_growth": _growth(revenue, previous_revenue),
            }
        )
        if revenue:
            previous_revenue = revenue

    return {
        "symbol": symbol,
        "period_type": period_type,
        "count": len(periods),
        "years_available": len(periods) if period_type == "A" else None,
        "periods": periods,
        "message": (
            None
            if periods
            else "No filings stored. Run the fundamentals stage of the pipeline."
        ),
    }


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or not denominator:
        return None
    return round(numerator / denominator, 6)


def _growth(current: float | None, previous: float | None) -> float | None:
    # A move from a loss to a profit has no meaningful growth rate.
    if current is None or previous is None or previous <= 0:
        return None
    return round(current / previous - 1.0, 6)


# ─────────────────────────────────────────────────────────────
# Peers
# ─────────────────────────────────────────────────────────────
@router.get("/{symbol}/peers")
async def get_peers(
    symbol: str,
    limit: int = Query(8, ge=1, le=25),
    db: AsyncSession = Depends(get_db),
):
    """
    Sector peers with their own valuation and signal, for side-by-side reading.

    Ranked by market capitalisation, because the nearest comparable to a company
    is usually one of similar size in the same sector rather than the sector's
    largest name.
    """
    symbol = symbol.upper().strip()
    stock = await _require_stock(db, symbol)

    rows = (
        await db.execute(
            text(
                """SELECT s.nse_symbol, s.company_name, q.ltp, q.market_cap,
                          q.change_pct, iv.iv_blended, iv.upside_pct,
                          iv.valuation_confidence, iv.years_of_history,
                          sg.action, sg.composite_score, sg.risk_score
                     FROM stocks s
                     JOIN sector_classification sc ON sc.id = s.sector_id
                LEFT JOIN realtime_quotes q  ON q.nse_symbol  = s.nse_symbol
                LEFT JOIN intrinsic_values iv ON iv.nse_symbol = s.nse_symbol
                LEFT JOIN signals sg          ON sg.nse_symbol = s.nse_symbol
                    WHERE sc.sector = :sector
                      AND s.nse_symbol <> :symbol
                      AND s.is_active
                      AND COALESCE(s.instrument_type,'EQ') NOT IN ('INDEX','FO')
                    ORDER BY q.market_cap DESC NULLS LAST
                    LIMIT :limit"""
            ),
            {"sector": stock["sector_name"], "symbol": symbol, "limit": limit},
        )
    ).mappings().all()

    return {
        "symbol": symbol,
        "sector": stock["sector_name"],
        "count": len(rows),
        "peers": [
            {
                "symbol": r["nse_symbol"],
                "name": r["company_name"],
                "price": _num(r["ltp"]),
                "change_pct": _num(r["change_pct"]),
                "market_cap": _num(r["market_cap"]),
                "intrinsic_value": _num(r["iv_blended"]),
                "upside_pct": _num(r["upside_pct"]),
                "confidence": r["valuation_confidence"],
                "years_of_history": r["years_of_history"],
                "action": r["action"],
                "composite_score": _num(r["composite_score"]),
                "risk_score": _num(r["risk_score"]),
            }
            for r in rows
        ],
        "message": (
            None
            if rows
            else f"No other active stocks classified under '{stock['sector_name']}'."
        ),
    }


# ─────────────────────────────────────────────────────────────
# Research notes
# ─────────────────────────────────────────────────────────────
class NoteIn(BaseModel):
    body: str = Field(min_length=1, max_length=20_000)
    title: str | None = Field(default=None, max_length=200)
    tags: list[str] = Field(default_factory=list, max_length=20)
    thesis_stance: str | None = Field(
        default=None, description="BULLISH, BEARISH or NEUTRAL"
    )


class NoteUpdate(BaseModel):
    body: str | None = Field(default=None, min_length=1, max_length=20_000)
    title: str | None = Field(default=None, max_length=200)
    tags: list[str] | None = Field(default=None, max_length=20)
    thesis_stance: str | None = None


_STANCES = {"BULLISH", "BEARISH", "NEUTRAL"}


@router.get("/{symbol}/notes")
async def list_notes(
    symbol: str,
    user_id: str = Query("default"),
    db: AsyncSession = Depends(get_db),
):
    """A user's research notes on one stock, newest first."""
    symbol = symbol.upper().strip()
    await _require_stock(db, symbol)

    rows = (
        await db.execute(
            text(
                """SELECT id, title, body, tags, thesis_stance, price_at_note,
                          iv_at_note, created_at, updated_at
                     FROM research_notes
                    WHERE nse_symbol = :symbol AND user_id = :user_id
                    ORDER BY created_at DESC"""
            ),
            {"symbol": symbol, "user_id": user_id},
        )
    ).mappings().all()

    return {
        "symbol": symbol,
        "count": len(rows),
        "notes": [
            {
                "id": str(r["id"]),
                "title": r["title"],
                "body": r["body"],
                "tags": _jsonb(r["tags"], []),
                "thesis_stance": r["thesis_stance"],
                # Captured when the note was written, so a thesis can be reviewed
                # against what was actually known at the time.
                "price_at_note": _num(r["price_at_note"]),
                "iv_at_note": _num(r["iv_at_note"]),
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
            }
            for r in rows
        ],
    }


@router.post("/{symbol}/notes", status_code=201)
async def create_note(
    symbol: str,
    note: NoteIn,
    user_id: str = Query("default"),
    db: AsyncSession = Depends(get_db),
):
    """
    Record a research note.

    The current price and intrinsic value are stamped onto the note at the
    moment it is written. That is the whole point: a thesis reviewed a year
    later should be read against what was known when it was formed, not against
    today's numbers, which is how hindsight quietly rewrites conviction.
    """
    symbol = symbol.upper().strip()
    await _require_stock(db, symbol)

    stance = (note.thesis_stance or "").upper().strip() or None
    if stance and stance not in _STANCES:
        raise HTTPException(
            status_code=400,
            detail=f"thesis_stance must be one of {', '.join(sorted(_STANCES))}",
        )

    snapshot = (
        await db.execute(
            text(
                """SELECT q.ltp, iv.iv_blended
                     FROM stocks s
                LEFT JOIN realtime_quotes q   ON q.nse_symbol  = s.nse_symbol
                LEFT JOIN intrinsic_values iv ON iv.nse_symbol = s.nse_symbol
                    WHERE s.nse_symbol = :symbol"""
            ),
            {"symbol": symbol},
        )
    ).mappings().first()

    row = (
        await db.execute(
            text(
                """INSERT INTO research_notes
                       (nse_symbol, user_id, title, body, tags, thesis_stance,
                        price_at_note, iv_at_note)
                   VALUES (:symbol, :user_id, :title, :body, CAST(:tags AS jsonb),
                           :stance, :price, :iv)
                   RETURNING id, created_at"""
            ),
            {
                "symbol": symbol,
                "user_id": user_id,
                "title": note.title,
                "body": note.body,
                "tags": json.dumps(note.tags),
                "stance": stance,
                "price": _num(snapshot["ltp"]) if snapshot else None,
                "iv": _num(snapshot["iv_blended"]) if snapshot else None,
            },
        )
    ).mappings().first()
    await db.commit()

    return {
        "id": str(row["id"]),
        "symbol": symbol,
        "created_at": row["created_at"].isoformat(),
        "price_at_note": _num(snapshot["ltp"]) if snapshot else None,
        "iv_at_note": _num(snapshot["iv_blended"]) if snapshot else None,
    }


@router.patch("/{symbol}/notes/{note_id}")
async def update_note(
    symbol: str,
    note_id: str,
    patch: NoteUpdate,
    user_id: str = Query("default"),
    db: AsyncSession = Depends(get_db),
):
    """
    Edit a note's text.

    ``price_at_note`` and ``iv_at_note`` are deliberately **not** updatable: they
    record the conditions the thesis was formed under, and letting an edit move
    them would erase exactly the information that makes the note worth keeping.
    """
    symbol = symbol.upper().strip()

    updates: dict[str, Any] = {}
    if patch.body is not None:
        updates["body"] = patch.body
    if patch.title is not None:
        updates["title"] = patch.title
    if patch.tags is not None:
        updates["tags"] = json.dumps(patch.tags)
    if patch.thesis_stance is not None:
        stance = patch.thesis_stance.upper().strip()
        if stance and stance not in _STANCES:
            raise HTTPException(
                status_code=400,
                detail=f"thesis_stance must be one of {', '.join(sorted(_STANCES))}",
            )
        updates["thesis_stance"] = stance or None

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update.")

    assignments = ", ".join(
        f"{k} = CAST(:{k} AS jsonb)" if k == "tags" else f"{k} = :{k}"
        for k in updates
    )
    result = await db.execute(
        text(
            f"""UPDATE research_notes
                   SET {assignments}, updated_at = NOW()
                 WHERE id = CAST(:note_id AS uuid)
                   AND nse_symbol = :symbol
                   AND user_id = :user_id
              RETURNING id"""
        ),
        {**updates, "note_id": note_id, "symbol": symbol, "user_id": user_id},
    )
    row = result.mappings().first()
    await db.commit()

    if row is None:
        raise HTTPException(status_code=404, detail="Note not found.")
    return {"id": str(row["id"]), "updated": True}


@router.delete("/{symbol}/notes/{note_id}", status_code=204)
async def delete_note(
    symbol: str,
    note_id: str,
    user_id: str = Query("default"),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        text(
            """DELETE FROM research_notes
                WHERE id = CAST(:note_id AS uuid)
                  AND nse_symbol = :symbol
                  AND user_id = :user_id
            RETURNING id"""
        ),
        {"note_id": note_id, "symbol": symbol.upper().strip(), "user_id": user_id},
    )
    row = result.mappings().first()
    await db.commit()
    if row is None:
        raise HTTPException(status_code=404, detail="Note not found.")
    return None


# ─────────────────────────────────────────────────────────────
# Search
# ─────────────────────────────────────────────────────────────
@router.get("/search")
async def search_stocks(
    q: str = Query(min_length=1, max_length=60),
    limit: int = Query(12, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
):
    """
    Find a stock by symbol or company name.

    Exact symbol matches rank first, then prefix matches, then name matches, so
    typing "TCS" returns TCS rather than every company with those letters
    somewhere in its name.
    """
    needle = q.strip().upper()
    rows = (
        await db.execute(
            text(
                """SELECT s.nse_symbol, s.company_name, s.exchange, s.currency,
                          sc.sector AS sector_name, q.ltp, q.change_pct,
                          sg.action, iv.upside_pct
                     FROM stocks s
                LEFT JOIN sector_classification sc ON sc.id = s.sector_id
                LEFT JOIN realtime_quotes q   ON q.nse_symbol  = s.nse_symbol
                LEFT JOIN signals sg          ON sg.nse_symbol = s.nse_symbol
                LEFT JOIN intrinsic_values iv ON iv.nse_symbol = s.nse_symbol
                    WHERE s.is_active
                      AND COALESCE(s.instrument_type,'EQ') NOT IN ('INDEX','FO')
                      AND (s.nse_symbol LIKE :prefix
                           OR UPPER(s.company_name) LIKE :contains)
                    ORDER BY
                      CASE WHEN s.nse_symbol = :exact THEN 0
                           WHEN s.nse_symbol LIKE :prefix THEN 1
                           ELSE 2 END,
                      s.nse_symbol
                    LIMIT :limit"""
            ),
            {
                "exact": needle,
                "prefix": f"{needle}%",
                "contains": f"%{needle}%",
                "limit": limit,
            },
        )
    ).mappings().all()

    return {
        "query": q,
        "count": len(rows),
        "results": [
            {
                "symbol": r["nse_symbol"],
                "name": r["company_name"],
                "sector": r["sector_name"],
                "exchange": r["exchange"],
                "currency": r["currency"],
                "price": _num(r["ltp"]),
                "change_pct": _num(r["change_pct"]),
                "action": r["action"],
                "upside_pct": _num(r["upside_pct"]),
            }
            for r in rows
        ],
    }
