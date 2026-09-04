"""
StockLens — SQLAlchemy ORM Models
All tables mirror the PostgreSQL schema in 001_initial.sql
"""
from datetime import datetime, date
from typing import Optional, List
from sqlalchemy import (
    String, Integer, Numeric, Boolean, Text, BigInteger,
    Date, DateTime, ForeignKey, UniqueConstraint, Index,
    JSON, Enum as SAEnum
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func
import uuid

from dependencies.db import Base


# ── Sector Classification ─────────────────────────────────────
class SectorClassification(Base):
    __tablename__ = "sector_classification"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    macro_sector: Mapped[str] = mapped_column(String(100), nullable=False)
    sector: Mapped[str] = mapped_column(String(100), nullable=False)
    industry: Mapped[Optional[str]] = mapped_column(String(100))
    basic_industry: Mapped[Optional[str]] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    stocks: Mapped[List["Stock"]] = relationship(back_populates="sector")


# ── Stock Universe ────────────────────────────────────────────
class Stock(Base):
    __tablename__ = "stocks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    nse_symbol: Mapped[str] = mapped_column(String(30), unique=True, nullable=False, index=True)
    bse_code: Mapped[Optional[str]] = mapped_column(String(10))
    isin: Mapped[Optional[str]] = mapped_column(String(12))
    company_name: Mapped[str] = mapped_column(String(200), nullable=False)
    short_name: Mapped[Optional[str]] = mapped_column(String(50))
    listing_status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    instrument_type: Mapped[str] = mapped_column(String(20), default="EQ")
    market_cap_category: Mapped[Optional[str]] = mapped_column(String(10))
    face_value: Mapped[Optional[float]] = mapped_column(Numeric(10, 2))
    lot_size: Mapped[int] = mapped_column(Integer, default=1)
    sector_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("sector_classification.id"))
    is_nifty50: Mapped[bool] = mapped_column(Boolean, default=False)
    is_nifty500: Mapped[bool] = mapped_column(Boolean, default=False)
    is_fno: Mapped[bool] = mapped_column(Boolean, default=False)
    data_source: Mapped[str] = mapped_column(String(50), default="NSE_BHAVCOPY")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    # ── Global exchange fields ───────────────────────────────────
    exchange: Mapped[str] = mapped_column(String(20), default="NSE", index=True)
    currency: Mapped[str] = mapped_column(String(5), default="INR")
    yahoo_ticker: Mapped[Optional[str]] = mapped_column(String(30), index=True)
    country: Mapped[str] = mapped_column(String(50), default="India", index=True)
    # ────────────────────────────────────────────────────────────
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    sector: Mapped[Optional[SectorClassification]] = relationship(back_populates="stocks")
    realtime_quote: Mapped[Optional["RealtimeQuote"]] = relationship(back_populates="stock", uselist=False)
    financial_results: Mapped[List["FinancialResult"]] = relationship(back_populates="stock")
    financial_ratios: Mapped[List["FinancialRatio"]] = relationship(back_populates="stock")
    valuation_runs: Mapped[List["ValuationRun"]] = relationship(back_populates="stock")
    intrinsic_value: Mapped[Optional["IntrinsicValue"]] = relationship(back_populates="stock", uselist=False)
    ml_scores: Mapped[Optional["MLScores"]] = relationship(back_populates="stock", uselist=False)
    signal: Mapped[Optional["Signal"]] = relationship(back_populates="stock", uselist=False)
    risk_flags: Mapped[List["RiskFlag"]] = relationship(back_populates="stock")
    watchlist: Mapped[Optional["Watchlist"]] = relationship(back_populates="stock", uselist=False)


# ── Real-Time Quote ───────────────────────────────────────────
class RealtimeQuote(Base):
    __tablename__ = "realtime_quotes"

    nse_symbol: Mapped[str] = mapped_column(String(30), ForeignKey("stocks.nse_symbol"), primary_key=True)
    ltp: Mapped[Optional[float]] = mapped_column(Numeric(12, 2))
    open: Mapped[Optional[float]] = mapped_column(Numeric(12, 2))
    high: Mapped[Optional[float]] = mapped_column(Numeric(12, 2))
    low: Mapped[Optional[float]] = mapped_column(Numeric(12, 2))
    close: Mapped[Optional[float]] = mapped_column(Numeric(12, 2))
    volume: Mapped[Optional[int]] = mapped_column(BigInteger)
    value: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    bid: Mapped[Optional[float]] = mapped_column(Numeric(12, 2))
    ask: Mapped[Optional[float]] = mapped_column(Numeric(12, 2))
    change_abs: Mapped[Optional[float]] = mapped_column(Numeric(10, 2))
    change_pct: Mapped[Optional[float]] = mapped_column(Numeric(8, 4))
    week_52_high: Mapped[Optional[float]] = mapped_column(Numeric(12, 2))
    week_52_low: Mapped[Optional[float]] = mapped_column(Numeric(12, 2))
    market_cap: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    data_source: Mapped[str] = mapped_column(String(30), default="UPSTOX")
    is_stale: Mapped[bool] = mapped_column(Boolean, default=False)
    last_updated: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    stock: Mapped[Stock] = relationship(back_populates="realtime_quote")


# ── Financial Results ─────────────────────────────────────────
class FinancialResult(Base):
    __tablename__ = "financial_results"
    __table_args__ = (UniqueConstraint("nse_symbol", "period_type", "period_end"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    nse_symbol: Mapped[str] = mapped_column(String(30), ForeignKey("stocks.nse_symbol"), nullable=False, index=True)
    period_type: Mapped[str] = mapped_column(String(10), nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    revenue: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    revenue_growth_yoy: Mapped[Optional[float]] = mapped_column(Numeric(8, 4))
    gross_profit: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    ebitda: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    ebit: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    pbt: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    tax: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    pat: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    pat_growth_yoy: Mapped[Optional[float]] = mapped_column(Numeric(8, 4))
    eps: Mapped[Optional[float]] = mapped_column(Numeric(12, 4))
    eps_diluted: Mapped[Optional[float]] = mapped_column(Numeric(12, 4))
    exceptional_items: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    total_assets: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    total_liabilities: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    net_worth: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    equity_capital: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    reserves: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    total_debt: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    long_term_debt: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    short_term_debt: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    cash_and_equiv: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    investments: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    fixed_assets: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    goodwill: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    cfo: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    cfi: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    cff: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    capex: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    free_cash_flow: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    dividends_paid: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    shares_outstanding: Mapped[Optional[int]] = mapped_column(BigInteger)
    book_value_per_share: Mapped[Optional[float]] = mapped_column(Numeric(12, 4))
    net_interest_income: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    nim: Mapped[Optional[float]] = mapped_column(Numeric(6, 4))
    gnpa_pct: Mapped[Optional[float]] = mapped_column(Numeric(6, 4))
    nnpa_pct: Mapped[Optional[float]] = mapped_column(Numeric(6, 4))
    credit_cost: Mapped[Optional[float]] = mapped_column(Numeric(6, 4))
    roe_bank: Mapped[Optional[float]] = mapped_column(Numeric(6, 4))
    deal_wins_usd: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    attrition_pct: Mapped[Optional[float]] = mapped_column(Numeric(6, 4))
    data_source: Mapped[str] = mapped_column(String(50), default="BSE_XML")
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    confidence_level: Mapped[str] = mapped_column(String(10), default="LOW")
    has_exceptional: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    stock: Mapped[Stock] = relationship(back_populates="financial_results")


# ── Financial Ratios ──────────────────────────────────────────
class FinancialRatio(Base):
    __tablename__ = "financial_ratios"
    __table_args__ = (UniqueConstraint("nse_symbol", "as_of_date"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    nse_symbol: Mapped[str] = mapped_column(String(30), ForeignKey("stocks.nse_symbol"), nullable=False, index=True)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    # Valuation
    pe: Mapped[Optional[float]] = mapped_column(Numeric(10, 4))
    pb: Mapped[Optional[float]] = mapped_column(Numeric(10, 4))
    ps: Mapped[Optional[float]] = mapped_column(Numeric(10, 4))
    ev_ebitda: Mapped[Optional[float]] = mapped_column(Numeric(10, 4))
    ev_sales: Mapped[Optional[float]] = mapped_column(Numeric(10, 4))
    peg: Mapped[Optional[float]] = mapped_column(Numeric(10, 4))
    market_cap: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    enterprise_value: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    # Profitability
    roe: Mapped[Optional[float]] = mapped_column(Numeric(8, 4))
    roce: Mapped[Optional[float]] = mapped_column(Numeric(8, 4))
    roa: Mapped[Optional[float]] = mapped_column(Numeric(8, 4))
    roic: Mapped[Optional[float]] = mapped_column(Numeric(8, 4))
    gross_margin: Mapped[Optional[float]] = mapped_column(Numeric(8, 4))
    ebitda_margin: Mapped[Optional[float]] = mapped_column(Numeric(8, 4))
    operating_margin: Mapped[Optional[float]] = mapped_column(Numeric(8, 4))
    net_margin: Mapped[Optional[float]] = mapped_column(Numeric(8, 4))
    # Growth
    revenue_growth_1y: Mapped[Optional[float]] = mapped_column(Numeric(8, 4))
    revenue_cagr_3y: Mapped[Optional[float]] = mapped_column(Numeric(8, 4))
    revenue_cagr_5y: Mapped[Optional[float]] = mapped_column(Numeric(8, 4))
    pat_growth_1y: Mapped[Optional[float]] = mapped_column(Numeric(8, 4))
    pat_cagr_3y: Mapped[Optional[float]] = mapped_column(Numeric(8, 4))
    pat_cagr_5y: Mapped[Optional[float]] = mapped_column(Numeric(8, 4))
    eps_cagr_3y: Mapped[Optional[float]] = mapped_column(Numeric(8, 4))
    eps_cagr_5y: Mapped[Optional[float]] = mapped_column(Numeric(8, 4))
    margin_expansion_3y: Mapped[Optional[float]] = mapped_column(Numeric(8, 4))
    # Leverage
    debt_equity: Mapped[Optional[float]] = mapped_column(Numeric(10, 4))
    debt_ebitda: Mapped[Optional[float]] = mapped_column(Numeric(10, 4))
    interest_coverage: Mapped[Optional[float]] = mapped_column(Numeric(10, 4))
    net_debt_equity: Mapped[Optional[float]] = mapped_column(Numeric(10, 4))
    # Liquidity
    current_ratio: Mapped[Optional[float]] = mapped_column(Numeric(10, 4))
    quick_ratio: Mapped[Optional[float]] = mapped_column(Numeric(10, 4))
    cfo_pat: Mapped[Optional[float]] = mapped_column(Numeric(10, 4))
    fcf_margin: Mapped[Optional[float]] = mapped_column(Numeric(8, 4))
    asset_turnover: Mapped[Optional[float]] = mapped_column(Numeric(10, 4))
    receivable_days: Mapped[Optional[float]] = mapped_column(Numeric(8, 2))
    inventory_days: Mapped[Optional[float]] = mapped_column(Numeric(8, 2))
    payable_days: Mapped[Optional[float]] = mapped_column(Numeric(8, 2))
    # Dividend
    dividend_yield: Mapped[Optional[float]] = mapped_column(Numeric(8, 4))
    dividend_payout: Mapped[Optional[float]] = mapped_column(Numeric(8, 4))
    # Quality
    data_completeness: Mapped[Optional[float]] = mapped_column(Numeric(5, 4))
    computed_ok: Mapped[bool] = mapped_column(Boolean, default=True)
    error_fields: Mapped[dict] = mapped_column(JSONB, default=list)

    stock: Mapped[Stock] = relationship(back_populates="financial_ratios")


# ── Valuation Run ─────────────────────────────────────────────
class ValuationRun(Base):
    __tablename__ = "valuation_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    nse_symbol: Mapped[str] = mapped_column(String(30), ForeignKey("stocks.nse_symbol"), nullable=False, index=True)
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    triggered_by: Mapped[str] = mapped_column(String(30), default="SCHEDULER")
    models_used: Mapped[list] = mapped_column(JSONB, default=list)
    iv_bear: Mapped[Optional[float]] = mapped_column(Numeric(12, 2))
    iv_base: Mapped[Optional[float]] = mapped_column(Numeric(12, 2))
    iv_bull: Mapped[Optional[float]] = mapped_column(Numeric(12, 2))
    iv_blended: Mapped[Optional[float]] = mapped_column(Numeric(12, 2))
    cmp: Mapped[Optional[float]] = mapped_column(Numeric(12, 2))
    upside_pct: Mapped[Optional[float]] = mapped_column(Numeric(8, 4))
    margin_of_safety: Mapped[Optional[float]] = mapped_column(Numeric(8, 4))
    sector_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("sector_classification.id"))
    primary_model: Mapped[Optional[str]] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(20), default="SUCCESS")
    error_message: Mapped[Optional[str]] = mapped_column(Text)

    stock: Mapped[Stock] = relationship(back_populates="valuation_runs")


# ── Intrinsic Value (latest) ──────────────────────────────────
class IntrinsicValue(Base):
    __tablename__ = "intrinsic_values"

    nse_symbol: Mapped[str] = mapped_column(String(30), ForeignKey("stocks.nse_symbol"), primary_key=True)
    valuation_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("valuation_runs.id"))
    iv_bear: Mapped[Optional[float]] = mapped_column(Numeric(12, 2))
    iv_base: Mapped[Optional[float]] = mapped_column(Numeric(12, 2))
    iv_bull: Mapped[Optional[float]] = mapped_column(Numeric(12, 2))
    iv_blended: Mapped[Optional[float]] = mapped_column(Numeric(12, 2))
    cmp: Mapped[Optional[float]] = mapped_column(Numeric(12, 2))
    upside_pct: Mapped[Optional[float]] = mapped_column(Numeric(8, 4))
    margin_of_safety: Mapped[Optional[float]] = mapped_column(Numeric(8, 4))
    primary_model: Mapped[Optional[str]] = mapped_column(String(50))
    valuation_confidence: Mapped[Optional[str]] = mapped_column(String(10))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    stock: Mapped[Stock] = relationship(back_populates="intrinsic_value")


# ── ML Scores ─────────────────────────────────────────────────
class MLScores(Base):
    __tablename__ = "ml_scores"

    nse_symbol: Mapped[str] = mapped_column(String(30), ForeignKey("stocks.nse_symbol"), primary_key=True)
    risk_score: Mapped[Optional[int]] = mapped_column(Integer)
    risk_level: Mapped[Optional[str]] = mapped_column(String(10))
    risk_drivers: Mapped[list] = mapped_column(JSONB, default=list)
    risk_debt: Mapped[Optional[int]] = mapped_column(Integer)
    risk_liquidity: Mapped[Optional[int]] = mapped_column(Integer)
    risk_governance: Mapped[Optional[int]] = mapped_column(Integer)
    risk_earnings_quality: Mapped[Optional[int]] = mapped_column(Integer)
    risk_value_trap: Mapped[Optional[int]] = mapped_column(Integer)
    risk_volatility: Mapped[Optional[int]] = mapped_column(Integer)
    risk_sector: Mapped[Optional[int]] = mapped_column(Integer)
    risk_data_quality: Mapped[Optional[int]] = mapped_column(Integer)
    valuation_confidence_score: Mapped[Optional[int]] = mapped_column(Integer)
    valuation_confidence: Mapped[Optional[str]] = mapped_column(String(10))
    confidence_factors: Mapped[list] = mapped_column(JSONB, default=list)
    fundamental_score: Mapped[Optional[int]] = mapped_column(Integer)
    valuation_gap_score: Mapped[Optional[int]] = mapped_column(Integer)
    profitability_score: Mapped[Optional[int]] = mapped_column(Integer)
    growth_score: Mapped[Optional[int]] = mapped_column(Integer)
    balance_sheet_score: Mapped[Optional[int]] = mapped_column(Integer)
    cash_flow_score: Mapped[Optional[int]] = mapped_column(Integer)
    risk_penalty: Mapped[Optional[int]] = mapped_column(Integer)
    growth_outlook_score: Mapped[Optional[int]] = mapped_column(Integer)
    revenue_cagr_score: Mapped[Optional[int]] = mapped_column(Integer)
    pat_cagr_score: Mapped[Optional[int]] = mapped_column(Integer)
    margin_expansion_score: Mapped[Optional[int]] = mapped_column(Integer)
    sector_tailwind_score: Mapped[Optional[int]] = mapped_column(Integer)
    risk_computed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    confidence_computed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    fundamental_computed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    stock: Mapped[Stock] = relationship(back_populates="ml_scores")


# ── Signal ────────────────────────────────────────────────────
class Signal(Base):
    __tablename__ = "signals"

    nse_symbol: Mapped[str] = mapped_column(String(30), ForeignKey("stocks.nse_symbol"), primary_key=True)
    signal: Mapped[str] = mapped_column(String(30), nullable=False)
    signal_color: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    signal_label: Mapped[str] = mapped_column(String(50), nullable=False)
    fundamental_score: Mapped[Optional[int]] = mapped_column(Integer)
    growth_score: Mapped[Optional[int]] = mapped_column(Integer)
    risk_score: Mapped[Optional[int]] = mapped_column(Integer)
    valuation_confidence: Mapped[Optional[str]] = mapped_column(String(10))
    upside_pct: Mapped[Optional[float]] = mapped_column(Numeric(8, 4), index=True)
    margin_of_safety: Mapped[Optional[float]] = mapped_column(Numeric(8, 4))
    conditions: Mapped[dict] = mapped_column(JSONB, default=dict)
    main_reason: Mapped[Optional[str]] = mapped_column(Text)
    blocking_flags: Mapped[list] = mapped_column(JSONB, default=list)
    prev_signal: Mapped[Optional[str]] = mapped_column(String(30))
    prev_signal_color: Mapped[Optional[str]] = mapped_column(String(10))
    signal_changed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    data_freshness: Mapped[str] = mapped_column(String(20), default="UNKNOWN")
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # BUG16 FIX: added onupdate so updated_at refreshes on every signal change
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    stock: Mapped[Stock] = relationship(back_populates="signal")


# ── Risk Flags ────────────────────────────────────────────────
class RiskFlag(Base):
    __tablename__ = "risk_flags"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    nse_symbol: Mapped[str] = mapped_column(String(30), ForeignKey("stocks.nse_symbol"), nullable=False, index=True)
    flag_type: Mapped[str] = mapped_column(String(50), nullable=False)
    severity: Mapped[str] = mapped_column(String(10), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    data_source: Mapped[Optional[str]] = mapped_column(String(50))

    stock: Mapped[Stock] = relationship(back_populates="risk_flags")


# ── Watchlist ─────────────────────────────────────────────────
class Watchlist(Base):
    __tablename__ = "watchlist"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    nse_symbol: Mapped[str] = mapped_column(String(30), ForeignKey("stocks.nse_symbol"), unique=True)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    stock: Mapped[Stock] = relationship(back_populates="watchlist")


# ── Alerts ────────────────────────────────────────────────────
class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    nse_symbol: Mapped[str] = mapped_column(String(30), ForeignKey("stocks.nse_symbol"), nullable=False)
    alert_type: Mapped[str] = mapped_column(String(30), nullable=False)
    condition_value: Mapped[Optional[float]] = mapped_column(Numeric(12, 4))
    condition_text: Mapped[Optional[str]] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_triggered: Mapped[bool] = mapped_column(Boolean, default=False)
    last_triggered: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ── Portfolio ─────────────────────────────────────────────────
class Portfolio(Base):
    __tablename__ = "portfolio"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), default="My Portfolio")
    description: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    holdings: Mapped[List["PortfolioHolding"]] = relationship(back_populates="portfolio")


class PortfolioHolding(Base):
    __tablename__ = "portfolio_holdings"
    __table_args__ = (UniqueConstraint("portfolio_id", "nse_symbol"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    portfolio_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("portfolio.id"), nullable=False)
    nse_symbol: Mapped[str] = mapped_column(String(30), ForeignKey("stocks.nse_symbol"), nullable=False)
    quantity: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False)
    avg_cost: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    buy_date: Mapped[Optional[date]] = mapped_column(Date)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    portfolio: Mapped[Portfolio] = relationship(back_populates="holdings")


# ── AI Reports ────────────────────────────────────────────────
class AIReport(Base):
    __tablename__ = "ai_reports"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    nse_symbol: Mapped[Optional[str]] = mapped_column(String(30), ForeignKey("stocks.nse_symbol"), index=True)
    sector_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("sector_classification.id"))
    report_type: Mapped[str] = mapped_column(String(30), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    sources: Mapped[list] = mapped_column(JSONB, default=list)
    model_used: Mapped[Optional[str]] = mapped_column(String(50))
    tokens_used: Mapped[Optional[int]] = mapped_column(Integer)
    data_version: Mapped[Optional[str]] = mapped_column(String(64))
    disclaimer: Mapped[str] = mapped_column(Text, default="AI-generated research summary. Not investment advice. Verify independently.")
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


# ── Audit & System ────────────────────────────────────────────
class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    action: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    entity_type: Mapped[Optional[str]] = mapped_column(String(50))
    entity_id: Mapped[Optional[str]] = mapped_column(Text)
    details: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="SUCCESS")
    error: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SystemJob(Base):
    __tablename__ = "system_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer)
    stocks_processed: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[list] = mapped_column(JSONB, default=list)
    triggered_by: Mapped[str] = mapped_column(String(20), default="SCHEDULER")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DataImportLog(Base):
    __tablename__ = "data_import_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    import_type: Mapped[str] = mapped_column(String(50), nullable=False)
    source: Mapped[Optional[str]] = mapped_column(String(50))
    filename: Mapped[Optional[str]] = mapped_column(Text)
    records_total: Mapped[int] = mapped_column(Integer, default=0)
    records_success: Mapped[int] = mapped_column(Integer, default=0)
    records_failed: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[list] = mapped_column(JSONB, default=list)
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class AdminOverride(Base):
    __tablename__ = "admin_overrides"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    table_name: Mapped[str] = mapped_column(String(50), nullable=False)
    record_id: Mapped[str] = mapped_column(Text, nullable=False)
    field_name: Mapped[str] = mapped_column(String(100), nullable=False)
    old_value: Mapped[Optional[str]] = mapped_column(Text)
    new_value: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    override_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ── Price Candles Daily ───────────────────────────────────────
class PriceCandleDaily(Base):
    __tablename__ = "price_candles_daily"
    __table_args__ = (UniqueConstraint("nse_symbol", "date"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    nse_symbol: Mapped[str] = mapped_column(String(30), ForeignKey("stocks.nse_symbol"), nullable=False, index=True)
    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    open: Mapped[Optional[float]] = mapped_column(Numeric(12, 2))
    high: Mapped[Optional[float]] = mapped_column(Numeric(12, 2))
    low: Mapped[Optional[float]] = mapped_column(Numeric(12, 2))
    close: Mapped[Optional[float]] = mapped_column(Numeric(12, 2))
    volume: Mapped[Optional[int]] = mapped_column(BigInteger)
    adjusted_close: Mapped[Optional[float]] = mapped_column(Numeric(12, 2))
    data_source: Mapped[str] = mapped_column(String(30), default="EOD_SNAPSHOT")


# ── Price Candles 1-Minute ────────────────────────────────────
class PriceCandle1m(Base):
    __tablename__ = "price_candles_1m"
    # BUG5 FIX: Removed UniqueConstraint — composite PK already enforces uniqueness
    # Adding it again creates a duplicate index that breaks create_all

    nse_symbol: Mapped[str] = mapped_column(String(30), ForeignKey("stocks.nse_symbol"), primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    open: Mapped[Optional[float]] = mapped_column(Numeric(12, 2))
    high: Mapped[Optional[float]] = mapped_column(Numeric(12, 2))
    low: Mapped[Optional[float]] = mapped_column(Numeric(12, 2))
    close: Mapped[Optional[float]] = mapped_column(Numeric(12, 2))
    volume: Mapped[Optional[int]] = mapped_column(BigInteger)


# ── Reverse DCF Output ────────────────────────────────────────
class ReverseDcfOutput(Base):
    __tablename__ = "reverse_dcf_outputs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    nse_symbol: Mapped[str] = mapped_column(String(30), ForeignKey("stocks.nse_symbol"), nullable=False, index=True)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    cmp: Mapped[Optional[float]] = mapped_column(Numeric(12, 2))
    implied_growth_rate: Mapped[Optional[float]] = mapped_column(Numeric(8, 4))
    historical_growth_rate: Mapped[Optional[float]] = mapped_column(Numeric(8, 4))
    premium_discount_pct: Mapped[Optional[float]] = mapped_column(Numeric(8, 4))
    interpretation: Mapped[Optional[str]] = mapped_column(Text)
    wacc_used: Mapped[Optional[float]] = mapped_column(Numeric(6, 4))
    terminal_growth_used: Mapped[Optional[float]] = mapped_column(Numeric(6, 4))


# ── Valuation Assumptions ─────────────────────────────────────
class ValuationAssumption(Base):
    __tablename__ = "valuation_assumptions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    nse_symbol: Mapped[str] = mapped_column(String(30), ForeignKey("stocks.nse_symbol"), nullable=False, index=True)
    scenario: Mapped[str] = mapped_column(String(10), nullable=False)  # BEAR / BASE / BULL
    revenue_growth_yr1_5: Mapped[Optional[float]] = mapped_column(Numeric(6, 4))
    revenue_growth_yr6_10: Mapped[Optional[float]] = mapped_column(Numeric(6, 4))
    terminal_growth_rate: Mapped[Optional[float]] = mapped_column(Numeric(6, 4))
    wacc: Mapped[Optional[float]] = mapped_column(Numeric(6, 4))
    ebitda_margin_target: Mapped[Optional[float]] = mapped_column(Numeric(6, 4))
    capex_pct_revenue: Mapped[Optional[float]] = mapped_column(Numeric(6, 4))
    tax_rate: Mapped[Optional[float]] = mapped_column(Numeric(6, 4))
    is_override: Mapped[bool] = mapped_column(Boolean, default=False)
    override_reason: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ── Shareholding Pattern ──────────────────────────────────────
class ShareholdingPattern(Base):
    __tablename__ = "shareholding_patterns"
    __table_args__ = (UniqueConstraint("nse_symbol", "quarter_end"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    nse_symbol: Mapped[str] = mapped_column(String(30), ForeignKey("stocks.nse_symbol"), nullable=False, index=True)
    quarter_end: Mapped[date] = mapped_column(Date, nullable=False)
    promoter_pct: Mapped[Optional[float]] = mapped_column(Numeric(6, 2))
    promoter_pledge_pct: Mapped[Optional[float]] = mapped_column(Numeric(6, 2))
    fii_pct: Mapped[Optional[float]] = mapped_column(Numeric(6, 2))
    dii_pct: Mapped[Optional[float]] = mapped_column(Numeric(6, 2))
    mutual_fund_pct: Mapped[Optional[float]] = mapped_column(Numeric(6, 2))
    public_pct: Mapped[Optional[float]] = mapped_column(Numeric(6, 2))
    retail_pct: Mapped[Optional[float]] = mapped_column(Numeric(6, 2))
    hni_pct: Mapped[Optional[float]] = mapped_column(Numeric(6, 2))
    data_source: Mapped[str] = mapped_column(String(50), default="BSE_SHP")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ── ML Cluster Results ────────────────────────────────────────
class MLClusterResult(Base):
    __tablename__ = "ml_cluster_results"
    __table_args__ = (UniqueConstraint("nse_symbol", "run_date"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    nse_symbol: Mapped[str] = mapped_column(String(30), ForeignKey("stocks.nse_symbol"), nullable=False, index=True)
    run_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    cluster_label: Mapped[Optional[str]] = mapped_column(String(50), index=True)
    cluster_id: Mapped[Optional[int]] = mapped_column(Integer)
    cluster_description: Mapped[Optional[str]] = mapped_column(Text)
    distance_to_centroid: Mapped[Optional[float]] = mapped_column(Numeric(10, 6))
    features_used: Mapped[list] = mapped_column(JSONB, default=list)
    feature_values: Mapped[dict] = mapped_column(JSONB, default=dict)
    algorithm: Mapped[str] = mapped_column(String(30), default="KMEANS")
    model_version: Mapped[Optional[str]] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
