"""
Buy / sell decision engine.

Turns a valuation, a quality assessment and the current technical state into an
actionable view: what to do, at what price, where to stop out, and what would
change the answer.

Two principles run through this module.

**Value decides direction, price action decides timing.** A cheap stock in a
collapsing downtrend is not a buy today; an expensive stock in a strong uptrend
is not a short. Valuation sets whether we want to own the business at all, and
the technical state sets whether now is the moment. Neither alone is sufficient,
and the engine will not issue a buy on one without the other.

**Every output is falsifiable.** Each signal carries the specific conditions
that produced it and the specific conditions that would reverse it, so a user
can check the reasoning rather than trusting a colour.

Nothing here is a trade recommendation to a third party. The output is a
research view with its inputs exposed, and the API labels it as such.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Sequence

from .intrinsic_value import HistoryProfile, ValuationResult
from .technicals import TechnicalSnapshot
from .timeseries import clamp


# ─────────────────────────────────────────────────────────────
# Actions
# ─────────────────────────────────────────────────────────────
STRONG_BUY = "STRONG_BUY"
BUY = "BUY"
ACCUMULATE = "ACCUMULATE"
HOLD = "HOLD"
REDUCE = "REDUCE"
SELL = "SELL"
AVOID = "AVOID"
INSUFFICIENT_DATA = "INSUFFICIENT_DATA"

_ACTION_LABELS = {
    STRONG_BUY: "Strong Buy",
    BUY: "Buy",
    ACCUMULATE: "Accumulate on weakness",
    HOLD: "Hold",
    REDUCE: "Reduce",
    SELL: "Sell",
    AVOID: "Avoid",
    INSUFFICIENT_DATA: "Insufficient data",
}

_ACTION_COLORS = {
    STRONG_BUY: "GREEN",
    BUY: "GREEN",
    ACCUMULATE: "LIME",
    HOLD: "YELLOW",
    REDUCE: "ORANGE",
    SELL: "RED",
    AVOID: "RED",
    INSUFFICIENT_DATA: "GREY",
}


@dataclass
class TradePlan:
    """The actionable half of a signal: prices, not adjectives."""

    entry_low: float | None = None
    entry_high: float | None = None
    max_buy_price: float | None = None
    stop_loss: float | None = None
    target_1: float | None = None
    target_2: float | None = None
    risk_reward: float | None = None
    position_size_pct: float | None = None
    horizon: str = ""

    def as_dict(self) -> dict:
        def r(v, p=2):
            return None if v is None else round(v, p)

        return {
            "entry_low": r(self.entry_low),
            "entry_high": r(self.entry_high),
            "max_buy_price": r(self.max_buy_price),
            "stop_loss": r(self.stop_loss),
            "target_1": r(self.target_1),
            "target_2": r(self.target_2),
            "risk_reward": r(self.risk_reward),
            "position_size_pct": r(self.position_size_pct),
            "horizon": self.horizon,
        }


@dataclass
class Signal:
    """A complete, auditable view on one stock."""

    symbol: str
    action: str = INSUFFICIENT_DATA
    label: str = ""
    color: str = "GREY"
    conviction: float = 0.0

    headline: str = ""
    rationale: list[str] = field(default_factory=list)
    invalidation: list[str] = field(default_factory=list)

    value_score: float | None = None
    quality_score: float | None = None
    momentum_score: float | None = None
    risk_score: float | None = None
    composite_score: float | None = None

    plan: TradePlan = field(default_factory=TradePlan)
    risk_flags: list[str] = field(default_factory=list)
    conditions: dict = field(default_factory=dict)

    current_price: float | None = None
    intrinsic_value: float | None = None
    upside_pct: float | None = None
    valuation_confidence: str = "LOW"
    trend: str = "UNKNOWN"
    computed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def as_dict(self) -> dict:
        def r(v, p=2):
            return None if v is None else round(v, p)

        return {
            "symbol": self.symbol,
            "action": self.action,
            "label": self.label,
            "color": self.color,
            "conviction": r(self.conviction, 3),
            "headline": self.headline,
            "rationale": self.rationale,
            "invalidation": self.invalidation,
            "value_score": r(self.value_score, 1),
            "quality_score": r(self.quality_score, 1),
            "momentum_score": r(self.momentum_score, 1),
            "risk_score": r(self.risk_score, 1),
            "composite_score": r(self.composite_score, 1),
            "plan": self.plan.as_dict(),
            "risk_flags": self.risk_flags,
            "conditions": self.conditions,
            "current_price": r(self.current_price),
            "intrinsic_value": r(self.intrinsic_value),
            "upside_pct": r(self.upside_pct),
            "valuation_confidence": self.valuation_confidence,
            "trend": self.trend,
            "computed_at": self.computed_at.isoformat(),
        }


# ─────────────────────────────────────────────────────────────
# Component scores, each 0..100
# ─────────────────────────────────────────────────────────────
def score_value(valuation: ValuationResult) -> float | None:
    """
    How much cheaper than its worth, adjusted for how sure we are.

    Upside is scaled by valuation confidence, because a 90% upside on a
    four-year history with wildly swinging margins is not worth the same as a
    40% upside on a decade of stable filings.
    """
    if not valuation.ok or valuation.upside_pct is None:
        return None

    upside = valuation.upside_pct
    # 0% upside -> 50; +60% -> 100; -60% -> 0.
    raw = 50.0 + (upside / 60.0) * 50.0
    raw = clamp(raw, 0.0, 100.0)

    # Pull toward neutral in proportion to how little we trust the valuation.
    confidence = clamp(valuation.confidence_score, 0.0, 1.0)
    return 50.0 + (raw - 50.0) * confidence


def score_quality(profile: HistoryProfile | None, wacc: float | None) -> float | None:
    """
    Is this a business worth owning at any price?

    Built only from components the filings actually support; each contributes
    only when its input exists, and the score is the average of what was
    measurable rather than a total penalised for missing data.
    """
    if profile is None:
        return None

    parts: list[float] = []

    # Returns on capital above the cost of capital is the whole game.
    if profile.roic_median is not None and wacc:
        spread = profile.roic_median - wacc
        parts.append(clamp(50.0 + spread * 400.0, 0.0, 100.0))

    if profile.operating_margin_median is not None:
        parts.append(clamp(profile.operating_margin_median * 330.0, 0.0, 100.0))

    # Consistency of earnings.
    if profile.years:
        parts.append(profile.profitable_years / profile.years * 100.0)
        parts.append(profile.fcf_positive_years / profile.years * 100.0)

    # Stable margins beat erratic ones.
    if profile.operating_margin_stability is not None:
        parts.append(clamp(100.0 - profile.operating_margin_stability * 150.0, 0.0, 100.0))

    # Growth, with diminishing credit above 20%.
    if profile.revenue_cagr is not None:
        parts.append(clamp(50.0 + profile.revenue_cagr * 250.0, 0.0, 100.0))

    # Leverage.
    if profile.net_debt is not None and profile.latest_equity:
        gearing = profile.net_debt / profile.latest_equity
        parts.append(clamp(100.0 - gearing * 60.0, 0.0, 100.0))

    if not parts:
        return None
    return sum(parts) / len(parts)


def score_momentum(tech: TechnicalSnapshot | None) -> float | None:
    """
    Is the market currently agreeing or disagreeing?

    Momentum is a timing input only. It never establishes that a business is
    worth owning; it establishes whether the market has stopped disagreeing.
    """
    if tech is None or tech.price is None:
        return None

    parts: list[float] = []

    trend_points = {
        "STRONG_UPTREND": 85.0,
        "UPTREND": 70.0,
        "SIDEWAYS": 50.0,
        "DOWNTREND": 30.0,
        "STRONG_DOWNTREND": 15.0,
    }
    if tech.trend in trend_points:
        parts.append(trend_points[tech.trend])

    # RSI: reward the middle, penalise both extremes. Overbought is a poor entry;
    # deeply oversold is often a falling knife.
    if tech.rsi_14 is not None:
        rsi_value = tech.rsi_14
        if rsi_value >= 70:
            parts.append(clamp(100.0 - (rsi_value - 70.0) * 3.0, 0.0, 60.0))
        elif rsi_value <= 30:
            parts.append(clamp(30.0 + rsi_value, 0.0, 60.0))
        else:
            parts.append(50.0 + (rsi_value - 50.0) * 0.8)

    if tech.macd_histogram is not None and tech.price:
        normalized = tech.macd_histogram / tech.price * 100.0
        parts.append(clamp(50.0 + normalized * 25.0, 0.0, 100.0))

    if tech.return_6m is not None:
        parts.append(clamp(50.0 + tech.return_6m * 125.0, 0.0, 100.0))

    if tech.pct_from_52w_high is not None:
        # Near the high is constructive; deeply below it is not.
        parts.append(clamp(100.0 + tech.pct_from_52w_high * 200.0, 0.0, 100.0))

    if not parts:
        return None
    return sum(parts) / len(parts)


def score_risk(
    profile: HistoryProfile | None,
    tech: TechnicalSnapshot | None,
    valuation: ValuationResult | None,
) -> tuple[float, list[str]]:
    """
    Risk from 0 (safe) to 100 (dangerous), with the specific flags that drove it.

    Returns the flags as well as the number so the UI can show *why* something is
    risky rather than only how much.
    """
    score = 20.0  # every equity carries some risk
    flags: list[str] = []

    if profile is not None:
        if profile.net_debt is not None and profile.latest_equity and profile.latest_equity > 0:
            gearing = profile.net_debt / profile.latest_equity
            if gearing > 2.0:
                score += 25
                flags.append(f"Net debt is {gearing:.1f}x equity")
            elif gearing > 1.0:
                score += 12
                flags.append(f"Net debt is {gearing:.1f}x equity")

        if profile.years and profile.profitable_years < profile.years:
            missed = profile.years - profile.profitable_years
            score += min(20.0, missed * 6.0)
            flags.append(f"Loss-making in {missed} of {profile.years} years")

        if profile.fcf_positive_years == 0 and profile.years:
            score += 15
            flags.append("Never generated positive free cash flow in the period")

        if profile.operating_margin_stability is not None and profile.operating_margin_stability > 0.6:
            score += 12
            flags.append("Operating margin is highly volatile")

        if profile.share_count_cagr is not None and profile.share_count_cagr > 0.05:
            score += 10
            flags.append(
                f"Share count growing {profile.share_count_cagr*100:.1f}%/yr"
            )

        if profile.roic_median is not None and profile.roic_median < 0.05:
            score += 10
            flags.append("Return on invested capital below 5%")

    if tech is not None:
        if tech.volatility_1y is not None and tech.volatility_1y > 0.55:
            score += 12
            flags.append(f"Annualised volatility {tech.volatility_1y*100:.0f}%")
        if tech.max_drawdown_5y is not None and tech.max_drawdown_5y < -0.60:
            score += 8
            flags.append(f"Fell {abs(tech.max_drawdown_5y)*100:.0f}% peak-to-trough")
        if tech.trend == "STRONG_DOWNTREND":
            score += 10
            flags.append("In a sustained downtrend")

    if valuation is not None and valuation.ok:
        if valuation.confidence == "LOW":
            score += 12
            flags.append("Valuation confidence is low")
        if valuation.terminal_value_share is not None and valuation.terminal_value_share > 0.90:
            score += 8
            flags.append("Valuation rests almost entirely on terminal assumptions")

    return clamp(score, 0.0, 100.0), flags


# ─────────────────────────────────────────────────────────────
# Trade plan
# ─────────────────────────────────────────────────────────────
# Fraction of intrinsic value we insist on paying below, by confidence. A
# valuation we trust less must be bought at a bigger discount to be worth the
# same risk.
_MARGIN_OF_SAFETY = {"HIGH": 0.20, "MEDIUM": 0.30, "LOW": 0.40}

# Stop distance in ATR multiples. Sizing the stop in units of how far the stock
# actually moves means a quiet large-cap and a volatile small-cap each get a stop
# appropriate to their own behaviour, instead of an arbitrary flat percentage.
_STOP_ATR_MULTIPLE = 2.5


def build_trade_plan(
    action: str,
    price: float,
    valuation: ValuationResult,
    tech: TechnicalSnapshot | None,
    risk: float,
) -> TradePlan:
    """
    Turn a view into prices.

    The entry zone is anchored on intrinsic value less a confidence-scaled
    margin of safety, then pulled toward technical support where support exists,
    so the plan is buyable rather than theoretical.
    """
    plan = TradePlan()
    iv = valuation.iv_blended
    if not iv or iv <= 0 or not price or price <= 0:
        return plan

    if action in (SELL, AVOID, INSUFFICIENT_DATA):
        # An exit plan, not an entry one.
        plan.horizon = "Exit"
        if action == SELL:
            plan.target_1 = iv  # fair value is where the argument to sell weakens
        return plan

    margin = _MARGIN_OF_SAFETY.get(valuation.confidence, 0.35)
    # A riskier business earns a wider discount before it is worth buying.
    margin += clamp((risk - 50.0) / 100.0 * 0.15, -0.05, 0.15)
    margin = clamp(margin, 0.10, 0.55)

    # The highest price at which buying this is still justified. Everything
    # else in the plan hangs off this number.
    plan.max_buy_price = iv * (1 - margin)

    band = 0.04
    if tech is not None and tech.atr_pct:
        # Widen the zone for volatile names so it is reachable intraday.
        band = clamp(tech.atr_pct * 1.5, 0.02, 0.10)

    if price <= plan.max_buy_price:
        # Already below the threshold: the zone sits around today's price,
        # because the answer is "buy now", not "buy anywhere up to fair value".
        # Stretching the zone up to fair entry would invite a purchase at a
        # price offering a fraction of the margin of safety the call assumed.
        plan.entry_low = price * (1 - band)
        plan.entry_high = min(price * (1 + band), plan.max_buy_price)
    else:
        # Above the threshold: wait. Anchor the zone on a real support level if
        # one sits near the threshold, so the target is a price the stock has
        # actually traded at rather than an arithmetic abstraction.
        supports = [
            s
            for s in (
                tech.sma_50 if tech else None,
                tech.sma_200 if tech else None,
                tech.week_52_low if tech else None,
            )
            if s is not None and s > 0
        ]
        nearby = [
            s
            for s in supports
            if plan.max_buy_price * 0.90 <= s <= plan.max_buy_price
        ]
        anchor = max(nearby) if nearby else plan.max_buy_price
        plan.entry_low = anchor * (1 - band)
        plan.entry_high = min(anchor * (1 + band), plan.max_buy_price)

    # Stop: ATR-based where volatility is known, otherwise a percentage floor.
    #
    # On a very volatile name 2.5 x ATR can exceed the price itself, which would
    # put the stop at or below zero. Clamping that to 0.01 prints a "stop loss"
    # of one paisa on screen — a number that looks deliberate and is worse than
    # useless. Where the ATR stop is not a sane fraction of the price, fall back
    # to a percentage stop instead.
    atr_stop = None
    if tech is not None and tech.atr_14:
        atr_stop = price - _STOP_ATR_MULTIPLE * tech.atr_14

    if atr_stop is not None and atr_stop > price * 0.50:
        plan.stop_loss = atr_stop
    else:
        # Either no ATR, or volatility so high the ATR stop is meaningless.
        plan.stop_loss = price * 0.85

    # Never stop out above the entry zone.
    if plan.entry_low and plan.stop_loss >= plan.entry_low:
        plan.stop_loss = plan.entry_low * 0.92

    # Targets: the bear case as a conservative first milestone, then fair value.
    #
    # The bear case is only a useful first target if it is far enough above the
    # price to be worth the risk taken to reach it. When it sits within noise of
    # the current price, using it produces a risk/reward below 1 next to a buy
    # rating — the plan contradicting the call. In that case skip straight to
    # fair value as the first target.
    bear = valuation.iv_bear
    if bear and bear > price * 1.08:
        plan.target_1 = bear
        plan.target_2 = iv
    else:
        plan.target_1 = iv
        plan.target_2 = valuation.iv_bull or iv

    if plan.target_2 and plan.target_1 and plan.target_2 < plan.target_1:
        plan.target_1, plan.target_2 = plan.target_2, plan.target_1

    reward = (plan.target_1 - price) if plan.target_1 else None
    downside = (price - plan.stop_loss) if plan.stop_loss else None
    # A negative ratio is not a worse trade, it is not a trade: the target sits
    # below the current price. Reporting "-0.53" invites it to be read as a
    # number on the same scale as a good setup's 3.0.
    if reward is not None and reward > 0 and downside and downside > 0:
        plan.risk_reward = reward / downside

    # Position sizing: scaled by conviction and inversely by risk, capped so no
    # single name can dominate a portfolio.
    base_size = {STRONG_BUY: 8.0, BUY: 6.0, ACCUMULATE: 4.0, HOLD: 0.0, REDUCE: 0.0}.get(
        action, 0.0
    )
    if base_size:
        plan.position_size_pct = clamp(base_size * (1.0 - risk / 200.0), 1.0, 10.0)

    plan.horizon = "3-5 years" if action in (STRONG_BUY, BUY, ACCUMULATE) else "Review quarterly"
    return plan


# ─────────────────────────────────────────────────────────────
# The decision
# ─────────────────────────────────────────────────────────────
def decide_action(
    value: float | None,
    quality: float | None,
    momentum: float | None,
    risk: float,
    valuation: ValuationResult,
    tech: TechnicalSnapshot | None,
) -> tuple[str, float, dict]:
    """
    Map the component scores onto an action.

    Deliberately gated rather than a single weighted sum crossing a threshold.
    A weighted average lets a spectacular value score drag a structurally broken
    business into a buy rating, which is exactly the failure mode that makes a
    screener dangerous. Here, a buy requires *every* gate to pass.
    """
    if value is None or valuation.iv_blended is None:
        return INSUFFICIENT_DATA, 0.0, {"reason": "no usable valuation"}

    upside = valuation.upside_pct or 0.0
    confidence = valuation.confidence
    trend = tech.trend if tech else "UNKNOWN"
    quality = quality if quality is not None else 50.0
    momentum = momentum if momentum is not None else 50.0

    gates = {
        "upside_over_25": upside >= 25.0,
        "upside_over_12": upside >= 12.0,
        "quality_over_55": quality >= 55.0,
        "quality_over_45": quality >= 45.0,
        "risk_under_55": risk < 55.0,
        "risk_under_70": risk < 70.0,
        "confidence_ok": confidence in ("HIGH", "MEDIUM"),
        "confidence_high": confidence == "HIGH",
        "not_collapsing": trend != "STRONG_DOWNTREND",
        "trend_constructive": trend in ("UPTREND", "STRONG_UPTREND", "SIDEWAYS"),
        "momentum_ok": momentum >= 45.0,
    }

    # ── Exit first: capital preservation outranks opportunity ─
    if risk >= 80.0:
        return AVOID, 0.85, gates
    if upside <= -25.0 and confidence in ("HIGH", "MEDIUM"):
        return SELL, 0.75, gates
    if upside <= -12.0 and trend in ("DOWNTREND", "STRONG_DOWNTREND"):
        return REDUCE, 0.65, gates
    if quality < 35.0 and upside < 10.0:
        return AVOID, 0.70, gates

    # ── Entry, strongest first ────────────────────────────────
    if (
        gates["upside_over_25"]
        and gates["quality_over_55"]
        and gates["risk_under_55"]
        and gates["confidence_high"]
        and gates["trend_constructive"]
        and gates["momentum_ok"]
    ):
        conviction = clamp(0.70 + upside / 400.0 + (quality - 55) / 300.0, 0.0, 0.98)
        return STRONG_BUY, conviction, gates

    if (
        gates["upside_over_25"]
        and gates["quality_over_45"]
        and gates["risk_under_70"]
        and gates["confidence_ok"]
        and gates["not_collapsing"]
    ):
        return BUY, clamp(0.55 + upside / 500.0, 0.0, 0.85), gates

    # Cheap and sound, but the market has not turned yet: wait for weakness
    # rather than buying into a falling trend.
    if (
        gates["upside_over_12"]
        and gates["quality_over_45"]
        and gates["risk_under_70"]
        and gates["confidence_ok"]
    ):
        return ACCUMULATE, clamp(0.40 + upside / 600.0, 0.0, 0.70), gates

    return HOLD, 0.35, gates


def _describe(
    action: str,
    valuation: ValuationResult,
    profile: HistoryProfile | None,
    tech: TechnicalSnapshot | None,
    value: float | None,
    quality: float | None,
    momentum: float | None,
    risk: float,
    risk_flags: Sequence[str],
) -> tuple[str, list[str], list[str]]:
    """Write the case in plain English, plus what would falsify it."""
    upside = valuation.upside_pct
    iv = valuation.iv_blended
    price = valuation.current_price

    rationale: list[str] = []
    invalidation: list[str] = []

    if iv and price:
        direction = "above" if iv > price else "below"
        rationale.append(
            f"Intrinsic value {iv:,.2f} is {abs(upside or 0):.0f}% {direction} "
            f"the current price of {price:,.2f}."
        )

    if profile is not None and profile.years:
        rationale.append(
            f"Valued on {profile.years} years of filings"
            + (
                f"; revenue compounded {profile.revenue_cagr*100:.1f}% a year"
                if profile.revenue_cagr is not None
                else ""
            )
            + (
                f" at a median {profile.operating_margin_median*100:.1f}% operating margin."
                if profile.operating_margin_median is not None
                else "."
            )
        )

    if profile is not None and profile.roic_median is not None:
        rationale.append(
            f"Return on invested capital has averaged {profile.roic_median*100:.1f}%."
        )

    if tech is not None and tech.trend != "UNKNOWN":
        # "SIDEWAYS" reads as "in a sideways" without the noun, so the label
        # supplies one. Small, but it is the sentence a user reads first.
        readable = {
            "STRONG_UPTREND": "strong uptrend",
            "UPTREND": "uptrend",
            "SIDEWAYS": "sideways range",
            "DOWNTREND": "downtrend",
            "STRONG_DOWNTREND": "strong downtrend",
        }.get(tech.trend, tech.trend.replace("_", " ").lower())
        detail = f"The stock is in a {readable}"
        if tech.rsi_14 is not None:
            detail += f" with RSI at {tech.rsi_14:.0f}"
        rationale.append(detail + ".")

    if valuation.confidence != "HIGH":
        rationale.append(
            f"Valuation confidence is {valuation.confidence.lower()}"
            + (f" — {valuation.warnings[0].lower()}." if valuation.warnings else ".")
        )

    if risk_flags:
        rationale.append("Risk flags: " + "; ".join(risk_flags[:3]) + ".")

    # What would change the view.
    if action in (STRONG_BUY, BUY, ACCUMULATE):
        if profile is not None and profile.revenue_cagr is not None:
            invalidation.append(
                f"Revenue growth falling durably below "
                f"{max(0.0, profile.revenue_cagr - 0.05)*100:.0f}%"
            )
        if profile is not None and profile.operating_margin_median is not None:
            invalidation.append(
                f"Operating margin compressing below "
                f"{max(0.0, profile.operating_margin_median - 0.03)*100:.1f}%"
            )
        invalidation.append("Price closing below the stop level on rising volume")
        invalidation.append("Net debt rising materially without matching returns")
    elif action in (SELL, REDUCE, AVOID):
        invalidation.append("Price falling back to or below intrinsic value")
        invalidation.append("A durable recovery in margins or return on capital")
    else:
        invalidation.append("Upside widening past 25% on unchanged fundamentals")
        invalidation.append("Deterioration in margins, cash generation or leverage")

    headlines = {
        STRONG_BUY: "Undervalued, high quality, and the market has turned",
        BUY: "Trading meaningfully below intrinsic value",
        ACCUMULATE: "Worth owning, but buy into weakness rather than chasing",
        HOLD: "Fairly priced — no edge either way at this level",
        REDUCE: "Overvalued and losing momentum",
        SELL: "Trading well above any defensible intrinsic value",
        AVOID: "Risk profile rules this out regardless of price",
        INSUFFICIENT_DATA: "Not enough reliable data to form a view",
    }
    return headlines.get(action, ""), rationale, invalidation


def generate_signal(
    symbol: str,
    valuation: ValuationResult,
    tech: TechnicalSnapshot | None,
    *,
    current_price: float | None = None,
) -> Signal:
    """
    Produce the full view for one stock.

    Returns an ``INSUFFICIENT_DATA`` signal rather than a guess whenever the
    valuation could not be computed. A grey light is a real answer; a yellow one
    invented to fill the cell is not.
    """
    price = current_price or valuation.current_price
    signal = Signal(symbol=symbol, current_price=price)

    if not valuation.ok:
        # A failed valuation has two very different causes, and collapsing them
        # into one grey light under-informs the user.
        #
        #   * Genuinely missing data — too few filings to say anything. Grey.
        #   * A business so distressed that every model returns negative equity.
        #     Here we have plenty of data and it all points one way. Telling
        #     someone "insufficient data" about a company we can demonstrate is
        #     distressed hides the finding behind a neutral colour.
        #
        # So where a profile exists and the measurable risk is severe, this is
        # reported as AVOID with the specific flags that earned it.
        risk, risk_flags = score_risk(valuation.history, tech, valuation)
        distressed = (
            valuation.history is not None
            and valuation.history.years >= 4
            and (risk >= 60.0 or len(risk_flags) >= 2)
        )

        if distressed:
            signal.action = AVOID
            signal.label = _ACTION_LABELS[AVOID]
            signal.color = _ACTION_COLORS[AVOID]
            signal.conviction = 0.70
            signal.risk_score = risk
            signal.risk_flags = risk_flags
            signal.composite_score = clamp(50.0 - (risk - 50.0), 0.0, 100.0)
            signal.quality_score = score_quality(valuation.history, None)
            signal.momentum_score = score_momentum(tech)
            signal.trend = tech.trend if tech else "UNKNOWN"
            signal.headline = "Financially distressed — no defensible value at any price"
            signal.rationale = [
                valuation.reason
                or "No valuation model produced a positive equity value",
                "Risk flags: " + "; ".join(risk_flags[:4]) + "."
                if risk_flags
                else "Measured risk is severe.",
            ]
            signal.invalidation = [
                "A return to sustained profitability",
                "Debt reduced to a level the business can service from cash flow",
            ]
            return signal

        signal.action = INSUFFICIENT_DATA
        signal.label = _ACTION_LABELS[INSUFFICIENT_DATA]
        signal.color = _ACTION_COLORS[INSUFFICIENT_DATA]
        signal.headline = "Not enough reliable data to form a view"
        signal.rationale = [valuation.reason or "Valuation could not be computed"]
        if risk_flags:
            signal.risk_flags = risk_flags
        return signal

    profile = valuation.history
    wacc = valuation.cost_of_capital.wacc if valuation.cost_of_capital else None

    value = score_value(valuation)
    quality = score_quality(profile, wacc)
    momentum = score_momentum(tech)
    risk, risk_flags = score_risk(profile, tech, valuation)

    action, conviction, gates = decide_action(
        value, quality, momentum, risk, valuation, tech
    )

    signal.action = action
    signal.label = _ACTION_LABELS[action]
    signal.color = _ACTION_COLORS[action]
    signal.conviction = conviction
    signal.value_score = value
    signal.quality_score = quality
    signal.momentum_score = momentum
    signal.risk_score = risk
    signal.risk_flags = risk_flags
    signal.conditions = gates
    signal.intrinsic_value = valuation.iv_blended
    signal.upside_pct = valuation.upside_pct
    signal.valuation_confidence = valuation.confidence
    signal.trend = tech.trend if tech else "UNKNOWN"

    components = [c for c in (value, quality, momentum) if c is not None]
    if components:
        # Risk enters as a deduction rather than a fourth average, so a genuinely
        # dangerous business cannot be averaged back into respectability.
        signal.composite_score = clamp(
            sum(components) / len(components) - (risk - 50.0) * 0.30, 0.0, 100.0
        )

    if price:
        signal.plan = build_trade_plan(action, price, valuation, tech, risk)

    headline, rationale, invalidation = _describe(
        action, valuation, profile, tech, value, quality, momentum, risk, risk_flags
    )
    signal.headline = headline
    signal.rationale = rationale
    signal.invalidation = invalidation
    return signal
