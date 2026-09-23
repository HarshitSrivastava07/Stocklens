-- ============================================================
-- 003 — Real engines
--
-- Adds the storage the rebuilt valuation, technical and signal
-- engines need, plus the per-stock research workspace.
--
-- Everything here is idempotent: safe to re-run against a database
-- that has already been migrated.
-- ============================================================

-- ─────────────────────────────────────────────────────────────
-- Stocks: provider linkage
-- ─────────────────────────────────────────────────────────────
ALTER TABLE stocks ADD COLUMN IF NOT EXISTS exchange        VARCHAR(20) NOT NULL DEFAULT 'NSE';
ALTER TABLE stocks ADD COLUMN IF NOT EXISTS currency        VARCHAR(5)  NOT NULL DEFAULT 'INR';
ALTER TABLE stocks ADD COLUMN IF NOT EXISTS country         VARCHAR(50) NOT NULL DEFAULT 'India';
ALTER TABLE stocks ADD COLUMN IF NOT EXISTS yahoo_ticker    VARCHAR(30);
ALTER TABLE stocks ADD COLUMN IF NOT EXISTS industry        VARCHAR(120);
ALTER TABLE stocks ADD COLUMN IF NOT EXISTS business_summary TEXT;
ALTER TABLE stocks ADD COLUMN IF NOT EXISTS website         VARCHAR(255);
ALTER TABLE stocks ADD COLUMN IF NOT EXISTS employees       INTEGER;
ALTER TABLE stocks ADD COLUMN IF NOT EXISTS benchmark_ticker VARCHAR(30);

CREATE INDEX IF NOT EXISTS idx_stocks_yahoo    ON stocks(yahoo_ticker);
CREATE INDEX IF NOT EXISTS idx_stocks_exchange ON stocks(exchange);
CREATE INDEX IF NOT EXISTS idx_stocks_country  ON stocks(country);

-- ─────────────────────────────────────────────────────────────
-- Daily candles: adjusted close is what valuation must use
--
-- Raw close is what the stock actually traded at and is what a chart
-- should display. Adjusted close is corrected for splits and dividends
-- and is the only correct input to a return or beta calculation.
-- Conflating them produces a fake -50% crash on every stock that has
-- ever split.
-- ─────────────────────────────────────────────────────────────
ALTER TABLE price_candles_daily ADD COLUMN IF NOT EXISTS adj_close NUMERIC(14,4);
ALTER TABLE price_candles_daily ADD COLUMN IF NOT EXISTS fetched_at TIMESTAMPTZ DEFAULT NOW();

CREATE INDEX IF NOT EXISTS idx_daily_symbol_date_desc
    ON price_candles_daily(nse_symbol, date DESC);

-- ─────────────────────────────────────────────────────────────
-- Intrinsic values: full audit trail of how the number was produced
-- ─────────────────────────────────────────────────────────────
ALTER TABLE intrinsic_values ADD COLUMN IF NOT EXISTS confidence_score     NUMERIC(6,4);
ALTER TABLE intrinsic_values ADD COLUMN IF NOT EXISTS years_of_history     INTEGER;
ALTER TABLE intrinsic_values ADD COLUMN IF NOT EXISTS wacc                 NUMERIC(8,6);
ALTER TABLE intrinsic_values ADD COLUMN IF NOT EXISTS cost_of_equity       NUMERIC(8,6);
ALTER TABLE intrinsic_values ADD COLUMN IF NOT EXISTS cost_of_debt         NUMERIC(8,6);
ALTER TABLE intrinsic_values ADD COLUMN IF NOT EXISTS beta                 NUMERIC(8,4);
ALTER TABLE intrinsic_values ADD COLUMN IF NOT EXISTS beta_source          VARCHAR(20);
ALTER TABLE intrinsic_values ADD COLUMN IF NOT EXISTS terminal_value_share NUMERIC(8,6);
ALTER TABLE intrinsic_values ADD COLUMN IF NOT EXISTS assumptions          JSONB DEFAULT '{}'::jsonb;
ALTER TABLE intrinsic_values ADD COLUMN IF NOT EXISTS projection           JSONB DEFAULT '[]'::jsonb;
ALTER TABLE intrinsic_values ADD COLUMN IF NOT EXISTS models               JSONB DEFAULT '[]'::jsonb;
ALTER TABLE intrinsic_values ADD COLUMN IF NOT EXISTS history_profile      JSONB DEFAULT '{}'::jsonb;
ALTER TABLE intrinsic_values ADD COLUMN IF NOT EXISTS warnings             JSONB DEFAULT '[]'::jsonb;
ALTER TABLE intrinsic_values ADD COLUMN IF NOT EXISTS reverse_dcf          JSONB DEFAULT '{}'::jsonb;
ALTER TABLE intrinsic_values ADD COLUMN IF NOT EXISTS computed_at          TIMESTAMPTZ DEFAULT NOW();
ALTER TABLE intrinsic_values ADD COLUMN IF NOT EXISTS data_source          VARCHAR(30) DEFAULT 'YAHOO';

-- ─────────────────────────────────────────────────────────────
-- Signals: the actionable trade plan
-- ─────────────────────────────────────────────────────────────
ALTER TABLE signals ADD COLUMN IF NOT EXISTS action            VARCHAR(24);
ALTER TABLE signals ADD COLUMN IF NOT EXISTS conviction        NUMERIC(6,4);
ALTER TABLE signals ADD COLUMN IF NOT EXISTS headline          TEXT;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS rationale         JSONB DEFAULT '[]'::jsonb;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS invalidation      JSONB DEFAULT '[]'::jsonb;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS value_score       NUMERIC(6,2);
ALTER TABLE signals ADD COLUMN IF NOT EXISTS quality_score     NUMERIC(6,2);
ALTER TABLE signals ADD COLUMN IF NOT EXISTS momentum_score    NUMERIC(6,2);
ALTER TABLE signals ADD COLUMN IF NOT EXISTS composite_score   NUMERIC(6,2);
ALTER TABLE signals ADD COLUMN IF NOT EXISTS entry_low         NUMERIC(14,4);
ALTER TABLE signals ADD COLUMN IF NOT EXISTS entry_high        NUMERIC(14,4);
ALTER TABLE signals ADD COLUMN IF NOT EXISTS max_buy_price     NUMERIC(14,4);
ALTER TABLE signals ADD COLUMN IF NOT EXISTS stop_loss         NUMERIC(14,4);
ALTER TABLE signals ADD COLUMN IF NOT EXISTS target_1          NUMERIC(14,4);
ALTER TABLE signals ADD COLUMN IF NOT EXISTS target_2          NUMERIC(14,4);
ALTER TABLE signals ADD COLUMN IF NOT EXISTS risk_reward       NUMERIC(8,3);
ALTER TABLE signals ADD COLUMN IF NOT EXISTS position_size_pct NUMERIC(6,2);
ALTER TABLE signals ADD COLUMN IF NOT EXISTS horizon           VARCHAR(40);
ALTER TABLE signals ADD COLUMN IF NOT EXISTS trend             VARCHAR(24);
ALTER TABLE signals ADD COLUMN IF NOT EXISTS intrinsic_value   NUMERIC(14,4);
ALTER TABLE signals ADD COLUMN IF NOT EXISTS current_price     NUMERIC(14,4);

CREATE INDEX IF NOT EXISTS idx_signals_action    ON signals(action);
CREATE INDEX IF NOT EXISTS idx_signals_composite ON signals(composite_score DESC);

-- ─────────────────────────────────────────────────────────────
-- Technical snapshots
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS technical_snapshots (
    nse_symbol          VARCHAR(30) PRIMARY KEY REFERENCES stocks(nse_symbol) ON DELETE CASCADE,
    as_of               DATE,
    price               NUMERIC(14,4),
    sma_20              NUMERIC(14,4),
    sma_50              NUMERIC(14,4),
    sma_200             NUMERIC(14,4),
    ema_12              NUMERIC(14,4),
    ema_26              NUMERIC(14,4),
    rsi_14              NUMERIC(8,4),
    macd_line           NUMERIC(14,6),
    macd_signal         NUMERIC(14,6),
    macd_histogram      NUMERIC(14,6),
    atr_14              NUMERIC(14,4),
    atr_pct             NUMERIC(8,6),
    bollinger_percent_b NUMERIC(8,6),
    week_52_high        NUMERIC(14,4),
    week_52_low         NUMERIC(14,4),
    pct_from_52w_high   NUMERIC(8,6),
    pct_from_52w_low    NUMERIC(8,6),
    return_1m           NUMERIC(10,6),
    return_3m           NUMERIC(10,6),
    return_6m           NUMERIC(10,6),
    return_1y           NUMERIC(10,6),
    return_3y_cagr      NUMERIC(10,6),
    return_5y_cagr      NUMERIC(10,6),
    volatility_1y       NUMERIC(10,6),
    max_drawdown_5y     NUMERIC(10,6),
    avg_volume_20d      NUMERIC(20,2),
    volume_ratio        NUMERIC(10,4),
    beta                NUMERIC(8,4),
    trend               VARCHAR(24),
    candles_used        INTEGER,
    warnings            JSONB DEFAULT '[]'::jsonb,
    computed_at         TIMESTAMPTZ DEFAULT NOW()
);

-- ─────────────────────────────────────────────────────────────
-- Ingest runs — so a failed overnight job can be diagnosed
-- without re-running it
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ingest_runs (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job           VARCHAR(50) NOT NULL,
    status        VARCHAR(20) NOT NULL DEFAULT 'RUNNING',
    requested     INTEGER DEFAULT 0,
    succeeded     INTEGER DEFAULT 0,
    failed        INTEGER DEFAULT 0,
    skipped       INTEGER DEFAULT 0,
    rows_written  INTEGER DEFAULT 0,
    errors        JSONB DEFAULT '{}'::jsonb,
    started_at    TIMESTAMPTZ DEFAULT NOW(),
    finished_at   TIMESTAMPTZ,
    duration_seconds NUMERIC(12,3)
);

CREATE INDEX IF NOT EXISTS idx_ingest_runs_job ON ingest_runs(job, started_at DESC);

-- ─────────────────────────────────────────────────────────────
-- Research workspace — a user's own notes and thesis on a stock
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS research_notes (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    nse_symbol    VARCHAR(30) NOT NULL REFERENCES stocks(nse_symbol) ON DELETE CASCADE,
    user_id       VARCHAR(100) NOT NULL DEFAULT 'default',
    title         VARCHAR(200),
    body          TEXT NOT NULL,
    tags          JSONB DEFAULT '[]'::jsonb,
    -- What the user believed at the time, so a thesis can be reviewed
    -- against what actually happened rather than rewritten after the fact.
    thesis_stance VARCHAR(20),          -- BULLISH, BEARISH, NEUTRAL
    price_at_note NUMERIC(14,4),
    iv_at_note    NUMERIC(14,4),
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    updated_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_research_symbol
    ON research_notes(nse_symbol, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_research_user
    ON research_notes(user_id, created_at DESC);

-- ─────────────────────────────────────────────────────────────
-- Market assumptions — operator-configurable, never company-specific
--
-- Risk-free rate and equity risk premium are genuine market inputs. They
-- live in a table so a desk can set its own house view and every
-- valuation records which values it used.
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS market_assumptions (
    country              VARCHAR(50) PRIMARY KEY,
    currency             VARCHAR(5) NOT NULL,
    risk_free_rate       NUMERIC(8,6) NOT NULL,
    equity_risk_premium  NUMERIC(8,6) NOT NULL,
    default_beta         NUMERIC(6,4) NOT NULL DEFAULT 1.0,
    benchmark_ticker     VARCHAR(30),
    updated_at           TIMESTAMPTZ DEFAULT NOW()
);

INSERT INTO market_assumptions
    (country, currency, risk_free_rate, equity_risk_premium, default_beta, benchmark_ticker)
VALUES
    ('India',          'INR', 0.070, 0.058, 1.0, '^NSEI'),
    ('United States',  'USD', 0.042, 0.050, 1.0, '^GSPC'),
    ('United Kingdom', 'GBP', 0.040, 0.055, 1.0, '^FTSE'),
    ('Japan',          'JPY', 0.010, 0.055, 1.0, '^N225')
ON CONFLICT (country) DO NOTHING;

-- ─────────────────────────────────────────────────────────────
-- Financial results: lines the valuation engine needs that the
-- original schema did not carry
--
--   depreciation      — separates EBITDA from EBIT
--   interest_expense  — gives the company's actual cost of debt
--   invested_capital  — drives the sales-to-capital reinvestment ratio
--   working_capital   — cross-check on the same
-- ─────────────────────────────────────────────────────────────
ALTER TABLE financial_results ADD COLUMN IF NOT EXISTS depreciation     NUMERIC(20,2);
ALTER TABLE financial_results ADD COLUMN IF NOT EXISTS interest_expense NUMERIC(20,2);
ALTER TABLE financial_results ADD COLUMN IF NOT EXISTS invested_capital NUMERIC(20,2);
ALTER TABLE financial_results ADD COLUMN IF NOT EXISTS working_capital  NUMERIC(20,2);
ALTER TABLE financial_results ADD COLUMN IF NOT EXISTS currency         VARCHAR(5);
ALTER TABLE financial_results ADD COLUMN IF NOT EXISTS fetched_at       TIMESTAMPTZ DEFAULT NOW();

CREATE INDEX IF NOT EXISTS idx_financials_symbol_annual
    ON financial_results(nse_symbol, period_end DESC)
    WHERE period_type = 'A';
