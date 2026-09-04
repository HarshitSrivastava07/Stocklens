"""
AI Router — Gemini-powered summaries with caching and sanitization
All LLM calls are backend-only. API key never exposed to frontend.
"""
import hashlib
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone

import google.genai as genai
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from dependencies.db import get_db
from dependencies.redis import get_redis_client
from models.db_models import AIReport, Stock, IntrinsicValue, MLScores, Signal, RealtimeQuote
from config import settings

router = APIRouter()
log = logging.getLogger("ai_router")

# Configure Gemini — new google-genai SDK uses a Client instance, not genai.configure()
_gemini_client = genai.Client(api_key=settings.GEMINI_API_KEY) if settings.GEMINI_API_KEY else None

SYSTEM_PROMPT = """You are a financial research assistant for a personal stock analysis tool.
Your role is to summarize available data clearly and objectively.

CRITICAL RULES:
1. Use ONLY the data provided. Never invent numbers, dates, or facts.
2. If data is missing or unavailable, say "Data unavailable for this field."
3. Never say "Buy" or "Sell". Use research-neutral language.
4. "Potentially Undervalued" means the model suggests possible undervaluation based on assumptions.
5. Always add: "This is a research summary, not investment advice."
6. Be concise. Maximum 400 words.
7. Structure: Overview → Valuation → Key Strengths → Key Risks → Model Notes."""

DISCLAIMER = "⚠️ AI-generated research summary. Not investment advice. Based on available data and model assumptions. Verify independently before making any financial decisions."


def _sanitize_output(text: str) -> str:
    """Remove any HTML/script tags from AI output before sending to frontend."""
    text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<[^>]+>", "", text)
    return text.strip()


def _build_stock_context(stock, iv, ml, signal, quote) -> str:
    """Build structured data context for RAG prompt — no raw user input injected."""
    parts = []

    if stock:
        parts.append(f"Company: {stock.company_name} ({stock.nse_symbol})")
        parts.append(f"Exchange: {stock.exchange} | Country: {stock.country} | Currency: {stock.currency}")

    if quote and quote.data_source not in ("MOCK", "SEED") and quote.ltp and quote.ltp > 0:
        currency = stock.currency if stock else "INR"
        parts.append(f"""
Real-time Price & Market Data:
- Last Trade Price: {currency} {quote.ltp}
- Today's Open: {quote.open} | High: {quote.high} | Low: {quote.low} | Close: {quote.close}
- Daily Change: {quote.change_abs} ({quote.change_pct}%)
- Volume Traded: {quote.volume}
- 52-Week High: {quote.week_52_high} | 52-Week Low: {quote.week_52_low}
- Market Capitalization: {currency} {quote.market_cap}""")
    elif quote:
        parts.append("Real-time Price: UNAVAILABLE (data_source=MOCK — live market data not yet loaded)")

    if iv:
        upside_str = f"{iv.upside_pct:.1f}%" if iv.upside_pct is not None else "N/A"
        mos_str = f"{(iv.margin_of_safety or 0) * 100:.1f}%" if iv.margin_of_safety is not None else "N/A"
        parts.append(f"""
Valuation:
- Current Market Price: {stock.currency if stock else '₹'}{iv.cmp}
- Intrinsic Value (Blended): {stock.currency if stock else '₹'}{iv.iv_blended}
- Bear Case: {iv.iv_bear} | Base: {iv.iv_base} | Bull: {iv.iv_bull}
- Upside/Downside: {upside_str}
- Margin of Safety: {mos_str}
- Primary Model Used: {iv.primary_model}
- Valuation Confidence: {iv.valuation_confidence}""")

    if ml:
        parts.append(f"""
Scores:
- Fundamental Score: {ml.fundamental_score}/100
- Growth Outlook Score: {ml.growth_outlook_score}/100
- Risk Score: {ml.risk_score}/100 ({ml.risk_level})
- Top Risk Drivers: {', '.join(ml.risk_drivers[:3]) if ml.risk_drivers else 'None'}""")

    if signal:
        parts.append(f"""
Signal: {signal.signal_label} ({signal.signal_color})
Main Reason: {signal.main_reason or 'Not computed'}
Blocking Flags: {', '.join(signal.blocking_flags[:3]) if signal.blocking_flags else 'None'}""")

    return "\n".join(parts) if parts else "Insufficient data available."


# ─────────────────────────────────────────────────────────────
# GET /ai/summary/{symbol} — return cached summary
# ─────────────────────────────────────────────────────────────
@router.get("/summary/{symbol}")
async def get_ai_summary(
    symbol: str,
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis_client),
):
    symbol = symbol.upper().strip()

    # 1. Redis cache
    redis_key = f"ai_cache:{symbol}:STOCK_SUMMARY"
    cached = await redis.get(redis_key)
    if cached:
        return json.loads(cached)

    # 2. DB cache
    result = await db.execute(
        select(AIReport)
        .where(
            AIReport.nse_symbol == symbol,
            AIReport.report_type == "STOCK_SUMMARY",
        )
        .order_by(AIReport.generated_at.desc())
        .limit(1)
    )
    report = result.scalar_one_or_none()

    if report and report.expires_at and report.expires_at > datetime.now(timezone.utc):
        data = {
            "symbol": symbol,
            "content": report.content,
            "disclaimer": report.disclaimer,
            "generated_at": report.generated_at.isoformat(),
            "model_used": report.model_used,
            "source": "CACHE",
        }
        await redis.setex(redis_key, settings.REDIS_AI_CACHE_TTL, json.dumps(data))
        return data

    return {
        "symbol": symbol,
        "content": None,
        "message": "No AI summary generated yet. POST to /ai/generate/{symbol} to create one.",
        "source": "NONE",
    }


# ─────────────────────────────────────────────────────────────
# POST /ai/generate/{symbol} — generate new AI summary
# ─────────────────────────────────────────────────────────────
@router.post("/generate/{symbol}")
async def generate_ai_summary(
    symbol: str,
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis_client),
):
    if not settings.GEMINI_API_KEY:
        raise HTTPException(status_code=503, detail="AI not configured. Set GEMINI_API_KEY in .env")

    symbol = symbol.upper().strip()

    # Load stock data
    stock_res = await db.execute(select(Stock).where(Stock.nse_symbol == symbol))
    stock = stock_res.scalar_one_or_none()
    if not stock:
        raise HTTPException(status_code=404, detail=f"Stock {symbol} not found")

    iv_res = await db.execute(select(IntrinsicValue).where(IntrinsicValue.nse_symbol == symbol))
    iv = iv_res.scalar_one_or_none()

    ml_res = await db.execute(select(MLScores).where(MLScores.nse_symbol == symbol))
    ml = ml_res.scalar_one_or_none()

    sig_res = await db.execute(select(Signal).where(Signal.nse_symbol == symbol))
    signal = sig_res.scalar_one_or_none()

    quote_res = await db.execute(select(RealtimeQuote).where(RealtimeQuote.nse_symbol == symbol))
    quote = quote_res.scalar_one_or_none()

    # ── PRICE VALIDATION GUARD ──────────────────────────────────
    # Refuse AI analysis if price is mock/unavailable.
    # Requirement: AI must never use stale, simulated, or incorrect prices.
    if not quote or quote.data_source in ("MOCK", "SEED") or not quote.ltp or quote.ltp <= 0:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "Live market data unavailable",
                "reason": "AI analysis requires verified live market prices. Current price data is mock/unavailable.",
                "fix": "Run `python fetch_live_prices.py` to load real market prices, then retry.",
                "data_source": quote.data_source if quote else "NONE",
                "ltp": float(quote.ltp) if (quote and quote.ltp) else None,
            }
        )

    # Build context — NO raw user input in prompt
    context = _build_stock_context(stock, iv, ml, signal, quote)
    data_version = hashlib.md5(context.encode()).hexdigest()[:16]

    # Check if we already have a report for this data version
    existing = await db.execute(
        select(AIReport).where(
            AIReport.nse_symbol == symbol,
            AIReport.report_type == "STOCK_SUMMARY",
            AIReport.data_version == data_version,
        ).limit(1)
    )
    if existing.scalar_one_or_none():
        return {"symbol": symbol, "message": "Summary up to date", "data_version": data_version}

    # Generate with Gemini — new google-genai SDK uses client.models.generate_content()
    try:
        if not _gemini_client:
            raise HTTPException(status_code=503, detail="AI not configured. Set GEMINI_API_KEY in .env")

        response = _gemini_client.models.generate_content(
            model=settings.GEMINI_MODEL_FAST,
            contents=f"Provide a research summary for this stock based on the following data:\n\n{context}",
            config=genai.types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                max_output_tokens=settings.AI_MAX_OUTPUT_TOKENS,
                temperature=0.3,  # low temp for factual financial content
            ),
        )
        raw_content = response.text
        tokens_used = response.usage_metadata.total_token_count if response.usage_metadata else 0

    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Gemini API error for {symbol}: {e}")
        raise HTTPException(status_code=503, detail="AI service temporarily unavailable")

    # Sanitize output before storing/returning — treat LLM output as untrusted
    clean_content = _sanitize_output(raw_content)

    # Store in DB
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=settings.AI_CACHE_TTL)
    report = AIReport(
        nse_symbol=symbol,
        report_type="STOCK_SUMMARY",
        content=clean_content,
        sources=[{"type": "internal_data", "context_chars": len(context)}],
        model_used=settings.GEMINI_MODEL_FAST,
        tokens_used=tokens_used,
        data_version=data_version,
        disclaimer=DISCLAIMER,
        expires_at=expires_at,
    )
    db.add(report)
    await db.commit()

    # Cache in Redis
    redis_data = {
        "symbol": symbol,
        "content": clean_content,
        "disclaimer": DISCLAIMER,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model_used": settings.GEMINI_MODEL_FAST,
        "tokens_used": tokens_used,
        "source": "FRESH",
    }
    await redis.setex(
        f"ai_cache:{symbol}:STOCK_SUMMARY",
        settings.REDIS_AI_CACHE_TTL,
        json.dumps(redis_data),
    )

    log.info(f"AI summary generated: {symbol}, tokens={tokens_used}")
    return redis_data


# ─────────────────────────────────────────────────────────────
# GET /ai/sector-summary/{sector} — sector-level AI overview
# ─────────────────────────────────────────────────────────────
@router.get("/sector-summary/{sector}")
async def get_sector_ai_summary(
    sector: str,
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis_client),
):
    if not settings.GEMINI_API_KEY:
        raise HTTPException(status_code=503, detail="AI not configured. Set GEMINI_API_KEY in .env")

    sector = sector.strip()
    redis_key = f"ai_cache:sector:{sector}"
    cached = await redis.get(redis_key)
    if cached:
        return json.loads(cached)

    # Load sector stats from DB
    from models.db_models import SectorClassification, Signal, IntrinsicValue, MLScores
    from sqlalchemy import func, case as sa_case
    result = await db.execute(
        select(
            func.count(Signal.nse_symbol).label("total"),
            func.sum(sa_case((Signal.signal_color == "GREEN", 1), else_=0)).label("green"),
            func.sum(sa_case((Signal.signal_color == "RED", 1), else_=0)).label("red"),
            func.avg(IntrinsicValue.upside_pct).label("avg_upside"),
            func.avg(MLScores.risk_score).label("avg_risk"),
            func.avg(MLScores.fundamental_score).label("avg_fundamental"),
        )
        .join(IntrinsicValue, IntrinsicValue.nse_symbol == Signal.nse_symbol, isouter=True)
        .join(MLScores, MLScores.nse_symbol == Signal.nse_symbol, isouter=True)
        .join(__import__("models.db_models", fromlist=["Stock"]).Stock, isouter=True)
        .join(SectorClassification, SectorClassification.sector == sector, isouter=True)
    )
    row = result.one_or_none()

    context = f"""Sector: {sector}
Total stocks analyzed: {row.total if row else 'Unknown'}
Green signals: {row.green if row else 'Unknown'}
Red signals: {row.red if row else 'Unknown'}
Average modelled upside: {f"{float(row.avg_upside):.1f}%" if row and row.avg_upside else 'N/A'}
Average risk score: {f"{float(row.avg_risk):.0f}/100" if row and row.avg_risk else 'N/A'}
Average fundamental score: {f"{float(row.avg_fundamental):.0f}/100" if row and row.avg_fundamental else 'N/A'}"""

    sector_prompt = f"""Provide a brief sector research overview for: {sector}
Based on this aggregate data from my valuation models:

{context}

Cover: sector characteristics, current signal distribution, key risks for this sector.
Maximum 250 words. Research only, not investment advice."""

    try:
        model = genai.GenerativeModel(
            model_name=settings.GEMINI_MODEL_FAST,
            generation_config=genai.types.GenerationConfig(max_output_tokens=400, temperature=0.3),
            system_instruction=SYSTEM_PROMPT,
        )
        response = model.generate_content(sector_prompt)
        content = _sanitize_output(response.text)
    except Exception as e:
        log.error(f"Gemini sector summary error for {sector}: {e}")
        raise HTTPException(status_code=503, detail="AI service temporarily unavailable")

    data = {
        "sector": sector,
        "content": content,
        "disclaimer": DISCLAIMER,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "FRESH",
    }
    await redis.setex(redis_key, settings.REDIS_AI_CACHE_TTL, json.dumps(data))
    return data


# ─────────────────────────────────────────────────────────────
# POST /ai/dcf-explain/{symbol} — explain DCF assumptions in plain English
# ─────────────────────────────────────────────────────────────
@router.post("/dcf-explain/{symbol}")
async def dcf_explain(
    symbol: str,
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis_client),
):
    if not settings.GEMINI_API_KEY:
        raise HTTPException(status_code=503, detail="AI not configured. Set GEMINI_API_KEY in .env")

    symbol = symbol.upper().strip()
    redis_key = f"ai_cache:{symbol}:DCF_EXPLAIN"
    cached = await redis.get(redis_key)
    if cached:
        return json.loads(cached)

    # Load intrinsic value + valuation run details
    from models.db_models import IntrinsicValue, ValuationRun, ValuationAssumption
    iv_res = await db.execute(select(IntrinsicValue).where(IntrinsicValue.nse_symbol == symbol))
    iv = iv_res.scalar_one_or_none()
    if not iv:
        raise HTTPException(status_code=404, detail=f"No valuation data for {symbol}. Run the valuation engine first.")

    context = f"""DCF Valuation for {symbol}:
- Primary Model: {iv.primary_model}
- Bear Case IV: ₹{iv.iv_bear}
- Base Case IV: ₹{iv.iv_base}
- Bull Case IV: ₹{iv.iv_bull}
- Blended IV (25/50/25): ₹{iv.iv_blended}
- Current Price: ₹{iv.cmp}
- Upside/Downside: {iv.upside_pct:.1f}%
- Margin of Safety: {(iv.margin_of_safety or 0)*100:.1f}%
- Valuation Confidence: {iv.valuation_confidence}
- Last Run: {iv.updated_at.date() if iv.updated_at else 'Unknown'}"""

    prompt = f"""Explain the DCF valuation for {symbol} in plain, simple English.

Data:
{context}

Explain:
1. What the bear/base/bull cases mean and what assumptions drive each
2. What the blended intrinsic value represents
3. What the margin of safety means
4. Why the confidence level is {iv.valuation_confidence}
5. Important limitations of this model

Keep it clear, factual, 300 words max. No buy/sell language."""

    try:
        model = genai.GenerativeModel(
            model_name=settings.GEMINI_MODEL_FAST,
            generation_config=genai.types.GenerationConfig(max_output_tokens=500, temperature=0.2),
            system_instruction=SYSTEM_PROMPT,
        )
        response = model.generate_content(prompt)
        content = _sanitize_output(response.text)
    except Exception as e:
        log.error(f"Gemini DCF explain error for {symbol}: {e}")
        raise HTTPException(status_code=503, detail="AI service temporarily unavailable")

    data = {
        "symbol": symbol,
        "content": content,
        "disclaimer": DISCLAIMER,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": iv.primary_model,
        "source": "FRESH",
    }
    await redis.setex(redis_key, settings.REDIS_AI_CACHE_TTL, json.dumps(data))
    log.info(f"DCF explanation generated: {symbol}")
    return data
