"""
StockLens ML Pipeline
Orchestrates: feature engineering → clustering → risk scoring → confidence scoring → fundamental scoring

Run manually:  python pipeline.py
Or via API:    POST /api/v1/ml/run-inference
"""
import asyncio
import logging
import os
import sys
import json
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import asyncpg
from dotenv import load_dotenv
from sklearn.preprocessing import RobustScaler
from sklearn.cluster import KMeans
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("ml_pipeline")

MODELS_DIR = Path(__file__).parent / "models"
MODELS_DIR.mkdir(exist_ok=True)

DB_URL = os.environ.get("DATABASE_SYNC_URL", "postgresql://postgres:password@localhost:5432/stocklens")


# ─────────────────────────────────────────────────────────────
# Feature Engineering
# ─────────────────────────────────────────────────────────────
FEATURE_COLS = [
    "pe", "pb", "ps", "ev_ebitda",
    "roe", "roce", "roa",
    "ebitda_margin", "net_margin",
    "revenue_cagr_3y", "pat_cagr_3y", "eps_cagr_3y",
    "debt_equity", "net_debt_equity", "interest_coverage",
    "cfo_pat", "fcf_margin",
    "dividend_yield",
    "data_completeness",
    "upside_pct", "margin_of_safety",
]

RISK_FEATURES = [
    "debt_equity", "net_debt_equity", "interest_coverage",
    "cfo_pat", "data_completeness", "debt_ebitda",
]

CONFIDENCE_FEATURES = [
    "data_completeness", "revenue_cagr_3y", "pat_cagr_3y",
    "ebitda_margin", "cfo_pat",
]

CLUSTER_LABELS = {
    0: "QUALITY_COMPOUNDER",
    1: "DEEP_VALUE",
    2: "GARP",
    3: "CYCLICAL_RECOVERY",
    4: "HIGH_GROWTH_EXPENSIVE",
    5: "VALUE_TRAP",
    6: "MOMENTUM_TRAP",
    7: "DISTRESSED",
    8: "LOW_LIQUIDITY",
}


def load_features(conn_url: str) -> pd.DataFrame:
    """Load combined ratios + intrinsic values from DB into a DataFrame."""
    import psycopg2
    import psycopg2.extras

    conn = psycopg2.connect(conn_url)
    query = """
    SELECT
        r.nse_symbol,
        r.pe, r.pb, r.ps, r.ev_ebitda, r.ev_sales,
        r.roe, r.roce, r.roa,
        r.ebitda_margin, r.net_margin, r.gross_margin,
        r.revenue_cagr_3y, r.revenue_cagr_5y,
        r.pat_cagr_3y, r.pat_cagr_5y,
        r.eps_cagr_3y, r.eps_cagr_5y,
        r.debt_equity, r.net_debt_equity, r.interest_coverage,
        r.debt_ebitda, r.cfo_pat, r.fcf_margin,
        r.current_ratio, r.quick_ratio,
        r.dividend_yield, r.dividend_payout,
        r.data_completeness,
        r.margin_expansion_3y,
        iv.upside_pct, iv.margin_of_safety, iv.iv_blended, iv.cmp,
        s.market_cap_category,
        sc.sector, sc.macro_sector
    FROM financial_ratios r
    LEFT JOIN intrinsic_values iv ON r.nse_symbol = iv.nse_symbol
    LEFT JOIN stocks s ON r.nse_symbol = s.nse_symbol
    LEFT JOIN sector_classification sc ON s.sector_id = sc.id
    WHERE s.is_active = TRUE
    ORDER BY r.as_of_date DESC
    """
    df = pd.read_sql(query, conn)
    conn.close()

    # Deduplicate — keep latest per symbol
    df = df.drop_duplicates(subset="nse_symbol", keep="first")
    df = df.set_index("nse_symbol")
    return df


def _clamp(val, lo, hi):
    return max(lo, min(hi, val))


# ─────────────────────────────────────────────────────────────
# Risk Score: 0=safe, 100=very risky
# ─────────────────────────────────────────────────────────────
def compute_risk_scores(df: pd.DataFrame) -> pd.Series:
    scores = pd.Series(50, index=df.index, name="risk_score")

    for sym in df.index:
        r = df.loc[sym]
        score = 50

        de = r.get("debt_equity", None)
        ic = r.get("interest_coverage", None)
        cfo_pat = r.get("cfo_pat", None)
        data_comp = r.get("data_completeness", 0.5)
        de_ebitda = r.get("debt_ebitda", None)
        fcf = r.get("fcf_margin", None)

        # Debt risk
        if de is not None:
            if de > 5: score += 25
            elif de > 3: score += 15
            elif de > 1.5: score += 8
            elif de < 0.3: score -= 10

        # Interest coverage
        if ic is not None:
            if ic < 1.5: score += 20
            elif ic < 3: score += 8
            elif ic > 10: score -= 10

        # Cash flow quality
        if cfo_pat is not None:
            if cfo_pat < 0.5: score += 15
            elif cfo_pat < 0.8: score += 5
            elif cfo_pat > 1.2: score -= 8

        # FCF
        if fcf is not None:
            if fcf < 0: score += 10
            elif fcf > 0.1: score -= 5

        # Data quality penalty
        if data_comp < 0.4: score += 20
        elif data_comp < 0.6: score += 10

        # Debt / EBITDA
        if de_ebitda is not None:
            if de_ebitda > 6: score += 15
            elif de_ebitda > 4: score += 8

        scores[sym] = _clamp(int(score), 0, 100)

    return scores


# ─────────────────────────────────────────────────────────────
# Valuation Confidence: 0=low, 100=high
# ─────────────────────────────────────────────────────────────
def compute_confidence_scores(df: pd.DataFrame) -> pd.Series:
    scores = pd.Series(50, index=df.index, name="confidence_score")

    for sym in df.index:
        r = df.loc[sym]
        score = 50
        data_comp = r.get("data_completeness", 0.5)
        rev_cagr = r.get("revenue_cagr_3y", None)
        pat_cagr = r.get("pat_cagr_3y", None)
        ebitda_m = r.get("ebitda_margin", None)
        cfo_pat = r.get("cfo_pat", None)
        iv = r.get("iv_blended", None)

        # Base from data completeness
        score = int(data_comp * 70)

        # Positive signals
        if rev_cagr is not None and rev_cagr > 0: score += 10
        if pat_cagr is not None and pat_cagr > 0: score += 10
        if ebitda_m is not None and ebitda_m > 0.1: score += 5
        if cfo_pat is not None and cfo_pat > 0.8: score += 8
        if iv is not None: score += 5

        # Negative signals
        if rev_cagr is not None and rev_cagr < 0: score -= 15
        if pat_cagr is not None and pat_cagr < -0.2: score -= 10

        scores[sym] = _clamp(int(score), 0, 100)

    return scores


# ─────────────────────────────────────────────────────────────
# Fundamental Score: 0-100
# ─────────────────────────────────────────────────────────────
def compute_fundamental_scores(df: pd.DataFrame) -> pd.Series:
    scores = pd.Series(0, index=df.index, name="fundamental_score")

    for sym in df.index:
        r = df.loc[sym]
        score = 0

        # Profitability (30 points)
        roe = r.get("roe", None)
        roce = r.get("roce", None)
        net_m = r.get("net_margin", None)
        if roe is not None: score += min(int(roe * 100), 15)
        if roce is not None: score += min(int(roce * 100), 15)

        # Growth (25 points)
        rev_c = r.get("revenue_cagr_3y", None)
        pat_c = r.get("pat_cagr_3y", None)
        if rev_c is not None and rev_c > 0: score += min(int(rev_c * 100), 12)
        if pat_c is not None and pat_c > 0: score += min(int(pat_c * 100), 13)

        # Balance sheet (25 points)
        de = r.get("debt_equity", None)
        ic = r.get("interest_coverage", None)
        cr = r.get("current_ratio", None)
        if de is not None: score += max(0, 10 - int(de * 2))
        if ic is not None: score += min(int(ic * 1.5), 10)
        if cr is not None and cr > 1.5: score += 5

        # Cash flow (20 points)
        cfo_pat = r.get("cfo_pat", None)
        fcf_m = r.get("fcf_margin", None)
        if cfo_pat is not None and cfo_pat > 0.8: score += 10
        if fcf_m is not None and fcf_m > 0.05: score += 10

        scores[sym] = _clamp(int(score), 0, 100)

    return scores


# ─────────────────────────────────────────────────────────────
# Growth Outlook Score
# ─────────────────────────────────────────────────────────────
def compute_growth_scores(df: pd.DataFrame) -> pd.Series:
    scores = pd.Series(50, index=df.index, name="growth_score")
    for sym in df.index:
        r = df.loc[sym]
        score = 40
        rev5 = r.get("revenue_cagr_5y", None)
        pat5 = r.get("pat_cagr_5y", None)
        eps5 = r.get("eps_cagr_5y", None)
        mex = r.get("margin_expansion_3y", None)
        if rev5 is not None and rev5 > 0: score += min(int(rev5 * 150), 20)
        if pat5 is not None and pat5 > 0: score += min(int(pat5 * 120), 20)
        if eps5 is not None and eps5 > 0: score += min(int(eps5 * 100), 10)
        if mex is not None and mex > 0: score += 10
        if rev5 is not None and rev5 < -0.05: score -= 20
        scores[sym] = _clamp(int(score), 0, 100)
    return scores


# ─────────────────────────────────────────────────────────────
# KMeans Clustering
# ─────────────────────────────────────────────────────────────
def run_clustering(df: pd.DataFrame, n_clusters: int = 9) -> pd.Series:
    avail_cols = [c for c in FEATURE_COLS if c in df.columns]
    feat_df = df[avail_cols].copy()

    imputer = SimpleImputer(strategy="median")
    scaler = RobustScaler()

    feat_imputed = imputer.fit_transform(feat_df)
    feat_scaled = scaler.fit_transform(feat_imputed)

    kmeans = KMeans(n_clusters=n_clusters, random_state=42, n_init=10, max_iter=300)
    labels = kmeans.fit_predict(feat_scaled)

    cluster_series = pd.Series(labels, index=df.index, name="cluster_id")

    # Auto-label clusters by characteristics
    labeled = pd.Series(index=df.index, name="cluster_label", dtype=str)
    for cid in range(n_clusters):
        mask = cluster_series == cid
        group = df[mask]
        if len(group) == 0:
            continue
        median_upside = group.get("upside_pct", pd.Series(dtype=float)).median()
        median_roe = group.get("roe", pd.Series(dtype=float)).median()
        median_growth = group.get("revenue_cagr_3y", pd.Series(dtype=float)).median()
        median_de = group.get("debt_equity", pd.Series(dtype=float)).median()

        if median_roe is not None and not pd.isna(median_roe) and median_roe > 0.2 and median_growth > 0.12:
            label = "QUALITY_COMPOUNDER"
        elif median_upside is not None and not pd.isna(median_upside) and median_upside > 30:
            label = "DEEP_VALUE"
        elif median_de is not None and not pd.isna(median_de) and median_de > 3:
            label = "DISTRESSED"
        elif median_growth is not None and not pd.isna(median_growth) and median_growth > 0.18:
            label = "HIGH_GROWTH_EXPENSIVE"
        else:
            label = CLUSTER_LABELS.get(cid, "UNCATEGORIZED")

        labeled[mask] = label

    return labeled


# ─────────────────────────────────────────────────────────────
# Write to Database
# ─────────────────────────────────────────────────────────────
async def write_scores_to_db(scores_dict: dict):
    db_url = DB_URL.replace("postgresql+psycopg2://", "").replace("postgresql://", "postgresql://")
    conn = await asyncpg.connect(db_url)
    today = date.today()

    for sym, scores in scores_dict.items():
        try:
            risk_level = "LOW" if scores["risk_score"] <= 30 else "MEDIUM" if scores["risk_score"] <= 60 else "HIGH"
            conf_label = "HIGH" if scores["confidence_score"] >= 70 else "MEDIUM" if scores["confidence_score"] >= 40 else "LOW"

            await conn.execute("""
                INSERT INTO ml_scores (
                    nse_symbol, risk_score, risk_level, valuation_confidence_score,
                    valuation_confidence, fundamental_score, growth_outlook_score, updated_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, NOW())
                ON CONFLICT (nse_symbol) DO UPDATE SET
                    risk_score = EXCLUDED.risk_score,
                    risk_level = EXCLUDED.risk_level,
                    valuation_confidence_score = EXCLUDED.valuation_confidence_score,
                    valuation_confidence = EXCLUDED.valuation_confidence,
                    fundamental_score = EXCLUDED.fundamental_score,
                    growth_outlook_score = EXCLUDED.growth_outlook_score,
                    updated_at = NOW()
            """,
                sym,
                scores["risk_score"],
                risk_level,
                scores["confidence_score"],
                conf_label,
                scores["fundamental_score"],
                scores["growth_score"],
            )

            # Write cluster result
            if scores.get("cluster_label"):
                await conn.execute("""
                    INSERT INTO ml_cluster_results (nse_symbol, run_date, cluster_label, algorithm)
                    VALUES ($1, $2, $3, 'KMEANS')
                    ON CONFLICT (nse_symbol, run_date) DO UPDATE SET
                        cluster_label = EXCLUDED.cluster_label
                """, sym, today, scores["cluster_label"])

        except Exception as e:
            log.warning(f"DB write failed for {sym}: {e}")

    await conn.close()


# ─────────────────────────────────────────────────────────────
# Main Pipeline
# ─────────────────────────────────────────────────────────────
async def main():
    log.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    log.info("StockLens ML Pipeline Starting")
    log.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    try:
        log.info("Loading features from database...")
        df = load_features(DB_URL)
        log.info(f"Loaded {len(df)} stocks")
    except Exception as e:
        log.error(f"Feature loading failed: {e}")
        sys.exit(1)

    if len(df) == 0:
        log.error("No data loaded — run ratio engine first")
        sys.exit(1)

    # Run all scoring
    log.info("Computing risk scores...")
    risk_scores = compute_risk_scores(df)

    log.info("Computing confidence scores...")
    conf_scores = compute_confidence_scores(df)

    log.info("Computing fundamental scores...")
    fund_scores = compute_fundamental_scores(df)

    log.info("Computing growth scores...")
    growth_scores = compute_growth_scores(df)

    log.info(f"Running clustering (n={min(9, len(df))})...")
    if len(df) >= 9:
        cluster_labels = run_clustering(df)
    else:
        cluster_labels = pd.Series("UNCATEGORIZED", index=df.index)

    # Combine
    scores_dict = {}
    for sym in df.index:
        scores_dict[sym] = {
            "risk_score": int(risk_scores.get(sym, 50)),
            "confidence_score": int(conf_scores.get(sym, 50)),
            "fundamental_score": int(fund_scores.get(sym, 0)),
            "growth_score": int(growth_scores.get(sym, 50)),
            "cluster_label": cluster_labels.get(sym),
        }

    log.info(f"Writing {len(scores_dict)} records to database...")
    await write_scores_to_db(scores_dict)

    log.info("ML Pipeline complete!")
    log.info(f"  Risk avg: {risk_scores.mean():.1f}")
    log.info(f"  Confidence avg: {conf_scores.mean():.1f}")
    log.info(f"  Fundamental avg: {fund_scores.mean():.1f}")


if __name__ == "__main__":
    asyncio.run(main())
