"""
Statistics for financial time series.

Deliberately dependency-free (no numpy) so the valuation engine can run inside a
worker, a script, or a test without carrying a scientific stack. Every function
returns ``None`` rather than a fallback number when the input cannot support an
answer — a valuation built on invented statistics is worse than no valuation.
"""
from __future__ import annotations

import math
from typing import Sequence


Number = float | int | None


def _clean(values: Sequence[Number]) -> list[float]:
    """Drop Nones, NaNs and infinities."""
    out: list[float] = []
    for v in values:
        if v is None:
            continue
        f = float(v)
        if f != f or f in (float("inf"), float("-inf")):
            continue
        out.append(f)
    return out


def median(values: Sequence[Number]) -> float | None:
    data = sorted(_clean(values))
    if not data:
        return None
    mid = len(data) // 2
    if len(data) % 2:
        return data[mid]
    return (data[mid - 1] + data[mid]) / 2.0


def mean(values: Sequence[Number]) -> float | None:
    data = _clean(values)
    if not data:
        return None
    return sum(data) / len(data)


def stdev(values: Sequence[Number]) -> float | None:
    """Sample standard deviation. Needs at least two observations."""
    data = _clean(values)
    if len(data) < 2:
        return None
    mu = sum(data) / len(data)
    var = sum((x - mu) ** 2 for x in data) / (len(data) - 1)
    return math.sqrt(var)


def coefficient_of_variation(values: Sequence[Number]) -> float | None:
    """
    Volatility relative to level. The engine uses this to judge how stable a
    company's margins have been, which drives valuation confidence.
    """
    data = _clean(values)
    mu = mean(data)
    sd = stdev(data)
    if mu is None or sd is None or abs(mu) < 1e-12:
        return None
    return sd / abs(mu)


def cagr(begin: Number, end: Number, years: float) -> float | None:
    """
    Compound annual growth rate.

    Returns ``None`` when the maths is undefined rather than a misleading number:
    a company that went from a loss to a profit has no meaningful CAGR, and
    pretending otherwise puts a fabricated growth rate into a DCF.
    """
    if begin is None or end is None or years <= 0:
        return None
    b, e = float(begin), float(end)
    if b <= 0 or e <= 0:
        return None
    try:
        return (e / b) ** (1.0 / years) - 1.0
    except (ValueError, ZeroDivisionError, OverflowError):
        return None


def series_cagr(values: Sequence[Number]) -> float | None:
    """CAGR across an ordered (oldest-first) series, spanning len-1 periods."""
    data = _clean(values)
    if len(data) < 2:
        return None
    return cagr(data[0], data[-1], len(data) - 1)


def yoy_changes(values: Sequence[Number]) -> list[float]:
    """Year-on-year growth rates. Periods spanning a sign change are skipped."""
    data = _clean(values)
    out: list[float] = []
    for prev, cur in zip(data, data[1:]):
        if prev is None or abs(prev) < 1e-12 or prev < 0:
            continue
        out.append((cur - prev) / prev)
    return out


def linear_trend(values: Sequence[Number]) -> tuple[float, float] | None:
    """
    Ordinary least squares fit against the index 0..n-1.

    Returns ``(slope_per_period, intercept)``. Used to tell a company whose
    margin is structurally improving from one whose good year was a fluke.
    """
    data = _clean(values)
    n = len(data)
    if n < 2:
        return None
    xs = list(range(n))
    mean_x = sum(xs) / n
    mean_y = sum(data) / n
    denom = sum((x - mean_x) ** 2 for x in xs)
    if abs(denom) < 1e-12:
        return None
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, data)) / denom
    return slope, mean_y - slope * mean_x


def winsorize(values: Sequence[Number], limit: float = 0.10) -> list[float]:
    """
    Clamp the extreme tails to the given quantile.

    One pandemic year or one asset-sale windfall should not set a ten-year
    growth assumption, but nor should it be deleted outright.
    """
    data = sorted(_clean(values))
    n = len(data)
    if n < 3 or not (0 < limit < 0.5):
        return _clean(values)
    k = max(1, int(n * limit))
    lo, hi = data[k - 1], data[n - k]
    return [min(max(v, lo), hi) for v in _clean(values)]


def robust_growth(values: Sequence[Number]) -> float | None:
    """
    A defensible growth rate from a noisy series.

    Blends the endpoint-to-endpoint CAGR with the median year-on-year change.
    The CAGR alone is hostage to the first and last year; the median alone
    ignores compounding. Taking the lower of the two is the conservative choice
    a valuation should make.
    """
    full = series_cagr(values)
    yoy_median = median(winsorize(yoy_changes(values)))
    candidates = [c for c in (full, yoy_median) if c is not None]
    if not candidates:
        return None
    return min(candidates)


def covariance(xs: Sequence[Number], ys: Sequence[Number]) -> float | None:
    a, b = _clean(xs), _clean(ys)
    n = min(len(a), len(b))
    if n < 2:
        return None
    a, b = a[:n], b[:n]
    mean_a, mean_b = sum(a) / n, sum(b) / n
    return sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b)) / (n - 1)


def beta(asset_returns: Sequence[Number], market_returns: Sequence[Number]) -> float | None:
    """
    Regression beta: cov(asset, market) / var(market).

    This is the real, measured sensitivity of the stock to its index — not a
    sector-average guess.
    """
    cov = covariance(asset_returns, market_returns)
    market_var = stdev(market_returns)
    if cov is None or market_var is None or market_var <= 0:
        return None
    return cov / (market_var**2)


def returns_from_prices(prices: Sequence[Number]) -> list[float]:
    """Simple period-over-period returns from an ordered price series."""
    data = _clean(prices)
    out: list[float] = []
    for prev, cur in zip(data, data[1:]):
        if prev <= 0:
            continue
        out.append(cur / prev - 1.0)
    return out


def annualized_volatility(returns: Sequence[Number], periods_per_year: int = 252) -> float | None:
    sd = stdev(returns)
    if sd is None:
        return None
    return sd * math.sqrt(periods_per_year)


def max_drawdown(prices: Sequence[Number]) -> float | None:
    """Deepest peak-to-trough fall in the series, as a negative fraction."""
    data = _clean(prices)
    if len(data) < 2:
        return None
    peak = data[0]
    worst = 0.0
    for price in data:
        peak = max(peak, price)
        if peak > 0:
            worst = min(worst, price / peak - 1.0)
    return worst


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
