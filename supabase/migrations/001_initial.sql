-- ============================================================
-- StockLens — Complete Database Schema
-- Migration: 001_initial.sql
-- Run via: psql $DATABASE_URL -f supabase/migrations/001_initial.sql
-- ============================================================

-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "vector";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";   -- for fuzzy symbol search
CREATE EXTENSION IF NOT EXISTS "btree_gin"; -- for GIN indexes

-- ============================================================
-- SECTION 1: STOCK UNIVERSE
-- ============================================================

CREATE TABLE IF NOT EXISTS sector_classification (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    macro_sector    VARCHAR(100) NOT NULL,  -- e.g. "Financial Services"
    sector          VARCHAR(100) NOT NULL,  -- e.g. "Banks"
    industry        VARCHAR(100),           -- e.g. "Private Sector Banks"
    basic_industry  VARCHAR(100),           -- e.g. "Large Private Banks"
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS stocks (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    nse_symbol          VARCHAR(30) UNIQUE NOT NULL,
    bse_code            VARCHAR(10),
    isin                VARCHAR(12) UNIQUE,
    company_name        VARCHAR(200) NOT NULL,
    short_name          VARCHAR(50),
    listing_status      VARCHAR(20) DEFAULT 'ACTIVE', -- ACTIVE, SUSPENDED, DELISTED
    instrument_type     VARCHAR(20) DEFAULT 'EQ',     -- EQ, ETF, INDEX, FO
    market_cap_category VARCHAR(10),                  -- LARGE, MID, SMALL, MICRO
    face_value          NUMERIC(10,2),
    lot_size            INTEGER DEFAULT 1,
    sector_id           UUID REFERENCES sector_classification(id),
    is_nifty50          BOOLEAN DEFAULT FALSE,
    is_nifty500         BOOLEAN DEFAULT FALSE,
    is_fno              BOOLEAN DEFAULT FALSE,
    data_source         VARCHAR(50) DEFAULT 'NSE_BHAVCOPY',
    is_active           BOOLEAN DEFAULT TRUE,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_stocks_symbol ON stocks(nse_symbol);
CREATE INDEX idx_stocks_sector ON stocks(sector_id);
CREATE INDEX idx_stocks_active ON stocks(is_active);
CREATE INDEX idx_stocks_instrument ON stocks(instrument_type);
-- Fuzzy search index
CREATE INDEX idx_stocks_name_trgm ON stocks USING gin(company_name gin_trgm_ops);

-- ============================================================
-- SECTION 2: REAL-TIME PRICE DATA
-- ============================================================

CREATE TABLE IF NOT EXISTS market_status (
    id              SERIAL PRIMARY KEY,
    status          VARCHAR(20) NOT NULL, -- OPEN, CLOSED, PRE_OPEN, POST_CLOSE, HOLIDAY
    session_date    DATE NOT NULL DEFAULT CURRENT_DATE,
    open_time       TIMESTAMPTZ,
    close_time      TIMESTAMPTZ,
    last_checked    TIMESTAMPTZ DEFAULT NOW()
);

-- Latest price — Redis primary, DB backup
-- This table is upserted on every tick (symbol = unique key)
CREATE TABLE IF NOT EXISTS realtime_quotes (
    nse_symbol      VARCHAR(30) PRIMARY KEY REFERENCES stocks(nse_symbol),
    ltp             NUMERIC(12,2),   -- last traded price
    open            NUMERIC(12,2),
    high            NUMERIC(12,2),
    low             NUMERIC(12,2),
    close           NUMERIC(12,2),   -- previous close
    volume          BIGINT,
    value           NUMERIC(20,2),   -- traded value
    bid             NUMERIC(12,2),
    ask             NUMERIC(12,2),
    change_abs      NUMERIC(10,2),
    change_pct      NUMERIC(8,4),
    week_52_high    NUMERIC(12,2),
    week_52_low     NUMERIC(12,2),
    market_cap      NUMERIC(20,2),
    data_source     VARCHAR(30) DEFAULT 'UPSTOX',
    is_stale        BOOLEAN DEFAULT FALSE,
    last_updated    TIMESTAMPTZ DEFAULT NOW()
);

-- 1-minute candles (partitioned by month for performance)
CREATE TABLE IF NOT EXISTS price_candles_1m (
    id              BIGSERIAL,
    nse_symbol      VARCHAR(30) NOT NULL,
    ts              TIMESTAMPTZ NOT NULL,   -- candle open timestamp
    open            NUMERIC(12,2) NOT NULL,
    high            NUMERIC(12,2) NOT NULL,
    low             NUMERIC(12,2) NOT NULL,
    close           NUMERIC(12,2) NOT NULL,
    volume          BIGINT DEFAULT 0,
    value           NUMERIC(20,2),
    vwap            NUMERIC(12,2),
    PRIMARY KEY (id, ts)
) PARTITION BY RANGE (ts);

-- Create partitions for next 3 months (add more as needed)
CREATE TABLE price_candles_1m_2025_07 PARTITION OF price_candles_1m
    FOR VALUES FROM ('2025-07-01') TO ('2025-08-01');
CREATE TABLE price_candles_1m_2025_08 PARTITION OF price_candles_1m
    FOR VALUES FROM ('2025-08-01') TO ('2025-09-01');
CREATE TABLE price_candles_1m_2025_09 PARTITION OF price_candles_1m
    FOR VALUES FROM ('2025-09-01') TO ('2025-10-01');
CREATE TABLE price_candles_1m_2025_10 PARTITION OF price_candles_1m
    FOR VALUES FROM ('2025-10-01') TO ('2025-11-01');
CREATE TABLE price_candles_1m_2025_11 PARTITION OF price_candles_1m
    FOR VALUES FROM ('2025-11-01') TO ('2025-12-01');
CREATE TABLE price_candles_1m_2025_12 PARTITION OF price_candles_1m
    FOR VALUES FROM ('2025-12-01') TO ('2026-01-01');
CREATE TABLE price_candles_1m_2026_01 PARTITION OF price_candles_1m
    FOR VALUES FROM ('2026-01-01') TO ('2026-02-01');
CREATE TABLE price_candles_1m_2026_02 PARTITION OF price_candles_1m
    FOR VALUES FROM ('2026-02-01') TO ('2026-03-01');
CREATE TABLE price_candles_1m_2026_03 PARTITION OF price_candles_1m
    FOR VALUES FROM ('2026-03-01') TO ('2026-04-01');
CREATE TABLE price_candles_1m_2026_04 PARTITION OF price_candles_1m
    FOR VALUES FROM ('2026-04-01') TO ('2026-05-01');
CREATE TABLE price_candles_1m_2026_05 PARTITION OF price_candles_1m
    FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');
CREATE TABLE price_candles_1m_2026_06 PARTITION OF price_candles_1m
    FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');
CREATE TABLE price_candles_1m_2026_07 PARTITION OF price_candles_1m
    FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');
CREATE TABLE price_candles_1m_2026_08 PARTITION OF price_candles_1m
    FOR VALUES FROM ('2026-08-01') TO ('2026-09-01');
CREATE TABLE price_candles_1m_2026_09 PARTITION OF price_candles_1m
    FOR VALUES FROM ('2026-09-01') TO ('2026-10-01');
CREATE TABLE price_candles_1m_2026_10 PARTITION OF price_candles_1m
    FOR VALUES FROM ('2026-10-01') TO ('2026-11-01');
CREATE TABLE price_candles_1m_2026_11 PARTITION OF price_candles_1m
    FOR VALUES FROM ('2026-11-01') TO ('2026-12-01');
CREATE TABLE price_candles_1m_2026_12 PARTITION OF price_candles_1m
    FOR VALUES FROM ('2026-12-01') TO ('2027-01-01');

CREATE INDEX idx_candles_1m_symbol_ts ON price_candles_1m(nse_symbol, ts DESC);

-- Daily OHLCV (historical)
CREATE TABLE IF NOT EXISTS price_candles_daily (
    id          BIGSERIAL PRIMARY KEY,
    nse_symbol  VARCHAR(30) NOT NULL,
    date        DATE NOT NULL,
    open        NUMERIC(12,2),
    high        NUMERIC(12,2),
    low         NUMERIC(12,2),
    close       NUMERIC(12,2),
    volume      BIGINT,
    value       NUMERIC(20,2),
    delivery_pct NUMERIC(6,2),
    data_source VARCHAR(30) DEFAULT 'NSE_BHAVCOPY',
    UNIQUE(nse_symbol, date)
);

CREATE INDEX idx_daily_symbol_date ON price_candles_daily(nse_symbol, date DESC);

-- ============================================================
-- SECTION 3: FUNDAMENTALS
-- ============================================================

CREATE TABLE IF NOT EXISTS financial_results (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    nse_symbol          VARCHAR(30) NOT NULL REFERENCES stocks(nse_symbol),
    period_type         VARCHAR(10) NOT NULL,  -- Q (quarterly), A (annual)
    period_end          DATE NOT NULL,
    -- Income Statement
    revenue             NUMERIC(20,2),
    revenue_growth_yoy  NUMERIC(8,4),
    gross_profit        NUMERIC(20,2),
    ebitda              NUMERIC(20,2),
    ebit                NUMERIC(20,2),
    pbt                 NUMERIC(20,2),
    tax                 NUMERIC(20,2),
    pat                 NUMERIC(20,2),        -- profit after tax
    pat_growth_yoy      NUMERIC(8,4),
    eps                 NUMERIC(12,4),
    eps_diluted         NUMERIC(12,4),
    exceptional_items   NUMERIC(20,2),
    -- Balance Sheet
    total_assets        NUMERIC(20,2),
    total_liabilities   NUMERIC(20,2),
    net_worth           NUMERIC(20,2),
    equity_capital      NUMERIC(20,2),
    reserves            NUMERIC(20,2),
    total_debt          NUMERIC(20,2),
    long_term_debt      NUMERIC(20,2),
    short_term_debt     NUMERIC(20,2),
    cash_and_equiv      NUMERIC(20,2),
    investments         NUMERIC(20,2),
    fixed_assets        NUMERIC(20,2),
    goodwill            NUMERIC(20,2),
    -- Cash Flow
    cfo                 NUMERIC(20,2),       -- cash from operations
    cfi                 NUMERIC(20,2),       -- cash from investing
    cff                 NUMERIC(20,2),       -- cash from financing
    capex               NUMERIC(20,2),
    free_cash_flow      NUMERIC(20,2),
    dividends_paid      NUMERIC(20,2),
    -- Additional
    shares_outstanding  BIGINT,
    book_value_per_share NUMERIC(12,4),
    -- Bank/NBFC specific
    net_interest_income NUMERIC(20,2),
    nim                 NUMERIC(6,4),        -- net interest margin
    gnpa_pct            NUMERIC(6,4),        -- gross NPA %
    nnpa_pct            NUMERIC(6,4),        -- net NPA %
    credit_cost         NUMERIC(6,4),
    roe_bank            NUMERIC(6,4),
    -- IT specific
    deal_wins_usd       NUMERIC(20,2),
    attrition_pct       NUMERIC(6,4),
    -- Data quality
    data_source         VARCHAR(50) DEFAULT 'BSE_XML',
    is_verified         BOOLEAN DEFAULT FALSE,
    confidence_level    VARCHAR(10) DEFAULT 'LOW', -- HIGH, MEDIUM, LOW
    has_exceptional     BOOLEAN DEFAULT FALSE,
    notes               TEXT,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(nse_symbol, period_type, period_end)
);

CREATE INDEX idx_financials_symbol_period ON financial_results(nse_symbol, period_type, period_end DESC);

CREATE TABLE IF NOT EXISTS shareholding_patterns (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    nse_symbol          VARCHAR(30) NOT NULL REFERENCES stocks(nse_symbol),
    period_end          DATE NOT NULL,
    promoter_pct        NUMERIC(6,3),
    promoter_pledge_pct NUMERIC(6,3),
    fii_pct             NUMERIC(6,3),
    dii_pct             NUMERIC(6,3),
    mf_pct              NUMERIC(6,3),
    insurance_pct       NUMERIC(6,3),
    public_pct          NUMERIC(6,3),
    other_inst_pct      NUMERIC(6,3),
    promoter_change     NUMERIC(6,3),   -- QoQ change
    fii_change          NUMERIC(6,3),
    dii_change          NUMERIC(6,3),
    data_source         VARCHAR(50) DEFAULT 'BSE_XML',
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(nse_symbol, period_end)
);

CREATE TABLE IF NOT EXISTS corporate_actions (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    nse_symbol      VARCHAR(30) NOT NULL REFERENCES stocks(nse_symbol),
    action_type     VARCHAR(30) NOT NULL, -- DIVIDEND, SPLIT, BONUS, RIGHTS, BUYBACK
    ex_date         DATE NOT NULL,
    record_date     DATE,
    amount          NUMERIC(12,4),
    ratio           VARCHAR(20),    -- for splits/bonus e.g. "1:2"
    description     TEXT,
    data_source     VARCHAR(50),
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- SECTION 4: COMPUTED RATIOS
-- ============================================================

CREATE TABLE IF NOT EXISTS financial_ratios (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    nse_symbol          VARCHAR(30) NOT NULL REFERENCES stocks(nse_symbol),
    computed_at         TIMESTAMPTZ DEFAULT NOW(),
    as_of_date          DATE NOT NULL,         -- data as of this date
    -- Valuation Ratios
    pe                  NUMERIC(10,4),         -- price/earnings
    pb                  NUMERIC(10,4),         -- price/book
    ps                  NUMERIC(10,4),         -- price/sales
    ev_ebitda           NUMERIC(10,4),
    ev_sales            NUMERIC(10,4),
    peg                 NUMERIC(10,4),
    market_cap          NUMERIC(20,2),
    enterprise_value    NUMERIC(20,2),
    -- Profitability
    roe                 NUMERIC(8,4),
    roce                NUMERIC(8,4),
    roa                 NUMERIC(8,4),
    roic                NUMERIC(8,4),
    gross_margin        NUMERIC(8,4),
    ebitda_margin       NUMERIC(8,4),
    operating_margin    NUMERIC(8,4),
    net_margin          NUMERIC(8,4),
    -- Growth (YoY and CAGR)
    revenue_growth_1y   NUMERIC(8,4),
    revenue_cagr_3y     NUMERIC(8,4),
    revenue_cagr_5y     NUMERIC(8,4),
    revenue_cagr_10y    NUMERIC(8,4),
    pat_growth_1y       NUMERIC(8,4),
    pat_cagr_3y         NUMERIC(8,4),
    pat_cagr_5y         NUMERIC(8,4),
    pat_cagr_10y        NUMERIC(8,4),
    eps_cagr_3y         NUMERIC(8,4),
    eps_cagr_5y         NUMERIC(8,4),
    margin_expansion_3y NUMERIC(8,4),
    -- Leverage
    debt_equity         NUMERIC(10,4),
    debt_ebitda         NUMERIC(10,4),
    interest_coverage   NUMERIC(10,4),
    net_debt_equity     NUMERIC(10,4),
    -- Liquidity & Efficiency
    current_ratio       NUMERIC(10,4),
    quick_ratio         NUMERIC(10,4),
    cfo_pat             NUMERIC(10,4),   -- cash conversion
    fcf_margin          NUMERIC(8,4),
    asset_turnover      NUMERIC(10,4),
    receivable_days     NUMERIC(8,2),
    inventory_days      NUMERIC(8,2),
    payable_days        NUMERIC(8,2),
    -- Dividend
    dividend_yield      NUMERIC(8,4),
    dividend_payout     NUMERIC(8,4),
    -- Confidence
    data_completeness   NUMERIC(5,4),  -- 0.0–1.0
    computed_ok         BOOLEAN DEFAULT TRUE,
    error_fields        JSONB DEFAULT '[]',
    UNIQUE(nse_symbol, as_of_date)
);

CREATE INDEX idx_ratios_symbol_date ON financial_ratios(nse_symbol, as_of_date DESC);

-- ============================================================
-- SECTION 5: VALUATION ENGINE
-- ============================================================

CREATE TABLE IF NOT EXISTS valuation_assumptions (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    nse_symbol      VARCHAR(30) NOT NULL,
    scenario        VARCHAR(10) NOT NULL,   -- BEAR, BASE, BULL
    source          VARCHAR(20) DEFAULT 'SYSTEM', -- SYSTEM, ADMIN
    -- DCF assumptions
    revenue_growth_yr1_5    NUMERIC(8,4),
    revenue_growth_yr6_10   NUMERIC(8,4),
    terminal_growth_rate    NUMERIC(8,4),
    ebitda_margin_target    NUMERIC(8,4),
    wacc                    NUMERIC(8,4),
    tax_rate                NUMERIC(8,4),
    capex_pct_revenue       NUMERIC(8,4),
    wc_change_pct_revenue   NUMERIC(8,4),
    -- Sector-specific
    pe_target               NUMERIC(8,4),
    pb_target               NUMERIC(8,4),
    ev_ebitda_target        NUMERIC(8,4),
    roe_target              NUMERIC(8,4),
    -- Context
    notes                   TEXT,
    is_active               BOOLEAN DEFAULT TRUE,
    created_at              TIMESTAMPTZ DEFAULT NOW(),
    updated_at              TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(nse_symbol, scenario, source)
);

CREATE TABLE IF NOT EXISTS valuation_runs (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    nse_symbol      VARCHAR(30) NOT NULL REFERENCES stocks(nse_symbol),
    run_at          TIMESTAMPTZ DEFAULT NOW(),
    triggered_by    VARCHAR(30) DEFAULT 'SCHEDULER',  -- SCHEDULER, MANUAL
    models_used     JSONB DEFAULT '[]',               -- list of models applied
    -- Bear / Base / Bull intrinsic values
    iv_bear         NUMERIC(12,2),
    iv_base         NUMERIC(12,2),
    iv_bull         NUMERIC(12,2),
    iv_blended      NUMERIC(12,2),    -- 0.25 bear + 0.50 base + 0.25 bull
    cmp             NUMERIC(12,2),    -- current market price at time of run
    upside_pct      NUMERIC(8,4),     -- (iv_blended - cmp) / cmp * 100
    margin_of_safety NUMERIC(8,4),   -- 1 - cmp/iv_blended
    sector_id       UUID REFERENCES sector_classification(id),
    primary_model   VARCHAR(50),      -- DCF, PE, PB_ROE, EV_EBITDA, NAV, etc.
    status          VARCHAR(20) DEFAULT 'SUCCESS', -- SUCCESS, FAILED, INSUFFICIENT_DATA
    error_message   TEXT,
    UNIQUE(nse_symbol, run_at)
);

CREATE INDEX idx_valuation_runs_symbol ON valuation_runs(nse_symbol, run_at DESC);

CREATE TABLE IF NOT EXISTS dcf_outputs (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    valuation_run_id UUID NOT NULL REFERENCES valuation_runs(id),
    scenario        VARCHAR(10) NOT NULL,   -- BEAR, BASE, BULL
    -- Free Cash Flow projections (10 years)
    fcf_projections JSONB DEFAULT '[]',    -- [{year:1, revenue:X, fcf:Y}, ...]
    terminal_value  NUMERIC(20,2),
    pv_fcf          NUMERIC(20,2),
    pv_terminal     NUMERIC(20,2),
    enterprise_value NUMERIC(20,2),
    net_debt        NUMERIC(20,2),
    equity_value    NUMERIC(20,2),
    shares          BIGINT,
    iv_per_share    NUMERIC(12,2),
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS reverse_dcf_outputs (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    nse_symbol      VARCHAR(30) NOT NULL REFERENCES stocks(nse_symbol),
    computed_at     TIMESTAMPTZ DEFAULT NOW(),
    cmp             NUMERIC(12,2),
    implied_growth_rate     NUMERIC(8,4),  -- what growth % is "priced in"
    implied_growth_years    INTEGER,
    historical_growth_rate  NUMERIC(8,4),  -- actual historical CAGR
    premium_discount_pct    NUMERIC(8,4),  -- how much more than historical
    interpretation  TEXT,  -- "Priced for X% growth; historical is Y%"
    wacc_used       NUMERIC(8,4),
    terminal_growth_used NUMERIC(8,4)
);

CREATE INDEX idx_rev_dcf_symbol ON reverse_dcf_outputs(nse_symbol, computed_at DESC);

-- Intrinsic values — final blended result (latest per stock)
CREATE TABLE IF NOT EXISTS intrinsic_values (
    nse_symbol          VARCHAR(30) PRIMARY KEY REFERENCES stocks(nse_symbol),
    valuation_run_id    UUID REFERENCES valuation_runs(id),
    iv_bear             NUMERIC(12,2),
    iv_base             NUMERIC(12,2),
    iv_bull             NUMERIC(12,2),
    iv_blended          NUMERIC(12,2),
    cmp                 NUMERIC(12,2),
    upside_pct          NUMERIC(8,4),
    margin_of_safety    NUMERIC(8,4),
    primary_model       VARCHAR(50),
    valuation_confidence VARCHAR(10),  -- HIGH, MEDIUM, LOW
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- SECTION 6: ML OUTPUTS
-- ============================================================

CREATE TABLE IF NOT EXISTS cluster_results (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    nse_symbol      VARCHAR(30) NOT NULL REFERENCES stocks(nse_symbol),
    computed_at     TIMESTAMPTZ DEFAULT NOW(),
    cluster_label   VARCHAR(50) NOT NULL,
    -- Labels: QUALITY_COMPOUNDER, DEEP_VALUE, GARP, CYCLICAL_RECOVERY,
    --         HIGH_GROWTH_EXPENSIVE, VALUE_TRAP, MOMENTUM_TRAP,
    --         DISTRESSED, LOW_LIQUIDITY
    cluster_id      INTEGER,
    confidence      NUMERIC(5,4),           -- model confidence 0–1
    top_features    JSONB DEFAULT '[]',      -- [{feature, importance, value}]
    similar_peers   JSONB DEFAULT '[]',      -- [nse_symbol, ...]
    explanation     TEXT,
    pca_x           NUMERIC(8,6),           -- for 2D UMAP visualization
    pca_y           NUMERIC(8,6),
    is_outlier      BOOLEAN DEFAULT FALSE,   -- DBSCAN outlier
    UNIQUE(nse_symbol, computed_at)
);

CREATE TABLE IF NOT EXISTS ml_scores (
    nse_symbol              VARCHAR(30) PRIMARY KEY REFERENCES stocks(nse_symbol),
    -- Risk Score (XGBoost)
    risk_score              INTEGER,         -- 0–100 (lower = safer)
    risk_level              VARCHAR(10),     -- LOW, MEDIUM, HIGH
    risk_drivers            JSONB DEFAULT '[]', -- top drivers
    risk_debt               INTEGER,         -- sub-scores
    risk_liquidity          INTEGER,
    risk_governance         INTEGER,
    risk_earnings_quality   INTEGER,
    risk_value_trap         INTEGER,
    risk_volatility         INTEGER,
    risk_sector             INTEGER,
    risk_data_quality       INTEGER,
    -- Valuation Confidence (LightGBM)
    valuation_confidence_score  INTEGER,     -- 0–100
    valuation_confidence        VARCHAR(10), -- HIGH, MEDIUM, LOW
    confidence_factors      JSONB DEFAULT '[]',
    -- Fundamental Score (rule-based composite)
    fundamental_score       INTEGER,         -- 0–100
    valuation_gap_score     INTEGER,         -- /30
    profitability_score     INTEGER,         -- /20
    growth_score            INTEGER,         -- /20
    balance_sheet_score     INTEGER,         -- /15
    cash_flow_score         INTEGER,         -- /10
    risk_penalty            INTEGER,         -- -15 to 0
    -- Growth Outlook Score
    growth_outlook_score    INTEGER,         -- 0–100
    revenue_cagr_score      INTEGER,
    pat_cagr_score          INTEGER,
    margin_expansion_score  INTEGER,
    sector_tailwind_score   INTEGER,
    -- Timestamps
    risk_computed_at        TIMESTAMPTZ,
    confidence_computed_at  TIMESTAMPTZ,
    fundamental_computed_at TIMESTAMPTZ,
    updated_at              TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- SECTION 7: SIGNAL ENGINE
-- ============================================================

CREATE TABLE IF NOT EXISTS risk_flags (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    nse_symbol      VARCHAR(30) NOT NULL REFERENCES stocks(nse_symbol),
    flag_type       VARCHAR(50) NOT NULL,
    -- Types: HIGH_DEBT, WEAK_CASHFLOW, NEGATIVE_FCF, PROMOTER_PLEDGE,
    --        AUDITOR_ISSUE, QUALIFIED_AUDIT, RELATED_PARTY_RISK,
    --        EQUITY_DILUTION, MARGIN_FALL, INVENTORY_SPIKE,
    --        RECEIVABLE_SPIKE, EARNINGS_MANIPULATION, LOW_LIQUIDITY,
    --        HIGH_VOLATILITY, GOVERNANCE_RISK, STALE_DATA, INSUFFICIENT_DATA
    severity        VARCHAR(10) NOT NULL,  -- HIGH, MEDIUM, LOW
    description     TEXT,
    detected_at     TIMESTAMPTZ DEFAULT NOW(),
    is_active       BOOLEAN DEFAULT TRUE,
    resolved_at     TIMESTAMPTZ,
    data_source     VARCHAR(50)
);

CREATE INDEX idx_risk_flags_symbol ON risk_flags(nse_symbol, is_active, severity);

CREATE TABLE IF NOT EXISTS signals (
    nse_symbol          VARCHAR(30) PRIMARY KEY REFERENCES stocks(nse_symbol),
    -- Signal labels (never "Buy"/"Sell")
    signal              VARCHAR(30) NOT NULL,
    -- GREEN: POTENTIALLY_UNDERVALUED / STRONG_WATCH
    -- YELLOW: REVIEW_REQUIRED
    -- RED: AVOID / OVERVALUED / WEAK
    -- GREY: INSUFFICIENT_DATA
    signal_color        VARCHAR(10) NOT NULL,  -- GREEN, YELLOW, RED, GREY
    signal_label        VARCHAR(50) NOT NULL,  -- display label
    -- Scores snapshot
    fundamental_score   INTEGER,
    growth_score        INTEGER,
    risk_score          INTEGER,
    valuation_confidence VARCHAR(10),
    upside_pct          NUMERIC(8,4),
    margin_of_safety    NUMERIC(8,4),
    -- Conditions met/failed (for explainability)
    conditions          JSONB DEFAULT '{}',
    main_reason         TEXT,
    blocking_flags      JSONB DEFAULT '[]',
    -- Previous signal for change tracking
    prev_signal         VARCHAR(30),
    prev_signal_color   VARCHAR(10),
    signal_changed_at   TIMESTAMPTZ,
    -- Freshness
    data_freshness      VARCHAR(20) DEFAULT 'UNKNOWN', -- FRESH, STALE, VERY_STALE
    computed_at         TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_signals_color ON signals(signal_color);
CREATE INDEX idx_signals_upside ON signals(upside_pct DESC);
CREATE INDEX idx_signals_fundamental ON signals(fundamental_score DESC);

-- ============================================================
-- SECTION 8: PERSONAL FEATURES (single user — no user_id FK)
-- ============================================================

CREATE TABLE IF NOT EXISTS watchlist (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    nse_symbol  VARCHAR(30) NOT NULL REFERENCES stocks(nse_symbol),
    notes       TEXT,
    added_at    TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(nse_symbol)
);

CREATE TABLE IF NOT EXISTS alerts (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    nse_symbol      VARCHAR(30) NOT NULL REFERENCES stocks(nse_symbol),
    alert_type      VARCHAR(30) NOT NULL,
    -- Types: PRICE_ABOVE, PRICE_BELOW, SIGNAL_CHANGE, VALUATION_UPSIDE,
    --        RESULT_PUBLISHED, PROMOTER_CHANGE, FII_CHANGE,
    --        PLEDGE_CHANGE, MARGIN_CHANGE, DEBT_CHANGE
    condition_value NUMERIC(12,4),
    condition_text  TEXT,
    is_active       BOOLEAN DEFAULT TRUE,
    is_triggered    BOOLEAN DEFAULT FALSE,
    last_triggered  TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS alert_history (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    alert_id        UUID REFERENCES alerts(id),
    nse_symbol      VARCHAR(30) NOT NULL,
    triggered_at    TIMESTAMPTZ DEFAULT NOW(),
    trigger_value   NUMERIC(12,4),
    message         TEXT
);

CREATE TABLE IF NOT EXISTS portfolio (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name        VARCHAR(100) DEFAULT 'My Portfolio',
    description TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- Insert default portfolio
INSERT INTO portfolio (name) VALUES ('My Portfolio') ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS portfolio_holdings (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    portfolio_id    UUID NOT NULL REFERENCES portfolio(id),
    nse_symbol      VARCHAR(30) NOT NULL REFERENCES stocks(nse_symbol),
    quantity        NUMERIC(12,4) NOT NULL,
    avg_cost        NUMERIC(12,2) NOT NULL,   -- average buy price
    buy_date        DATE,
    notes           TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(portfolio_id, nse_symbol)
);

-- ============================================================
-- SECTION 9: AI
-- ============================================================

CREATE TABLE IF NOT EXISTS ai_reports (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    nse_symbol      VARCHAR(30) REFERENCES stocks(nse_symbol),
    sector_id       UUID REFERENCES sector_classification(id),
    report_type     VARCHAR(30) NOT NULL,
    -- Types: STOCK_SUMMARY, VALUATION_EXPLAIN, RISK_SUMMARY,
    --        PEER_COMPARISON, SECTOR_SUMMARY, DCF_EXPLAIN,
    --        QUARTERLY_RESULT, CONCALL_SUMMARY
    content         TEXT NOT NULL,            -- sanitized AI output
    sources         JSONB DEFAULT '[]',       -- source data references
    model_used      VARCHAR(50),              -- gemini-1.5-flash, etc.
    tokens_used     INTEGER,
    data_version    VARCHAR(64),              -- hash of input data
    disclaimer      TEXT DEFAULT 'AI-generated research summary. Not investment advice. Verify independently.',
    generated_at    TIMESTAMPTZ DEFAULT NOW(),
    expires_at      TIMESTAMPTZ,              -- for cache invalidation
    UNIQUE(nse_symbol, report_type, data_version)
);

CREATE INDEX idx_ai_reports_symbol_type ON ai_reports(nse_symbol, report_type, generated_at DESC);

-- pgvector RAG store for financial news/documents
CREATE TABLE IF NOT EXISTS news_embeddings (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    nse_symbol      VARCHAR(30) REFERENCES stocks(nse_symbol),
    sector_id       UUID REFERENCES sector_classification(id),
    source_url      TEXT,
    headline        TEXT NOT NULL,
    content         TEXT,
    published_at    TIMESTAMPTZ,
    embedding       vector(768),    -- Gemini text-embedding-004 = 768 dims
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_news_embedding ON news_embeddings USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);
CREATE INDEX idx_news_symbol ON news_embeddings(nse_symbol, published_at DESC);

-- ============================================================
-- SECTION 10: ADMIN & OPERATIONS
-- ============================================================

CREATE TABLE IF NOT EXISTS data_sources (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name        VARCHAR(100) NOT NULL,
    source_type VARCHAR(30) NOT NULL, -- NSE_BHAVCOPY, BSE_XML, UPSTOX, CSV_UPLOAD
    base_url    TEXT,
    is_active   BOOLEAN DEFAULT TRUE,
    last_fetched TIMESTAMPTZ,
    notes       TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS data_import_logs (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    import_type     VARCHAR(50) NOT NULL, -- STOCK_UNIVERSE, FINANCIALS, PRICE, SECTOR
    source          VARCHAR(50),
    filename        TEXT,
    records_total   INTEGER DEFAULT 0,
    records_success INTEGER DEFAULT 0,
    records_failed  INTEGER DEFAULT 0,
    errors          JSONB DEFAULT '[]',
    status          VARCHAR(20) DEFAULT 'PENDING', -- PENDING, RUNNING, SUCCESS, FAILED
    started_at      TIMESTAMPTZ DEFAULT NOW(),
    completed_at    TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS admin_overrides (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    table_name      VARCHAR(50) NOT NULL,
    record_id       TEXT NOT NULL,
    field_name      VARCHAR(100) NOT NULL,
    old_value       TEXT,
    new_value       TEXT NOT NULL,
    reason          TEXT NOT NULL,
    override_at     TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    action      VARCHAR(50) NOT NULL,   -- VALUATION_RUN, DATA_IMPORT, ML_RETRAIN, etc.
    entity_type VARCHAR(50),
    entity_id   TEXT,
    details     JSONB DEFAULT '{}',
    status      VARCHAR(20) DEFAULT 'SUCCESS',
    error       TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_audit_logs_action ON audit_logs(action, created_at DESC);

CREATE TABLE IF NOT EXISTS system_jobs (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    job_name        VARCHAR(100) NOT NULL,
    status          VARCHAR(20) DEFAULT 'PENDING',
    started_at      TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ,
    duration_ms     INTEGER,
    stocks_processed INTEGER DEFAULT 0,
    errors          JSONB DEFAULT '[]',
    triggered_by    VARCHAR(20) DEFAULT 'SCHEDULER',  -- SCHEDULER, MANUAL
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_system_jobs_name ON system_jobs(job_name, created_at DESC);

-- ============================================================
-- SECTION 11: BACKTESTING
-- ============================================================

CREATE TABLE IF NOT EXISTS backtest_runs (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name            VARCHAR(200),
    description     TEXT,
    strategy        VARCHAR(50),  -- SIGNAL, SCREENER, SECTOR
    parameters      JSONB DEFAULT '{}',
    from_date       DATE NOT NULL,
    to_date         DATE NOT NULL,
    benchmark       VARCHAR(20) DEFAULT 'NIFTY50',
    status          VARCHAR(20) DEFAULT 'PENDING',
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    completed_at    TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS backtest_results (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    backtest_run_id     UUID NOT NULL REFERENCES backtest_runs(id),
    nse_symbol          VARCHAR(30),
    entry_date          DATE,
    exit_date           DATE,
    entry_price         NUMERIC(12,2),
    exit_price          NUMERIC(12,2),
    return_pct          NUMERIC(8,4),
    return_3m           NUMERIC(8,4),
    return_6m           NUMERIC(8,4),
    return_1y           NUMERIC(8,4),
    benchmark_return    NUMERIC(8,4),
    signal_at_entry     VARCHAR(30),
    fundamental_score_at_entry INTEGER,
    max_drawdown        NUMERIC(8,4),
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- SECTION 12: SEED DATA
-- ============================================================

INSERT INTO data_sources (name, source_type, base_url, notes) VALUES
    ('NSE Bhavcopy', 'NSE_BHAVCOPY', 'https://archives.nseindia.com/content/historical/EQUITIES', 'Daily EOD bhavcopy CSV'),
    ('BSE Corporate', 'BSE_XML', 'https://www.bseindia.com/corporates', 'BSE quarterly results XML'),
    ('Upstox API', 'UPSTOX', 'https://api.upstox.com/v2', 'Real-time market data'),
    ('Admin CSV Upload', 'CSV_UPLOAD', NULL, 'Manual admin uploads')
ON CONFLICT DO NOTHING;

-- Seed sector classification (NSE GICS-like hierarchy)
INSERT INTO sector_classification (macro_sector, sector, industry, basic_industry) VALUES
    ('Financial Services', 'Banks', 'Private Sector Banks', 'Large Private Banks'),
    ('Financial Services', 'Banks', 'Public Sector Banks', 'Large Public Banks'),
    ('Financial Services', 'NBFCs', 'Housing Finance', 'Large HFCs'),
    ('Financial Services', 'NBFCs', 'Consumer Finance', 'Retail NBFCs'),
    ('Financial Services', 'Insurance', 'Life Insurance', NULL),
    ('Financial Services', 'Insurance', 'Non-Life Insurance', NULL),
    ('Financial Services', 'Capital Markets', 'Broking', NULL),
    ('Information Technology', 'IT Services', 'Large Cap IT', 'Global IT Services'),
    ('Information Technology', 'IT Services', 'Mid Cap IT', 'Niche IT Services'),
    ('Information Technology', 'Software Products', 'SaaS', NULL),
    ('Consumer', 'FMCG', 'Food & Beverages', NULL),
    ('Consumer', 'FMCG', 'Personal Care', NULL),
    ('Consumer', 'FMCG', 'Household Products', NULL),
    ('Consumer', 'Retail', 'Organized Retail', NULL),
    ('Healthcare', 'Pharma', 'Domestic Formulations', NULL),
    ('Healthcare', 'Pharma', 'Export Formulations', NULL),
    ('Healthcare', 'Pharma', 'API / Bulk Drugs', NULL),
    ('Healthcare', 'Hospitals', 'Multi-Specialty Hospitals', NULL),
    ('Healthcare', 'Diagnostics', 'Diagnostic Chains', NULL),
    ('Industrials', 'Auto', 'Passenger Vehicles', NULL),
    ('Industrials', 'Auto', 'Two Wheelers', NULL),
    ('Industrials', 'Auto', 'Commercial Vehicles', NULL),
    ('Industrials', 'Auto Ancillaries', 'Auto Components', NULL),
    ('Industrials', 'Capital Goods', 'Engineering', NULL),
    ('Industrials', 'Capital Goods', 'Defence', NULL),
    ('Industrials', 'Logistics', 'Logistics & Transport', NULL),
    ('Materials', 'Metals', 'Steel', NULL),
    ('Materials', 'Metals', 'Aluminium', NULL),
    ('Materials', 'Metals', 'Copper', NULL),
    ('Materials', 'Cement', 'Cement', NULL),
    ('Materials', 'Chemicals', 'Specialty Chemicals', NULL),
    ('Materials', 'Chemicals', 'Agrochemicals', NULL),
    ('Energy', 'Oil & Gas', 'Upstream E&P', NULL),
    ('Energy', 'Oil & Gas', 'Downstream Refining', NULL),
    ('Energy', 'Oil & Gas', 'Gas Distribution', NULL),
    ('Energy', 'Power', 'Power Generation', NULL),
    ('Energy', 'Power', 'Power Transmission', NULL),
    ('Energy', 'Renewables', 'Solar & Wind', NULL),
    ('Real Estate', 'Realty', 'Residential Real Estate', NULL),
    ('Real Estate', 'Realty', 'Commercial Real Estate', NULL),
    ('Telecom', 'Telecom', 'Telecom Services', NULL),
    ('Telecom', 'Telecom Infrastructure', 'Tower Companies', NULL),
    ('Consumer Discretionary', 'Hotels & Tourism', 'Hotels & Resorts', NULL),
    ('Consumer Discretionary', 'Media', 'Broadcasting', NULL),
    ('Consumer Discretionary', 'Media', 'OTT & Digital Media', NULL)
ON CONFLICT DO NOTHING;

-- Confirm schema created
DO $$
BEGIN
    RAISE NOTICE 'StockLens schema created successfully.';
    RAISE NOTICE 'Tables: stocks, sector_classification, realtime_quotes, price_candles_1m, price_candles_daily,';
    RAISE NOTICE '        financial_results, shareholding_patterns, corporate_actions, financial_ratios,';
    RAISE NOTICE '        valuation_assumptions, valuation_runs, dcf_outputs, reverse_dcf_outputs, intrinsic_values,';
    RAISE NOTICE '        cluster_results, ml_scores, risk_flags, signals,';
    RAISE NOTICE '        watchlist, alerts, alert_history, portfolio, portfolio_holdings,';
    RAISE NOTICE '        ai_reports, news_embeddings,';
    RAISE NOTICE '        data_sources, data_import_logs, admin_overrides, audit_logs, system_jobs,';
    RAISE NOTICE '        backtest_runs, backtest_results';
END $$;
