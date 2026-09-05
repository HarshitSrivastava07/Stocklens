"""
Signal Engine
Converts scores + valuation output → Green/Yellow/Red/Grey signal.
Uses a multi-condition gating system — must pass ALL conditions for GREEN.

Signal Colors:
  GREEN  → Potentially Undervalued (safe label, not "Buy")
  YELLOW → Review Required
  RED    → Avoid / Overvalued
  GREY   → Insufficient Data
"""
import logging
from datetime import datetime, timezone

import structlog
from sqlalchemy import select

from dependencies.db import AsyncSessionLocal
from models.db_models import (
    Stock, IntrinsicValue, MLScores, FinancialRatio,
    Signal, RiskFlag, AuditLog
)

log = structlog.get_logger("signal_engine")


# ─────────────────────────────────────────────────────────────
# Signal Logic
# ─────────────────────────────────────────────────────────────
def _compute_signal(
    iv: IntrinsicValue | None,
    ml: MLScores | None,
    ratio: FinancialRatio | None,
    risk_flags: list,
) -> dict:
    """
    Returns signal dict with color, label, conditions, blocking_flags.
    """
    now = datetime.now(timezone.utc)

    # ── Not enough data → GREY ────────────────────────────────
    if not iv or not ml or iv.iv_blended is None:
        return {
            "signal": "INSUFFICIENT_DATA",
            "signal_color": "GREY",
            "signal_label": "Insufficient Data",
            "main_reason": "Valuation or ML scores not yet computed",
            "conditions": {},
            "blocking_flags": ["NO_VALUATION_DATA"],
            "data_freshness": "STALE",
        }

    upside = float(iv.upside_pct) if iv.upside_pct else 0
    mos = float(iv.margin_of_safety) if iv.margin_of_safety else 0
    risk = ml.risk_score or 50
    fund = ml.fundamental_score or 0
    growth = ml.growth_outlook_score or 0
    conf = ml.valuation_confidence or "LOW"

    # ── Blocking risk flags ───────────────────────────────────
    critical_flags = [f.flag_type for f in risk_flags if f.severity in ("CRITICAL", "HIGH") and f.is_active]

    conditions = {
        "upside_gte_15": upside >= 15,
        "mos_positive": mos > 0,
        "risk_score_acceptable": risk <= 60,
        "fundamental_ok": fund >= 45,
        "no_critical_flags": len(critical_flags) == 0,
        "valuation_confidence_ok": conf in ("HIGH", "MEDIUM"),
    }

    blocking = critical_flags.copy()

    # ── GREEN: All gate conditions met + strong upside ────────
    if (
        upside >= 25
        and mos >= 0.15
        and risk <= 45
        and fund >= 60
        and conf in ("HIGH", "MEDIUM")
        and not critical_flags
        and growth >= 50
    ):
        return {
            "signal": "POTENTIALLY_UNDERVALUED",
            "signal_color": "GREEN",
            "signal_label": "Potentially Undervalued",
            "main_reason": f"Upside {upside:.1f}%, MoS {mos*100:.1f}%, Risk {risk}/100",
            "conditions": conditions,
            "blocking_flags": blocking,
            "data_freshness": "FRESH",
        }

    # ── RED: Overvalued or high risk ──────────────────────────
    if (
        upside <= -10
        or risk >= 75
        or bool(critical_flags)
        or (fund < 30 and upside < 0)
    ):
        reasons = []
        if upside <= -10:
            reasons.append(f"Overvalued by {abs(upside):.1f}%")
        if risk >= 75:
            reasons.append(f"High risk score: {risk}")
        if critical_flags:
            reasons.append(f"Critical flags: {', '.join(critical_flags[:2])}")

        return {
            "signal": "AVOID_OVERVALUED",
            "signal_color": "RED",
            "signal_label": "Avoid / Overvalued",
            "main_reason": "; ".join(reasons),
            "conditions": conditions,
            "blocking_flags": blocking,
            "data_freshness": "FRESH",
        }

    # ── YELLOW: Middle ground / review ────────────────────────
    reasons = []
    if 0 <= upside < 25:
        reasons.append(f"Limited upside ({upside:.1f}%)")
    if 45 < risk <= 75:
        reasons.append(f"Moderate risk ({risk})")
    if conf == "LOW":
        reasons.append("Low valuation confidence")
    if fund < 45:
        reasons.append(f"Weak fundamentals ({fund}/100)")

    return {
        "signal": "REVIEW_REQUIRED",
        "signal_color": "YELLOW",
        "signal_label": "Review Required",
        "main_reason": "; ".join(reasons) if reasons else "Mixed signals — review manually",
        "conditions": conditions,
        "blocking_flags": blocking,
        "data_freshness": "FRESH",
    }


# ─────────────────────────────────────────────────────────────
# Main Signal Engine
# ─────────────────────────────────────────────────────────────
async def run_signal_engine():
    async with AsyncSessionLocal() as db:
        # BUG9 FIX: Eagerly load all needed relationships to prevent MissingGreenlet in async
        stocks_res = await db.execute(select(Stock).where(Stock.is_active == True))
        stocks = stocks_res.scalars().all()

        success, failed = 0, 0
        changed = 0

        for stock in stocks:
            try:
                sym = stock.nse_symbol

                iv_res = await db.execute(select(IntrinsicValue).where(IntrinsicValue.nse_symbol == sym))
                iv = iv_res.scalar_one_or_none()

                ml_res = await db.execute(select(MLScores).where(MLScores.nse_symbol == sym))
                ml = ml_res.scalar_one_or_none()

                ratio_res = await db.execute(select(FinancialRatio).where(FinancialRatio.nse_symbol == sym).order_by(FinancialRatio.as_of_date.desc()).limit(1))
                ratio = ratio_res.scalar_one_or_none()

                flags_res = await db.execute(select(RiskFlag).where(RiskFlag.nse_symbol == sym, RiskFlag.is_active == True))
                flags = flags_res.scalars().all()

                new_sig = _compute_signal(iv, ml, ratio, flags)

                # Upsert signal
                existing_res = await db.execute(select(Signal).where(Signal.nse_symbol == sym))
                existing = existing_res.scalar_one_or_none()

                if existing:
                    prev_color = existing.signal_color
                    if prev_color != new_sig["signal_color"]:
                        new_sig["prev_signal"] = existing.signal
                        new_sig["prev_signal_color"] = prev_color
                        new_sig["signal_changed_at"] = datetime.now(timezone.utc)
                        changed += 1
                    for k, v in new_sig.items():
                        if hasattr(existing, k):
                            setattr(existing, k, v)
                    existing.computed_at = datetime.now(timezone.utc)
                    existing.updated_at = datetime.now(timezone.utc)
                else:
                    sig_obj = Signal(nse_symbol=sym, **new_sig, computed_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc))
                    db.add(sig_obj)

                # Commit per stock so one bad row can't discard the whole batch.
                await db.commit()
                success += 1

            except Exception as e:
                # Without the rollback the session stays in a failed transaction
                # and every remaining stock dies with PendingRollbackError.
                await db.rollback()
                log.error(f"Signal failed for {stock.nse_symbol}: {e}")
                failed += 1

        log.info(f"Signal engine done: success={success}, changed={changed}, failed={failed}")

        audit = AuditLog(action="SIGNAL_ENGINE_RUN", details={"success": success, "changed": changed, "failed": failed})
        db.add(audit)
        await db.commit()
