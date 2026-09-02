-- Migration 002: pgvector stock embeddings table
-- Run after: 001_initial.sql
-- Requires: CREATE EXTENSION IF NOT EXISTS vector;

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS stock_embeddings (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    nse_symbol      VARCHAR(30) NOT NULL REFERENCES stocks(nse_symbol) ON DELETE CASCADE,
    content_type    VARCHAR(50) NOT NULL,  -- 'ai_summary', 'fundamentals', 'sector_context'
    text_content    TEXT,
    embedding       vector(768),
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (nse_symbol, content_type)
);

-- HNSW index for fast approximate nearest-neighbor search
CREATE INDEX IF NOT EXISTS idx_stock_embeddings_vector
    ON stock_embeddings USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

CREATE INDEX IF NOT EXISTS idx_stock_embeddings_symbol
    ON stock_embeddings(nse_symbol);

-- Bhavcopy / NSE EOD staging table
CREATE TABLE IF NOT EXISTS nse_bhavcopy_staging (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trade_date      DATE NOT NULL,
    nse_symbol      VARCHAR(30),
    isin            VARCHAR(20),
    series          VARCHAR(5),
    open            NUMERIC(14,4),
    high            NUMERIC(14,4),
    low             NUMERIC(14,4),
    close           NUMERIC(14,4),
    prev_close      NUMERIC(14,4),
    tottrdqty       BIGINT,
    tottrdval       NUMERIC(20,4),
    totaltrades     BIGINT,
    imported_at     TIMESTAMPTZ DEFAULT NOW(),
    processed       BOOLEAN DEFAULT FALSE
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_bhavcopy_unique
    ON nse_bhavcopy_staging(trade_date, nse_symbol, series);

-- F&O OI history
CREATE TABLE IF NOT EXISTS fo_oi_history (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    nse_symbol      VARCHAR(30) NOT NULL,
    expiry_date     DATE NOT NULL,
    option_type     VARCHAR(2) NOT NULL,  -- CE / PE / FUT
    strike_price    NUMERIC(10,2),
    open_interest   BIGINT,
    change_oi       BIGINT,
    recorded_at     TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_fo_oi_symbol_date
    ON fo_oi_history(nse_symbol, recorded_at DESC);

-- Peer cluster mapping
CREATE TABLE IF NOT EXISTS peer_groups (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    group_name      VARCHAR(100),
    nse_symbols     TEXT[],  -- array of NSE symbols
    sector_id       UUID REFERENCES sector_classification(id) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Audit: track all admin override changes
ALTER TABLE admin_overrides
    ADD COLUMN IF NOT EXISTS applied_to_valuation BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ;

COMMENT ON TABLE stock_embeddings IS 'Stores pgvector embeddings for RAG-enhanced AI summaries';
COMMENT ON TABLE fo_oi_history IS 'Historical F&O open interest data';
COMMENT ON TABLE peer_groups IS 'Manual or ML-derived peer groups for comparison';
