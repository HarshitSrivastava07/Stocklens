"""
Advanced ML Models — XGBoost Risk Scorer + LightGBM Confidence Scorer
Trains on historical ratio + signal data to predict outcomes.

Usage:
  python apps/ml/train_models.py          # full training
  python apps/ml/train_models.py --eval   # evaluation only

Models saved to: apps/ml/models/
"""
import argparse
import logging
import os
import sys
import json
import pickle
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2
from dotenv import load_dotenv
from sklearn.model_selection import train_test_split, StratifiedKFold
from sklearn.metrics import roc_auc_score, mean_absolute_error, classification_report
from sklearn.preprocessing import RobustScaler
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("train_models")

DB_URL = os.environ.get("DATABASE_SYNC_URL", "postgresql://postgres:password@localhost:5432/stocklens")
MODELS_DIR = Path(__file__).parent / "models"
MODELS_DIR.mkdir(exist_ok=True)

# ── Feature sets ──────────────────────────────────────────────
RISK_FEATURES = [
    "debt_equity", "net_debt_equity", "interest_coverage", "debt_ebitda",
    "cfo_pat", "fcf_margin", "current_ratio", "quick_ratio",
    "roe", "roce", "net_margin", "ebitda_margin",
    "data_completeness", "revenue_cagr_3y", "pat_cagr_3y",
]

CONFIDENCE_FEATURES = [
    "data_completeness", "revenue_cagr_3y", "pat_cagr_3y",
    "eps_cagr_3y", "ebitda_margin", "cfo_pat", "fcf_margin",
    "roe", "roce", "net_margin", "revenue_growth_1y",
]

FUNDAMENTAL_FEATURES = [
    "roe", "roce", "roa", "ebitda_margin", "net_margin",
    "revenue_cagr_3y", "pat_cagr_3y", "eps_cagr_3y",
    "debt_equity", "interest_coverage", "cfo_pat", "fcf_margin",
    "revenue_growth_1y", "margin_expansion_3y", "data_completeness",
]


def load_training_data() -> pd.DataFrame:
    """Load features + historical outcomes from DB."""
    conn = psycopg2.connect(DB_URL)
    query = """
    SELECT
        r.nse_symbol,
        r.pe, r.pb, r.ps, r.ev_ebitda, r.peg,
        r.roe, r.roce, r.roa,
        r.ebitda_margin, r.net_margin, r.operating_margin,
        r.revenue_growth_1y, r.revenue_cagr_3y, r.revenue_cagr_5y,
        r.pat_growth_1y, r.pat_cagr_3y, r.pat_cagr_5y,
        r.eps_cagr_3y, r.margin_expansion_3y,
        r.debt_equity, r.net_debt_equity, r.interest_coverage, r.debt_ebitda,
        r.cfo_pat, r.fcf_margin, r.current_ratio, r.quick_ratio,
        r.dividend_yield, r.data_completeness,
        iv.upside_pct, iv.margin_of_safety,
        sig.signal_color,
        ml.risk_score as existing_risk,
        ml.fundamental_score as existing_fund
    FROM financial_ratios r
    LEFT JOIN intrinsic_values iv ON r.nse_symbol = iv.nse_symbol
    LEFT JOIN signals sig ON r.nse_symbol = sig.nse_symbol
    LEFT JOIN ml_scores ml ON r.nse_symbol = ml.nse_symbol
    WHERE r.data_completeness >= 0.4
    """
    df = pd.read_sql(query, conn)
    conn.close()
    df = df.drop_duplicates("nse_symbol")
    log.info(f"Loaded {len(df)} training samples")
    return df


def build_risk_labels(df: pd.DataFrame) -> pd.Series:
    """
    Create risk labels from observable data:
    HIGH (1) = D/E > 2 OR ICR < 2 OR CFO/PAT < 0.5
    LOW  (0) = D/E < 0.5 AND ICR > 5 AND CFO/PAT > 1
    MEDIUM = everything else → we use multi-class
    Simplified to binary for XGBoost demo.
    """
    high_risk = (
        (df["debt_equity"].fillna(0) > 2) |
        (df["interest_coverage"].fillna(999) < 2) |
        (df["cfo_pat"].fillna(1) < 0.5)
    )
    return high_risk.astype(int)


def build_confidence_labels(df: pd.DataFrame) -> pd.Series:
    """Confidence = data quality × cashflow quality × growth consistency."""
    score = (
        df["data_completeness"].fillna(0.3) * 50 +
        df["cfo_pat"].fillna(0).clip(0, 2) * 15 +
        (df["revenue_cagr_3y"].fillna(0) > 0.1).astype(float) * 20 +
        (df["pat_cagr_3y"].fillna(0) > 0.08).astype(float) * 15
    )
    return (score >= 60).astype(int)


def train_xgboost_risk(df: pd.DataFrame):
    try:
        import xgboost as xgb
    except ImportError:
        log.warning("XGBoost not installed. Run: pip install xgboost")
        return None

    feats = [f for f in RISK_FEATURES if f in df.columns]
    X = df[feats]
    y = build_risk_labels(df)

    if len(y.unique()) < 2:
        log.warning("Not enough class diversity for XGBoost training")
        return None

    pipeline = Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("scaler", RobustScaler()),
        ("model", xgb.XGBClassifier(
            n_estimators=200,
            max_depth=4,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            use_label_encoder=False,
            eval_metric="logloss",
            random_state=42,
            n_jobs=-1,
        )),
    ])

    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42, stratify=y)
    pipeline.fit(X_train, y_train)

    proba = pipeline.predict_proba(X_test)[:, 1]
    try:
        auc = roc_auc_score(y_test, proba)
        log.info(f"XGBoost Risk Model AUC: {auc:.4f}")
    except Exception:
        pass

    path = MODELS_DIR / "xgboost_risk.pkl"
    with open(path, "wb") as f:
        pickle.dump({"pipeline": pipeline, "features": feats, "trained_at": date.today().isoformat()}, f)
    log.info(f"XGBoost risk model saved: {path}")
    return pipeline


def train_lightgbm_confidence(df: pd.DataFrame):
    try:
        import lightgbm as lgb
    except ImportError:
        log.warning("LightGBM not installed. Run: pip install lightgbm")
        return None

    feats = [f for f in CONFIDENCE_FEATURES if f in df.columns]
    X = df[feats]
    y = build_confidence_labels(df)

    if len(y.unique()) < 2:
        log.warning("Not enough class diversity for LightGBM training")
        return None

    pipeline = Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("scaler", RobustScaler()),
        ("model", lgb.LGBMClassifier(
            n_estimators=300,
            max_depth=5,
            learning_rate=0.03,
            num_leaves=31,
            subsample=0.8,
            colsample_bytree=0.8,
            random_state=42,
            verbose=-1,
        )),
    ])

    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)
    pipeline.fit(X_train, y_train)

    proba = pipeline.predict_proba(X_test)[:, 1]
    try:
        auc = roc_auc_score(y_test, proba)
        log.info(f"LightGBM Confidence Model AUC: {auc:.4f}")
    except Exception:
        pass

    path = MODELS_DIR / "lgbm_confidence.pkl"
    with open(path, "wb") as f:
        pickle.dump({"pipeline": pipeline, "features": feats, "trained_at": date.today().isoformat()}, f)
    log.info(f"LightGBM confidence model saved: {path}")
    return pipeline


def predict_with_models(df: pd.DataFrame) -> pd.DataFrame:
    """Run predictions using saved models. Falls back to rule-based if models not found."""
    risk_model_path = MODELS_DIR / "xgboost_risk.pkl"
    conf_model_path = MODELS_DIR / "lgbm_confidence.pkl"

    results = pd.DataFrame(index=df.index)

    if risk_model_path.exists():
        with open(risk_model_path, "rb") as f:
            risk_model = pickle.load(f)
        pipeline = risk_model["pipeline"]
        feats = [f for f in risk_model["features"] if f in df.columns]
        X = df[feats]
        proba = pipeline.predict_proba(X)[:, 1]
        results["risk_score_ml"] = (proba * 100).astype(int).clip(0, 100)
        log.info("XGBoost risk predictions applied")
    else:
        log.warning("XGBoost model not found — using rule-based risk scores")
        results["risk_score_ml"] = None

    if conf_model_path.exists():
        with open(conf_model_path, "rb") as f:
            conf_model = pickle.load(f)
        pipeline = conf_model["pipeline"]
        feats = [f for f in conf_model["features"] if f in df.columns]
        X = df[feats]
        proba = pipeline.predict_proba(X)[:, 1]
        results["confidence_score_ml"] = (proba * 100).astype(int).clip(0, 100)
        log.info("LightGBM confidence predictions applied")
    else:
        log.warning("LightGBM model not found — using rule-based confidence scores")
        results["confidence_score_ml"] = None

    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--eval", action="store_true", help="Evaluation only, no training")
    args = parser.parse_args()

    log.info("Loading training data...")
    df = load_training_data()

    if len(df) < 30:
        log.warning(f"Only {len(df)} samples — need at least 30 for ML training. Using rule-based scoring.")
        log.info("Add financial data via parse_financials.py first, then retrain.")
        sys.exit(0)

    if not args.eval:
        log.info("Training XGBoost Risk Model...")
        train_xgboost_risk(df)

        log.info("Training LightGBM Confidence Model...")
        train_lightgbm_confidence(df)

    log.info("Running predictions on full dataset...")
    preds = predict_with_models(df)
    log.info(f"Predictions complete for {len(preds)} stocks")

    # Save model metadata
    metadata = {
        "trained_at": date.today().isoformat(),
        "sample_count": len(df),
        "risk_model": "xgboost" if (MODELS_DIR / "xgboost_risk.pkl").exists() else "rule_based",
        "confidence_model": "lightgbm" if (MODELS_DIR / "lgbm_confidence.pkl").exists() else "rule_based",
    }
    with open(MODELS_DIR / "metadata.json", "w") as f:
        json.dump(metadata, f, indent=2)

    log.info("Done! Models available at apps/ml/models/")


if __name__ == "__main__":
    main()
