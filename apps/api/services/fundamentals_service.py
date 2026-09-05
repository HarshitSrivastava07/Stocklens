"""
Fundamentals Service — annual financials ingestion.

Fills `financial_results` with period_type='A' rows, which is the input the
ratio engine, valuation engine and signal engine all gate on:

    fundamentals -> ratio_engine -> valuation_engine -> signal_engine

Source is Yahoo's fundamentals-timeseries endpoint (the same data yfinance
wraps, but called directly — the pinned yfinance 0.2.50 returns empty frames
against Yahoo's current API). No crumb/cookie is required for this endpoint.

Run standalone from apps/api:
    python -m services.fundamentals_service
"""
import asyncio
import logging
import time
from datetime import date, datetime
from typing import Optional

import httpx
import structlog
from sqlalchemy import select

from dependencies.db import AsyncSessionLocal
from models.db_models import Stock, FinancialResult, AuditLog

log = structlog.get_logger("fundamentals")

TIMESERIES_URL = "https://query2.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/{sym}"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

# Yahoo timeseries key -> financial_results column
FIELD_MAP = {
    "annualTotalRevenue": "revenue",
    "annualGrossProfit": "gross_profit",
    "annualEBITDA": "ebitda",
    "annualOperatingIncome": "ebit",
    "annualPretaxIncome": "pbt",
    "annualTaxProvision": "tax",
    "annualNetIncome": "pat",
    "annualBasicEPS": "eps",
    "annualDilutedEPS": "eps_diluted",
    "annualTotalAssets": "total_assets",
    "annualTotalLiabilitiesNetMinorityInterest": "total_liabilities",
    "annualStockholdersEquity": "net_worth",
    "annualTotalDebt": "total_debt",
    "annualLongTermDebt": "long_term_debt",
    "annualCashAndCashEquivalents": "cash_and_equiv",
    "annualOrdinarySharesNumber": "shares_outstanding",
    "annualOperatingCashFlow": "cfo",
    "annualCapitalExpenditure": "capex",
    "annualFreeCashFlow": "free_cash_flow",
}

YEARS_BACK = 6
CONCURRENCY = 6
REQUEST_TIMEOUT = 20


async def fetch_annual_fundamentals(
    client: httpx.AsyncClient, yahoo_ticker: str
) -> dict[date, dict]:
    """Return {period_end: {column: value}} of annual figures for one ticker."""
    now = int(time.time())
    params = {
        "symbol": yahoo_ticker,
        "type": ",".join(FIELD_MAP.keys()),
        "period1": now - YEARS_BACK * 365 * 86400,
        "period2": now,
    }
    resp = await client.get(
        TIMESERIES_URL.format(sym=yahoo_ticker), params=params, timeout=REQUEST_TIMEOUT
    )
    if resp.status_code == 429:
        await asyncio.sleep(5)
        resp = await client.get(
            TIMESERIES_URL.format(sym=yahoo_ticker), params=params, timeout=REQUEST_TIMEOUT
        )
    if resp.status_code != 200:
        return {}

    periods: dict[date, dict] = {}
    for series in resp.json().get("timeseries", {}).get("result", []):
        keys = [k for k in series if k not in ("meta", "timestamp")]
        if not keys:
            continue
        yahoo_key = keys[0]
        column = FIELD_MAP.get(yahoo_key)
        if not column:
            continue
        for point in series[yahoo_key]:
            if not isinstance(point, dict):
                continue
            raw = (point.get("reportedValue") or {}).get("raw")
            as_of = point.get("asOfDate")
            if raw is None or not as_of:
                continue
            try:
                period_end = datetime.strptime(as_of, "%Y-%m-%d").date()
            except ValueError:
                continue
            periods.setdefault(period_end, {})[column] = raw
    return periods


def _derive(values: dict) -> dict:
    """Fill fields Yahoo doesn't report directly but the engines expect."""
    shares = values.get("shares_outstanding")
    net_worth = values.get("net_worth")
    if values.get("book_value_per_share") is None and shares and net_worth:
        values["book_value_per_share"] = float(net_worth) / float(shares)
    if values.get("free_cash_flow") is None:
        cfo, capex = values.get("cfo"), values.get("capex")
        if cfo is not None and capex is not None:
            # Yahoo reports capex as a negative number
            values["free_cash_flow"] = float(cfo) + float(capex)
    if shares is not None:
        values["shares_outstanding"] = int(shares)
    return values


async def run_fundamentals_refresh(limit: Optional[int] = None) -> dict:
    """Fetch annual financials for every active stock that has a yahoo_ticker."""
    async with AsyncSessionLocal() as db:
        stmt = select(Stock).where(
            Stock.is_active == True,  # noqa: E712
            Stock.yahoo_ticker.isnot(None),
        )
        if limit:
            stmt = stmt.limit(limit)
        stocks = (await db.execute(stmt)).scalars().all()
        log.info("fundamentals refresh starting", stocks=len(stocks))

        sem = asyncio.Semaphore(CONCURRENCY)
        inserted = updated = no_data = failed = 0

        async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True) as client:

            async def _guarded(stock: Stock):
                async with sem:
                    try:
                        return stock, await fetch_annual_fundamentals(
                            client, stock.yahoo_ticker
                        )
                    except Exception as e:  # network/parse — keep going
                        log.warning(
                            "fundamentals fetch failed",
                            symbol=stock.nse_symbol,
                            error=str(e),
                        )
                        return stock, None

            results = await asyncio.gather(*[_guarded(s) for s in stocks])

        for stock, periods in results:
            if periods is None:
                failed += 1
                continue
            if not periods:
                no_data += 1
                continue

            for period_end, values in periods.items():
                values = _derive(dict(values))
                existing = (
                    await db.execute(
                        select(FinancialResult).where(
                            FinancialResult.nse_symbol == stock.nse_symbol,
                            FinancialResult.period_type == "A",
                            FinancialResult.period_end == period_end,
                        )
                    )
                ).scalar_one_or_none()

                if existing:
                    for column, value in values.items():
                        setattr(existing, column, value)
                    existing.data_source = "YAHOO_FUNDAMENTALS"
                    updated += 1
                else:
                    db.add(
                        FinancialResult(
                            nse_symbol=stock.nse_symbol,
                            period_type="A",
                            period_end=period_end,
                            data_source="YAHOO_FUNDAMENTALS",
                            is_verified=False,
                            confidence_level="MEDIUM",
                            has_exceptional=False,
                            **values,
                        )
                    )
                    inserted += 1

        await db.commit()

        summary = {
            "stocks": len(stocks),
            "inserted": inserted,
            "updated": updated,
            "no_data": no_data,
            "failed": failed,
        }
        log.info("fundamentals refresh done", **summary)

        db.add(AuditLog(action="FUNDAMENTALS_REFRESH", details=summary))
        await db.commit()
        return summary


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(asyncio.run(run_fundamentals_refresh()))
