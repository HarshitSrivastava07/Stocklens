"""
Yahoo Finance provider.

Covers the four things StockLens needs from a price/fundamentals source:

  1. ``get_quote``         — live (or 15-minute delayed) last-traded price
  2. ``get_daily_history`` — split/dividend-adjusted daily OHLCV, up to 10+ years
  3. ``get_fundamentals``  — annual and quarterly filings as far back as Yahoo serves
  4. ``get_profile`` / ``search`` — company descriptives and symbol lookup

Endpoints used (all public, no API key):

  * ``/v8/finance/chart/{t}``                                — OHLCV
  * ``/ws/fundamentals-timeseries/v1/finance/timeseries/{t}`` — multi-year filings
  * ``/v10/finance/quoteSummary/{t}``                        — profile + key stats
  * ``/v1/finance/search``                                    — symbol lookup

A note on history depth
-----------------------
Yahoo's free fundamentals feed does not guarantee ten years for every listing;
depth varies by company and exchange. This client always *asks* for a twelve-year
window and returns every period the source actually serves. It never pads,
extrapolates or back-fills a missing year. Callers read ``len(periods)`` to learn
the true depth, and the valuation engine scales its own confidence to it. Where a
deeper history is needed than Yahoo carries, ``scripts/import_screener_excel.py``
merges a Screener.in export (10 years for Indian listings) into the same tables.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Sequence

import httpx

from .base import (
    AsyncRateLimiter,
    Candle,
    CompanyProfile,
    FinancialPeriod,
    MalformedResponse,
    NotFound,
    Quote,
    RateLimited,
    UpstreamUnavailable,
    epoch_to_date,
    retry_async,
    to_float,
    to_int,
)

log = logging.getLogger("provider.yahoo")

SOURCE = "YAHOO"

_BASE = "https://query2.finance.yahoo.com"
_FALLBACK_BASE = "https://query1.finance.yahoo.com"
_COOKIE_URL = "https://fc.yahoo.com/"
_CRUMB_URL = "/v1/test/getcrumb"

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# ─────────────────────────────────────────────────────────────
# Fundamentals field map
#
# Left  = Yahoo timeseries type name (without the annual/quarterly prefix)
# Right = attribute on FinancialPeriod
# ─────────────────────────────────────────────────────────────
_FUNDAMENTAL_FIELDS: dict[str, str] = {
    # Income statement
    "TotalRevenue": "revenue",
    "CostOfRevenue": "cost_of_revenue",
    "GrossProfit": "gross_profit",
    "OperatingIncome": "operating_income",
    "EBITDA": "ebitda",
    "ReconciledDepreciation": "depreciation",
    "InterestExpense": "interest_expense",
    "PretaxIncome": "pretax_income",
    "TaxProvision": "tax_provision",
    "NetIncome": "net_income",
    "BasicEPS": "eps_basic",
    "DilutedEPS": "eps_diluted",
    # Balance sheet
    "TotalAssets": "total_assets",
    "TotalLiabilitiesNetMinorityInterest": "total_liabilities",
    "StockholdersEquity": "equity",
    "TotalDebt": "total_debt",
    "LongTermDebt": "long_term_debt",
    "CurrentDebt": "short_term_debt",
    "CashAndCashEquivalents": "cash",
    "OtherShortTermInvestments": "short_term_investments",
    "WorkingCapital": "working_capital",
    "InvestedCapital": "invested_capital",
    "NetPPE": "net_ppe",
    "OrdinarySharesNumber": "shares_outstanding",
    # Cash flow
    "OperatingCashFlow": "cfo",
    "CapitalExpenditure": "capex",
    "FreeCashFlow": "free_cash_flow",
    "CashDividendsPaid": "dividends_paid",
    "RepurchaseOfCapitalStock": "buybacks",
}

# Yahoo caps the `type` query parameter length, so requests are chunked.
_TYPES_PER_REQUEST = 12

# Fields reported as negative outflows that the rest of the system treats as
# positive magnitudes.
_ABS_FIELDS = {"capex", "dividends_paid", "buybacks", "interest_expense"}


class YahooProvider:
    """
    Async Yahoo Finance client.

    Construct one per process and reuse it: it holds the cookie/crumb session
    and the shared rate limiter. Safe for concurrent use.
    """

    def __init__(
        self,
        *,
        rate_per_sec: float = 5.0,
        burst: int = 10,
        timeout: float = 20.0,
        client: httpx.AsyncClient | None = None,
    ):
        self._limiter = AsyncRateLimiter(rate_per_sec, burst)
        self._timeout = timeout
        self._client = client
        self._owns_client = client is None
        self._crumb: str | None = None
        self._crumb_lock = asyncio.Lock()

    # ── lifecycle ────────────────────────────────────────────
    async def __aenter__(self) -> "YahooProvider":
        await self._ensure_client()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self._timeout,
                follow_redirects=True,
                headers={
                    "User-Agent": _UA,
                    "Accept": "application/json,text/plain,*/*",
                    "Accept-Language": "en-US,en;q=0.9",
                },
            )
        return self._client

    # ── authentication ───────────────────────────────────────
    async def _ensure_crumb(self) -> str:
        """
        Yahoo requires a cookie plus a matching crumb on quoteSummary. The chart
        and timeseries endpoints tolerate its absence, so a crumb failure is
        logged and tolerated rather than fatal.
        """
        if self._crumb:
            return self._crumb
        async with self._crumb_lock:
            if self._crumb:
                return self._crumb
            client = await self._ensure_client()
            try:
                # Seeds the A1/A3 consent cookies.
                await client.get(_COOKIE_URL)
            except httpx.HTTPError as exc:
                log.debug("cookie priming failed: %s", exc)
            for base in (_BASE, _FALLBACK_BASE):
                try:
                    resp = await client.get(f"{base}{_CRUMB_URL}")
                    if resp.status_code == 200 and resp.text.strip():
                        text = resp.text.strip()
                        # A crumb is a short opaque token; an HTML body means
                        # we were served a consent page instead.
                        if "<" not in text and len(text) < 40:
                            self._crumb = text
                            log.debug("acquired Yahoo crumb")
                            return self._crumb
                except httpx.HTTPError as exc:
                    log.debug("crumb fetch via %s failed: %s", base, exc)
            raise UpstreamUnavailable("could not obtain a Yahoo session crumb")

    # ── transport ────────────────────────────────────────────
    async def _get_json(
        self, path: str, params: dict[str, Any], *, ticker: str
    ) -> dict:
        """One rate-limited GET returning parsed JSON, with errors classified."""
        await self._limiter.acquire()
        client = await self._ensure_client()
        last_exc: Exception | None = None

        for base in (_BASE, _FALLBACK_BASE):
            url = f"{base}{path}"
            try:
                resp = await client.get(url, params=params)
            except httpx.HTTPError as exc:
                last_exc = UpstreamUnavailable(f"{ticker}: {type(exc).__name__}: {exc}")
                continue

            if resp.status_code == 429:
                retry_after = to_float(resp.headers.get("Retry-After"))
                raise RateLimited(f"{ticker}: rate limited by Yahoo", retry_after)
            if resp.status_code in (401, 403):
                # A stale crumb is the usual cause; drop it so the next call re-auths.
                self._crumb = None
                last_exc = UpstreamUnavailable(
                    f"{ticker}: Yahoo returned {resp.status_code}"
                )
                continue
            if resp.status_code == 404:
                raise NotFound(f"{ticker}: not found at Yahoo")
            if resp.status_code >= 500:
                last_exc = UpstreamUnavailable(f"{ticker}: Yahoo {resp.status_code}")
                continue
            if resp.status_code != 200:
                last_exc = UpstreamUnavailable(
                    f"{ticker}: unexpected status {resp.status_code}"
                )
                continue

            if not resp.text.strip():
                last_exc = MalformedResponse(f"{ticker}: empty body from {url}")
                continue
            try:
                return resp.json()
            except ValueError as exc:
                last_exc = MalformedResponse(f"{ticker}: non-JSON body: {exc}")
                continue

        raise last_exc or UpstreamUnavailable(f"{ticker}: all Yahoo hosts failed")

    # ─────────────────────────────────────────────────────────
    # Quotes
    # ─────────────────────────────────────────────────────────
    async def get_quote(self, ticker: str, symbol: str | None = None) -> Quote:
        """Latest traded price. Sourced from the chart endpoint's meta block."""
        symbol = symbol or ticker

        async def _call():
            return await self._get_json(
                f"/v8/finance/chart/{ticker}",
                {"interval": "1d", "range": "5d", "includePrePost": "false"},
                ticker=ticker,
            )

        payload = await retry_async(_call)
        return self._parse_quote(payload, ticker, symbol)

    @staticmethod
    def _parse_quote(payload: dict, ticker: str, symbol: str) -> Quote:
        chart = (payload or {}).get("chart") or {}
        if chart.get("error"):
            desc = str(chart["error"].get("description", chart["error"]))
            if "No data found" in desc or "Not Found" in desc:
                raise NotFound(f"{ticker}: {desc}")
            raise MalformedResponse(f"{ticker}: {desc}")

        results = chart.get("result") or []
        if not results:
            raise NotFound(f"{ticker}: chart returned no result")

        block = results[0]
        meta = block.get("meta") or {}
        price = to_float(meta.get("regularMarketPrice"))
        if price is None or price <= 0:
            raise MalformedResponse(f"{ticker}: no usable regularMarketPrice")

        # meta often nulls open/high/low outside market hours; fall back to the
        # final populated entry of the indicator arrays for the same session.
        quote_arr = ((block.get("indicators") or {}).get("quote") or [{}])[0]

        def last_of(key: str) -> float | None:
            for value in reversed(quote_arr.get(key) or []):
                out = to_float(value)
                if out is not None:
                    return out
            return None

        prev_close = (
            to_float(meta.get("previousClose"))
            or to_float(meta.get("chartPreviousClose"))
        )

        return Quote(
            symbol=symbol,
            ticker=ticker,
            price=price,
            previous_close=prev_close,
            open=to_float(meta.get("regularMarketOpen")) or last_of("open"),
            day_high=to_float(meta.get("regularMarketDayHigh")) or last_of("high"),
            day_low=to_float(meta.get("regularMarketDayLow")) or last_of("low"),
            volume=to_int(meta.get("regularMarketVolume")) or to_int(last_of("volume")),
            currency=meta.get("currency"),
            exchange=meta.get("exchangeName") or meta.get("fullExchangeName"),
            market_state=meta.get("marketState"),
            week_52_high=to_float(meta.get("fiftyTwoWeekHigh")),
            week_52_low=to_float(meta.get("fiftyTwoWeekLow")),
            market_cap=to_float(meta.get("marketCap")),
            source=SOURCE,
            fetched_at=datetime.now(timezone.utc),
        )

    async def get_quotes(
        self, tickers: Sequence[tuple[str, str]], *, concurrency: int = 8
    ) -> tuple[list[Quote], dict[str, str]]:
        """
        Fetch many quotes concurrently.

        ``tickers`` is a sequence of ``(symbol, yahoo_ticker)``. Returns the
        quotes that succeeded plus a ``{symbol: error}`` map, so one delisted
        name can never abort a market-wide poll.
        """
        sem = asyncio.Semaphore(concurrency)
        errors: dict[str, str] = {}

        async def one(symbol: str, ticker: str) -> Quote | None:
            async with sem:
                try:
                    return await self.get_quote(ticker, symbol)
                except Exception as exc:  # noqa: BLE001 - recorded, not swallowed
                    errors[symbol] = f"{type(exc).__name__}: {exc}"
                    return None

        results = await asyncio.gather(*(one(s, t) for s, t in tickers))
        return [q for q in results if q is not None], errors

    # ─────────────────────────────────────────────────────────
    # Price history
    # ─────────────────────────────────────────────────────────
    async def get_daily_history(
        self,
        ticker: str,
        *,
        years: int = 10,
        start: date | None = None,
        end: date | None = None,
    ) -> list[Candle]:
        """
        Daily OHLCV. Returns split/dividend-adjusted closes in ``adj_close`` and
        raw traded prices in ``close``; valuation uses adjusted, display uses raw.
        Ordered oldest to newest, with null-priced rows (trading halts) dropped.
        """
        end = end or datetime.now(timezone.utc).date()
        start = start or (end - timedelta(days=int(365.25 * years) + 5))

        async def _call():
            return await self._get_json(
                f"/v8/finance/chart/{ticker}",
                {
                    "period1": int(
                        datetime.combine(
                            start, datetime.min.time(), tzinfo=timezone.utc
                        ).timestamp()
                    ),
                    "period2": int(
                        datetime.combine(
                            end, datetime.max.time(), tzinfo=timezone.utc
                        ).timestamp()
                    ),
                    "interval": "1d",
                    "events": "div,split",
                    "includeAdjustedClose": "true",
                },
                ticker=ticker,
            )

        payload = await retry_async(_call)
        return self._parse_candles(payload, ticker)

    @staticmethod
    def _parse_candles(payload: dict, ticker: str) -> list[Candle]:
        chart = (payload or {}).get("chart") or {}
        if chart.get("error"):
            raise NotFound(f"{ticker}: {chart['error']}")
        results = chart.get("result") or []
        if not results:
            raise NotFound(f"{ticker}: history returned no result")

        block = results[0]
        stamps = block.get("timestamp") or []
        indicators = block.get("indicators") or {}
        quote = (indicators.get("quote") or [{}])[0]
        adj = (indicators.get("adjclose") or [{}])[0].get("adjclose") or []

        opens = quote.get("open") or []
        highs = quote.get("high") or []
        lows = quote.get("low") or []
        closes = quote.get("close") or []
        volumes = quote.get("volume") or []

        def at(seq, i):
            return to_float(seq[i]) if i < len(seq) else None

        candles: list[Candle] = []
        for i, stamp in enumerate(stamps):
            day = epoch_to_date(stamp)
            close = at(closes, i)
            # A null close means no trade that session; the row carries no
            # information and must not become a zero-price bar on the chart.
            if day is None or close is None:
                continue
            candles.append(
                Candle(
                    date=day,
                    open=at(opens, i),
                    high=at(highs, i),
                    low=at(lows, i),
                    close=close,
                    adj_close=at(adj, i) if adj else close,
                    volume=to_int(volumes[i]) if i < len(volumes) else None,
                )
            )

        candles.sort(key=lambda c: c.date)
        # Yahoo occasionally repeats the most recent session; keep the last.
        deduped: dict[date, Candle] = {c.date: c for c in candles}
        return [deduped[d] for d in sorted(deduped)]

    # ─────────────────────────────────────────────────────────
    # Fundamentals
    # ─────────────────────────────────────────────────────────
    async def get_fundamentals(
        self, ticker: str, *, period_type: str = "A", years: int = 12
    ) -> list[FinancialPeriod]:
        """
        Annual (``"A"``) or quarterly (``"Q"``) filings, oldest first.

        Asks for a ``years``-wide window and returns exactly what Yahoo serves —
        no padding, no interpolation. Callers must treat ``len(result)`` as the
        real history depth.
        """
        if period_type not in ("A", "Q"):
            raise ValueError("period_type must be 'A' or 'Q'")
        prefix = "annual" if period_type == "A" else "quarterly"

        end = datetime.now(timezone.utc).date()
        start = end - timedelta(days=int(365.25 * years))
        period1 = int(
            datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc).timestamp()
        )
        period2 = int(
            datetime.combine(end, datetime.max.time(), tzinfo=timezone.utc).timestamp()
        )

        names = list(_FUNDAMENTAL_FIELDS)
        chunks = [
            names[i : i + _TYPES_PER_REQUEST]
            for i in range(0, len(names), _TYPES_PER_REQUEST)
        ]

        # asOfDate -> {attribute: value}
        collected: dict[date, dict[str, float]] = {}
        currency: str | None = None
        any_success = False
        last_error: Exception | None = None

        for chunk in chunks:

            async def _call(chunk=chunk):
                return await self._get_json(
                    f"/ws/fundamentals-timeseries/v1/finance/timeseries/{ticker}",
                    {
                        "symbol": ticker,
                        "type": ",".join(f"{prefix}{n}" for n in chunk),
                        "period1": period1,
                        "period2": period2,
                        "merge": "false",
                    },
                    ticker=ticker,
                )

            try:
                payload = await retry_async(_call)
            except NotFound:
                raise
            except Exception as exc:  # noqa: BLE001 - partial data is still useful
                last_error = exc
                log.warning("%s: fundamentals chunk failed: %s", ticker, exc)
                continue

            any_success = True
            cur = self._merge_timeseries(payload, prefix, collected)
            currency = currency or cur

        if not any_success and last_error is not None:
            raise last_error
        if not collected:
            raise NotFound(f"{ticker}: no {prefix} fundamentals returned")

        periods: list[FinancialPeriod] = []
        for as_of in sorted(collected):
            period = FinancialPeriod(
                period_end=as_of,
                period_type=period_type,
                source=SOURCE,
                currency=currency,
            )
            for attr, value in collected[as_of].items():
                setattr(period, attr, value)
            periods.append(period)
        return periods

    @staticmethod
    def _merge_timeseries(
        payload: dict, prefix: str, into: dict[date, dict[str, float]]
    ) -> str | None:
        """Fold one timeseries response into the ``as_of -> {attr: value}`` map."""
        series = (payload or {}).get("timeseries") or {}
        results = series.get("result") or []
        currency: str | None = None

        for entry in results:
            meta = entry.get("meta") or {}
            types = meta.get("type") or []
            if not types:
                continue
            type_name = types[0]
            if not type_name.startswith(prefix):
                continue
            attr = _FUNDAMENTAL_FIELDS.get(type_name[len(prefix) :])
            if attr is None:
                continue

            for observation in entry.get(type_name) or []:
                if not isinstance(observation, dict):
                    continue
                as_of = observation.get("asOfDate")
                if not as_of:
                    continue
                try:
                    as_of_date = date.fromisoformat(as_of)
                except ValueError:
                    continue
                value = to_float((observation.get("reportedValue") or {}).get("raw"))
                if value is None:
                    continue
                if attr in _ABS_FIELDS:
                    value = abs(value)
                into.setdefault(as_of_date, {})[attr] = value
                currency = currency or observation.get("currencyCode")

        return currency

    # ─────────────────────────────────────────────────────────
    # Profile and search
    # ─────────────────────────────────────────────────────────
    async def get_profile(self, ticker: str, symbol: str | None = None) -> CompanyProfile:
        symbol = symbol or ticker
        crumb = await self._ensure_crumb()

        async def _call():
            return await self._get_json(
                f"/v10/finance/quoteSummary/{ticker}",
                {
                    "modules": "assetProfile,price,summaryDetail,defaultKeyStatistics",
                    "crumb": crumb,
                },
                ticker=ticker,
            )

        payload = await retry_async(_call)
        summary = (payload or {}).get("quoteSummary") or {}
        if summary.get("error"):
            raise NotFound(f"{ticker}: {summary['error']}")
        results = summary.get("result") or []
        if not results:
            raise NotFound(f"{ticker}: quoteSummary returned no result")

        block = results[0]
        profile = block.get("assetProfile") or {}
        price = block.get("price") or {}

        return CompanyProfile(
            symbol=symbol,
            ticker=ticker,
            name=price.get("longName") or price.get("shortName"),
            sector=profile.get("sector"),
            industry=profile.get("industry"),
            country=profile.get("country"),
            currency=price.get("currency"),
            exchange=price.get("exchangeName"),
            website=profile.get("website"),
            summary=profile.get("longBusinessSummary"),
            employees=to_int(profile.get("fullTimeEmployees")),
            source=SOURCE,
            fetched_at=datetime.now(timezone.utc),
        )

    async def search(self, query: str, *, limit: int = 10) -> list[dict]:
        """Symbol lookup. Returns raw-but-narrowed dicts for the UI search box."""

        async def _call():
            return await self._get_json(
                "/v1/finance/search",
                {"q": query, "quotesCount": limit, "newsCount": 0, "listsCount": 0},
                ticker=query,
            )

        payload = await retry_async(_call)
        out = []
        for quote in (payload or {}).get("quotes", [])[:limit]:
            if quote.get("quoteType") not in ("EQUITY", "ETF", "INDEX", None):
                continue
            out.append(
                {
                    "ticker": quote.get("symbol"),
                    "name": quote.get("longname") or quote.get("shortname"),
                    "exchange": quote.get("exchDisp") or quote.get("exchange"),
                    "type": quote.get("quoteType"),
                }
            )
        return out
