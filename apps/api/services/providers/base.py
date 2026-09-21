"""
Market-data provider foundations.

Everything that talks to an outside data source goes through here so that
retries, rate limiting, and error classification are uniform and testable.

Design rules for this package:
  * A provider NEVER invents a number. If the source has no value, the field is
    ``None`` and the caller decides what to do.
  * A provider NEVER silently swallows an error into a default. It raises a
    typed exception so the ingest layer can record the failure.
  * Every returned record carries its ``source`` and ``fetched_at`` so a row in
    the database can always be traced back to where it came from.
"""
from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Iterable


# ─────────────────────────────────────────────────────────────
# Exceptions
# ─────────────────────────────────────────────────────────────
class ProviderError(Exception):
    """Base class for every provider failure."""


class RateLimited(ProviderError):
    """Upstream asked us to slow down (HTTP 429)."""

    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class NotFound(ProviderError):
    """Upstream has no such symbol."""


class UpstreamUnavailable(ProviderError):
    """Network failure, 5xx, or a blocked/denied route."""


class MalformedResponse(ProviderError):
    """We reached the source but the payload was not what the contract says."""


# ─────────────────────────────────────────────────────────────
# Value objects
# ─────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Quote:
    """A point-in-time price observation."""

    symbol: str
    ticker: str
    price: float
    previous_close: float | None
    open: float | None
    day_high: float | None
    day_low: float | None
    volume: int | None
    currency: str | None
    exchange: str | None
    market_state: str | None
    week_52_high: float | None
    week_52_low: float | None
    market_cap: float | None
    source: str
    fetched_at: datetime

    @property
    def change_abs(self) -> float | None:
        if self.previous_close is None:
            return None
        return self.price - self.previous_close

    @property
    def change_pct(self) -> float | None:
        if not self.previous_close:
            return None
        return (self.price - self.previous_close) / self.previous_close * 100.0


@dataclass(frozen=True)
class Candle:
    """One OHLCV bar. ``adj_close`` is split- and dividend-adjusted."""

    date: date
    open: float | None
    high: float | None
    low: float | None
    close: float
    adj_close: float | None
    volume: int | None


@dataclass
class FinancialPeriod:
    """
    One annual or quarterly reporting period for one company.

    Every field is Optional on purpose: real filings have holes, and a hole must
    stay a hole rather than becoming a zero that quietly corrupts a ratio.
    Units are the filing currency, absolute (not crores, not millions).
    """

    period_end: date
    period_type: str  # "A" annual | "Q" quarterly

    # Income statement
    revenue: float | None = None
    cost_of_revenue: float | None = None
    gross_profit: float | None = None
    operating_income: float | None = None
    ebitda: float | None = None
    depreciation: float | None = None
    interest_expense: float | None = None
    pretax_income: float | None = None
    tax_provision: float | None = None
    net_income: float | None = None
    eps_basic: float | None = None
    eps_diluted: float | None = None

    # Balance sheet
    total_assets: float | None = None
    total_liabilities: float | None = None
    equity: float | None = None
    total_debt: float | None = None
    long_term_debt: float | None = None
    short_term_debt: float | None = None
    cash: float | None = None
    short_term_investments: float | None = None
    working_capital: float | None = None
    invested_capital: float | None = None
    net_ppe: float | None = None
    shares_outstanding: float | None = None

    # Cash-flow statement
    cfo: float | None = None
    capex: float | None = None
    free_cash_flow: float | None = None
    dividends_paid: float | None = None
    buybacks: float | None = None

    # Provenance
    source: str = ""
    currency: str | None = None

    # ── Derived helpers (never stored, always recomputed) ──────
    @property
    def net_debt(self) -> float | None:
        if self.total_debt is None:
            return None
        cash = (self.cash or 0.0) + (self.short_term_investments or 0.0)
        return self.total_debt - cash

    @property
    def effective_tax_rate(self) -> float | None:
        """Cash tax rate for the period, clamped to a sane band."""
        if not self.pretax_income or self.tax_provision is None:
            return None
        if self.pretax_income <= 0:
            return None
        rate = self.tax_provision / self.pretax_income
        if rate < 0 or rate > 0.60:
            return None
        return rate

    @property
    def ebitda_margin(self) -> float | None:
        if not self.revenue or self.ebitda is None:
            return None
        return self.ebitda / self.revenue

    @property
    def operating_margin(self) -> float | None:
        if not self.revenue or self.operating_income is None:
            return None
        return self.operating_income / self.revenue

    @property
    def net_margin(self) -> float | None:
        if not self.revenue or self.net_income is None:
            return None
        return self.net_income / self.revenue

    @property
    def capex_intensity(self) -> float | None:
        """Capex as a share of revenue. Capex is stored as a positive number."""
        if not self.revenue or self.capex is None:
            return None
        return abs(self.capex) / self.revenue

    def completeness(self, fields: Iterable[str]) -> float:
        """Fraction of the named fields that actually carry a value."""
        fields = list(fields)
        if not fields:
            return 0.0
        present = sum(1 for f in fields if getattr(self, f, None) is not None)
        return present / len(fields)


@dataclass(frozen=True)
class CompanyProfile:
    """Static descriptive facts about a listed company."""

    symbol: str
    ticker: str
    name: str | None
    sector: str | None
    industry: str | None
    country: str | None
    currency: str | None
    exchange: str | None
    website: str | None
    summary: str | None
    employees: int | None
    source: str
    fetched_at: datetime


@dataclass
class FetchReport:
    """
    What happened during a bulk ingest. Written to the audit log so a failed
    overnight run can be diagnosed without re-running it.
    """

    requested: int = 0
    succeeded: int = 0
    failed: int = 0
    skipped: int = 0
    rows_written: int = 0
    errors: dict[str, str] = field(default_factory=dict)
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None

    def record_success(self, rows: int = 0) -> None:
        self.succeeded += 1
        self.rows_written += rows

    def record_failure(self, symbol: str, error: str) -> None:
        self.failed += 1
        # Keep the log bounded: a market-wide outage must not produce a 5000-key blob.
        if len(self.errors) < 50:
            self.errors[symbol] = error[:300]

    def record_skip(self) -> None:
        self.skipped += 1

    def finish(self) -> "FetchReport":
        self.finished_at = datetime.now(timezone.utc)
        return self

    @property
    def duration_seconds(self) -> float | None:
        if self.finished_at is None:
            return None
        return (self.finished_at - self.started_at).total_seconds()

    def as_dict(self) -> dict[str, Any]:
        return {
            "requested": self.requested,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "skipped": self.skipped,
            "rows_written": self.rows_written,
            "duration_seconds": self.duration_seconds,
            "errors": self.errors,
        }


# ─────────────────────────────────────────────────────────────
# Rate limiting
# ─────────────────────────────────────────────────────────────
class AsyncRateLimiter:
    """
    Token bucket. Public market-data endpoints throttle aggressively and an
    unthrottled 5000-symbol backfill will get the deployment banned, so every
    outbound request is metered.
    """

    def __init__(self, rate_per_sec: float, burst: int | None = None):
        if rate_per_sec <= 0:
            raise ValueError("rate_per_sec must be positive")
        self.rate = rate_per_sec
        self.capacity = burst if burst is not None else max(1, int(rate_per_sec))
        self._tokens = float(self.capacity)
        self._updated = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        while True:
            async with self._lock:
                now = time.monotonic()
                self._tokens = min(
                    self.capacity, self._tokens + (now - self._updated) * self.rate
                )
                self._updated = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                deficit = (1.0 - self._tokens) / self.rate
            await asyncio.sleep(deficit)


# ─────────────────────────────────────────────────────────────
# Retry
# ─────────────────────────────────────────────────────────────
async def retry_async(
    fn,
    *,
    attempts: int = 4,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    retry_on: tuple[type[Exception], ...] = (RateLimited, UpstreamUnavailable),
    jitter: bool = True,
):
    """
    Exponential backoff with jitter.

    ``NotFound`` and ``MalformedResponse`` are deliberately not retried: asking a
    second time for a symbol that does not exist just wastes the rate budget.
    """
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            return await fn()
        except retry_on as exc:
            last = exc
            if attempt == attempts - 1:
                break
            delay = min(max_delay, base_delay * (2**attempt))
            if isinstance(exc, RateLimited) and exc.retry_after:
                delay = max(delay, exc.retry_after)
            if jitter:
                delay *= 0.5 + random.random()
            await asyncio.sleep(delay)
    assert last is not None
    raise last


# ─────────────────────────────────────────────────────────────
# Parsing helpers
# ─────────────────────────────────────────────────────────────
def to_float(value: Any) -> float | None:
    """
    Coerce a provider value to float, or None.

    Rejects NaN and infinity, which otherwise propagate silently through a DCF
    and surface as a NaN intrinsic value on the customer's screen.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, dict):  # Yahoo wraps some numbers as {"raw": 1.23, ...}
        value = value.get("raw")
        if value is None:
            return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if out != out or out in (float("inf"), float("-inf")):
        return None
    return out


def to_int(value: Any) -> int | None:
    out = to_float(value)
    return None if out is None else int(out)


def epoch_to_date(value: Any) -> date | None:
    """Seconds since epoch (UTC) to a calendar date."""
    ts = to_float(value)
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc).date()
    except (OverflowError, OSError, ValueError):
        return None
