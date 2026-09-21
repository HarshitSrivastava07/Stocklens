"""
Technical indicators computed from real daily candles.

Every function here takes the actual price series that came out of the provider
and returns either a real number or ``None``. None of them accept a "default"
value, because a fabricated RSI is indistinguishable from a real one once it
reaches a chart, and a customer will trade on it.

Conventions:
  * Inputs are ordered oldest-first, matching the provider's candle output.
  * Rolling functions return a list the same length as the input, with ``None``
    in the leading positions where the window is not yet full. Silently
    returning a shorter list is how off-by-one errors reach production charts.
  * Valuation-facing work uses adjusted closes; display uses raw closes. The
    caller chooses which series to pass.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Sequence

from .timeseries import annualized_volatility, max_drawdown, returns_from_prices, stdev

Number = float | int | None


def _f(values: Sequence[Number]) -> list[float | None]:
    """Normalize to floats, preserving position by mapping bad values to None."""
    out: list[float | None] = []
    for v in values:
        if v is None:
            out.append(None)
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            out.append(None)
            continue
        out.append(None if f != f or f in (float("inf"), float("-inf")) else f)
    return out


# ─────────────────────────────────────────────────────────────
# Moving averages
# ─────────────────────────────────────────────────────────────
def sma(values: Sequence[Number], period: int) -> list[float | None]:
    """Simple moving average. ``None`` until the window is full."""
    if period <= 0:
        raise ValueError("period must be positive")
    data = _f(values)
    out: list[float | None] = []
    window: list[float] = []

    for value in data:
        if value is None:
            # A gap invalidates the window rather than shortening it silently.
            window.clear()
            out.append(None)
            continue
        window.append(value)
        if len(window) > period:
            window.pop(0)
        out.append(sum(window) / period if len(window) == period else None)
    return out


def ema(values: Sequence[Number], period: int) -> list[float | None]:
    """
    Exponential moving average, seeded with the first full SMA.

    Seeding with the SMA rather than the first price is what makes an EMA
    comparable across charting tools; seeding with the first close leaves a
    visible artefact at the left edge of the series.
    """
    if period <= 0:
        raise ValueError("period must be positive")
    data = _f(values)
    out: list[float | None] = [None] * len(data)
    multiplier = 2.0 / (period + 1)

    window: list[float] = []
    previous: float | None = None

    for i, value in enumerate(data):
        if value is None:
            window.clear()
            previous = None
            continue
        if previous is None:
            window.append(value)
            if len(window) == period:
                previous = sum(window) / period
                out[i] = previous
            continue
        previous = (value - previous) * multiplier + previous
        out[i] = previous
    return out


# ─────────────────────────────────────────────────────────────
# Momentum
# ─────────────────────────────────────────────────────────────
def rsi(values: Sequence[Number], period: int = 14) -> list[float | None]:
    """
    Wilder's relative strength index.

    Uses Wilder's smoothing (an EMA with alpha = 1/period), not a simple average
    of the last N changes. The simple-average variant is a different indicator
    and disagrees with every standard chart.
    """
    data = _f(values)
    out: list[float | None] = [None] * len(data)
    if period <= 0 or len(data) <= period:
        return out

    gains: list[float] = []
    losses: list[float] = []
    avg_gain: float | None = None
    avg_loss: float | None = None

    for i in range(1, len(data)):
        current, previous = data[i], data[i - 1]
        if current is None or previous is None:
            gains.clear()
            losses.clear()
            avg_gain = avg_loss = None
            continue

        change = current - previous
        gain = max(change, 0.0)
        loss = max(-change, 0.0)

        if avg_gain is None:
            gains.append(gain)
            losses.append(loss)
            if len(gains) == period:
                avg_gain = sum(gains) / period
                avg_loss = sum(losses) / period
                out[i] = _rsi_from(avg_gain, avg_loss)
            continue

        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        out[i] = _rsi_from(avg_gain, avg_loss)

    return out


def _rsi_from(avg_gain: float, avg_loss: float) -> float:
    # An unbroken run of up days has no downside to divide by; RSI is 100.
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


@dataclass
class MacdResult:
    macd: list[float | None]
    signal: list[float | None]
    histogram: list[float | None]


def macd(
    values: Sequence[Number], fast: int = 12, slow: int = 26, signal_period: int = 9
) -> MacdResult:
    """Moving-average convergence/divergence with its signal line and histogram."""
    fast_ema = ema(values, fast)
    slow_ema = ema(values, slow)
    line = [
        (f - s) if (f is not None and s is not None) else None
        for f, s in zip(fast_ema, slow_ema)
    ]

    # The signal line is an EMA of the MACD line, which only exists once the slow
    # EMA does; feeding the leading Nones through would shift it.
    populated = [v for v in line if v is not None]
    signal_tail = ema(populated, signal_period)
    signal: list[float | None] = []
    cursor = 0
    for value in line:
        if value is None:
            signal.append(None)
        else:
            signal.append(signal_tail[cursor])
            cursor += 1

    histogram = [
        (m - s) if (m is not None and s is not None) else None
        for m, s in zip(line, signal)
    ]
    return MacdResult(macd=line, signal=signal, histogram=histogram)


# ─────────────────────────────────────────────────────────────
# Volatility and range
# ─────────────────────────────────────────────────────────────
def true_range(
    highs: Sequence[Number], lows: Sequence[Number], closes: Sequence[Number]
) -> list[float | None]:
    """Greatest of today's range, and each gap from yesterday's close."""
    h, l, c = _f(highs), _f(lows), _f(closes)
    out: list[float | None] = [None] * len(c)
    for i in range(len(c)):
        if h[i] is None or l[i] is None:
            continue
        if i == 0 or c[i - 1] is None:
            out[i] = h[i] - l[i]
            continue
        out[i] = max(
            h[i] - l[i],
            abs(h[i] - c[i - 1]),
            abs(l[i] - c[i - 1]),
        )
    return out


def atr(
    highs: Sequence[Number],
    lows: Sequence[Number],
    closes: Sequence[Number],
    period: int = 14,
) -> list[float | None]:
    """
    Average true range, Wilder-smoothed.

    The signal engine sizes its stop loss in ATR rather than a flat percentage,
    so a quiet large-cap and a volatile small-cap each get a stop appropriate to
    how far they actually move.
    """
    tr = true_range(highs, lows, closes)
    out: list[float | None] = [None] * len(tr)
    window: list[float] = []
    previous: float | None = None

    for i, value in enumerate(tr):
        if value is None:
            window.clear()
            previous = None
            continue
        if previous is None:
            window.append(value)
            if len(window) == period:
                previous = sum(window) / period
                out[i] = previous
            continue
        previous = (previous * (period - 1) + value) / period
        out[i] = previous
    return out


@dataclass
class BollingerBands:
    upper: list[float | None]
    middle: list[float | None]
    lower: list[float | None]
    percent_b: list[float | None]


def bollinger(
    values: Sequence[Number], period: int = 20, num_std: float = 2.0
) -> BollingerBands:
    """Bands at N standard deviations around the SMA, plus %B position."""
    data = _f(values)
    middle = sma(data, period)
    upper: list[float | None] = []
    lower: list[float | None] = []
    percent_b: list[float | None] = []

    for i in range(len(data)):
        centre = middle[i]
        window = data[max(0, i - period + 1) : i + 1]
        if centre is None or any(v is None for v in window) or len(window) < period:
            upper.append(None)
            lower.append(None)
            percent_b.append(None)
            continue
        deviation = stdev(window) or 0.0
        hi = centre + num_std * deviation
        lo = centre - num_std * deviation
        upper.append(hi)
        lower.append(lo)
        width = hi - lo
        value = data[i]
        percent_b.append(None if width <= 0 or value is None else (value - lo) / width)

    return BollingerBands(upper=upper, middle=middle, lower=lower, percent_b=percent_b)


# ─────────────────────────────────────────────────────────────
# Snapshot
# ─────────────────────────────────────────────────────────────
@dataclass
class TechnicalSnapshot:
    """
    Current technical state, computed from real candles.

    Every field is Optional: a stock listed three months ago genuinely has no
    200-day moving average, and the honest representation of that is ``None``.
    """

    as_of: object = None
    price: float | None = None

    sma_20: float | None = None
    sma_50: float | None = None
    sma_200: float | None = None
    ema_12: float | None = None
    ema_26: float | None = None

    rsi_14: float | None = None
    macd_line: float | None = None
    macd_signal: float | None = None
    macd_histogram: float | None = None

    atr_14: float | None = None
    atr_pct: float | None = None
    bollinger_percent_b: float | None = None

    week_52_high: float | None = None
    week_52_low: float | None = None
    pct_from_52w_high: float | None = None
    pct_from_52w_low: float | None = None

    return_1m: float | None = None
    return_3m: float | None = None
    return_6m: float | None = None
    return_1y: float | None = None
    return_3y_cagr: float | None = None
    return_5y_cagr: float | None = None

    volatility_1y: float | None = None
    max_drawdown_5y: float | None = None
    avg_volume_20d: float | None = None
    volume_ratio: float | None = None

    trend: str = "UNKNOWN"
    candles_used: int = 0
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        def r(value, places=4):
            return None if value is None else round(value, places)

        return {
            "as_of": str(self.as_of) if self.as_of else None,
            "price": r(self.price, 2),
            "sma_20": r(self.sma_20, 2),
            "sma_50": r(self.sma_50, 2),
            "sma_200": r(self.sma_200, 2),
            "ema_12": r(self.ema_12, 2),
            "ema_26": r(self.ema_26, 2),
            "rsi_14": r(self.rsi_14, 2),
            "macd_line": r(self.macd_line, 4),
            "macd_signal": r(self.macd_signal, 4),
            "macd_histogram": r(self.macd_histogram, 4),
            "atr_14": r(self.atr_14, 2),
            "atr_pct": r(self.atr_pct),
            "bollinger_percent_b": r(self.bollinger_percent_b),
            "week_52_high": r(self.week_52_high, 2),
            "week_52_low": r(self.week_52_low, 2),
            "pct_from_52w_high": r(self.pct_from_52w_high),
            "pct_from_52w_low": r(self.pct_from_52w_low),
            "return_1m": r(self.return_1m),
            "return_3m": r(self.return_3m),
            "return_6m": r(self.return_6m),
            "return_1y": r(self.return_1y),
            "return_3y_cagr": r(self.return_3y_cagr),
            "return_5y_cagr": r(self.return_5y_cagr),
            "volatility_1y": r(self.volatility_1y),
            "max_drawdown_5y": r(self.max_drawdown_5y),
            "avg_volume_20d": r(self.avg_volume_20d, 0),
            "volume_ratio": r(self.volume_ratio),
            "trend": self.trend,
            "candles_used": self.candles_used,
            "warnings": self.warnings,
        }


# Roughly one trading year; used for lookback windows.
TRADING_DAYS_YEAR = 252
TRADING_DAYS_MONTH = 21


def _trailing_return(closes: list[float], lookback: int) -> float | None:
    if len(closes) <= lookback:
        return None
    past = closes[-1 - lookback]
    if past <= 0:
        return None
    return closes[-1] / past - 1.0


def _trailing_cagr(closes: list[float], years: int) -> float | None:
    lookback = TRADING_DAYS_YEAR * years
    if len(closes) <= lookback:
        return None
    past = closes[-1 - lookback]
    if past <= 0:
        return None
    return (closes[-1] / past) ** (1.0 / years) - 1.0


def compute_snapshot(candles: Sequence) -> TechnicalSnapshot:
    """
    Build the current technical picture from a list of provider ``Candle``s.

    Indicators that the available history cannot support are left ``None`` and
    noted in ``warnings`` — never filled with a shorter-window substitute, which
    would silently mislabel a 50-day average as a 200-day one.
    """
    snapshot = TechnicalSnapshot()
    if not candles:
        snapshot.warnings.append("No price history available")
        return snapshot

    ordered = sorted(candles, key=lambda c: c.date)
    closes = [c.close for c in ordered if c.close is not None]
    if not closes:
        snapshot.warnings.append("Price history contains no usable closes")
        return snapshot

    highs = [c.high for c in ordered]
    lows = [c.low for c in ordered]
    raw_closes = [c.close for c in ordered]
    volumes = [c.volume for c in ordered if c.volume is not None]

    snapshot.as_of = ordered[-1].date
    snapshot.price = closes[-1]
    snapshot.candles_used = len(ordered)

    snapshot.sma_20 = sma(closes, 20)[-1]
    snapshot.sma_50 = sma(closes, 50)[-1]
    snapshot.sma_200 = sma(closes, 200)[-1]
    snapshot.ema_12 = ema(closes, 12)[-1]
    snapshot.ema_26 = ema(closes, 26)[-1]

    snapshot.rsi_14 = rsi(closes, 14)[-1]

    macd_result = macd(closes)
    snapshot.macd_line = macd_result.macd[-1]
    snapshot.macd_signal = macd_result.signal[-1]
    snapshot.macd_histogram = macd_result.histogram[-1]

    snapshot.atr_14 = atr(highs, lows, raw_closes, 14)[-1]
    if snapshot.atr_14 is not None and snapshot.price:
        snapshot.atr_pct = snapshot.atr_14 / snapshot.price

    snapshot.bollinger_percent_b = bollinger(closes, 20).percent_b[-1]

    # 52-week extremes from the actual traded range, not the provider's summary.
    year_window = closes[-TRADING_DAYS_YEAR:]
    if year_window:
        snapshot.week_52_high = max(year_window)
        snapshot.week_52_low = min(year_window)
        if snapshot.week_52_high:
            snapshot.pct_from_52w_high = (
                snapshot.price - snapshot.week_52_high
            ) / snapshot.week_52_high
        if snapshot.week_52_low:
            snapshot.pct_from_52w_low = (
                snapshot.price - snapshot.week_52_low
            ) / snapshot.week_52_low

    snapshot.return_1m = _trailing_return(closes, TRADING_DAYS_MONTH)
    snapshot.return_3m = _trailing_return(closes, TRADING_DAYS_MONTH * 3)
    snapshot.return_6m = _trailing_return(closes, TRADING_DAYS_MONTH * 6)
    snapshot.return_1y = _trailing_return(closes, TRADING_DAYS_YEAR)
    snapshot.return_3y_cagr = _trailing_cagr(closes, 3)
    snapshot.return_5y_cagr = _trailing_cagr(closes, 5)

    snapshot.volatility_1y = annualized_volatility(
        returns_from_prices(closes[-TRADING_DAYS_YEAR:])
    )
    snapshot.max_drawdown_5y = max_drawdown(closes[-TRADING_DAYS_YEAR * 5 :])

    if len(volumes) >= 20:
        snapshot.avg_volume_20d = sum(volumes[-20:]) / 20
        if snapshot.avg_volume_20d:
            snapshot.volume_ratio = volumes[-1] / snapshot.avg_volume_20d

    snapshot.trend = classify_trend(snapshot)

    if len(ordered) < 200:
        snapshot.warnings.append(
            f"Only {len(ordered)} sessions of history; long-window indicators unavailable"
        )
    return snapshot


def classify_trend(snapshot: TechnicalSnapshot) -> str:
    """
    Label the trend from moving-average structure.

    Returns UNKNOWN when there is not enough history to say, rather than
    defaulting to a neutral-sounding label that reads as a real assessment.
    """
    price, sma50, sma200 = snapshot.price, snapshot.sma_50, snapshot.sma_200
    if price is None or sma50 is None:
        return "UNKNOWN"

    if sma200 is None:
        return "UPTREND" if price > sma50 else "DOWNTREND"

    if price > sma50 > sma200:
        return "STRONG_UPTREND"
    if price > sma200 and price > sma50:
        return "UPTREND"
    if price < sma50 < sma200:
        return "STRONG_DOWNTREND"
    if price < sma200:
        return "DOWNTREND"
    return "SIDEWAYS"


def regress_beta_against(
    stock_candles: Sequence, index_candles: Sequence, *, weekly: bool = True
) -> tuple[float | None, int]:
    """
    Beta of the stock against its index, from overlapping real history.

    Aligning on the dates both series actually traded matters: a naive
    zip of two lists silently pairs a stock's Monday with the index's Tuesday
    whenever one market had a holiday the other did not, which quietly corrupts
    the regression.

    Weekly sampling is the default because daily returns on thinly-traded names
    are dominated by bid-ask bounce, which biases beta toward zero.
    """
    stock_by_date = {c.date: c.close for c in stock_candles if c.close is not None}
    index_by_date = {c.date: c.close for c in index_candles if c.close is not None}
    shared = sorted(set(stock_by_date) & set(index_by_date))
    if len(shared) < 30:
        return None, len(shared)

    if weekly:
        shared = shared[::5]
        if len(shared) < 20:
            return None, len(shared)

    stock_series = [stock_by_date[d] for d in shared]
    index_series = [index_by_date[d] for d in shared]

    from .timeseries import beta as _beta

    stock_returns = returns_from_prices(stock_series)
    index_returns = returns_from_prices(index_series)
    return _beta(stock_returns, index_returns), len(shared)
