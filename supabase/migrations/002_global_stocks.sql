-- Migration: Add global exchange support to stocks table
-- Run this against your PostgreSQL database before seeding global stocks.
-- Safe to run multiple times (uses IF NOT EXISTS / DO NOTHING patterns).

-- 1. Remove unique constraint on isin (global stocks may share or lack ISINs)
ALTER TABLE stocks DROP CONSTRAINT IF EXISTS stocks_isin_key;

-- 2. Add global exchange columns
ALTER TABLE stocks ADD COLUMN IF NOT EXISTS exchange   VARCHAR(20) NOT NULL DEFAULT 'NSE';
ALTER TABLE stocks ADD COLUMN IF NOT EXISTS currency   VARCHAR(5)  NOT NULL DEFAULT 'INR';
ALTER TABLE stocks ADD COLUMN IF NOT EXISTS yahoo_ticker VARCHAR(30);
ALTER TABLE stocks ADD COLUMN IF NOT EXISTS country    VARCHAR(50) NOT NULL DEFAULT 'India';

-- 3. Indexes for fast filtering
CREATE INDEX IF NOT EXISTS idx_stocks_exchange ON stocks(exchange);
CREATE INDEX IF NOT EXISTS idx_stocks_country  ON stocks(country);
CREATE INDEX IF NOT EXISTS idx_stocks_yahoo    ON stocks(yahoo_ticker);

-- 4. Back-fill existing NSE rows
UPDATE stocks SET exchange = 'NSE', currency = 'INR', country = 'India'
WHERE exchange = 'NSE' OR exchange IS NULL;

-- Done
SELECT COUNT(*) AS total_stocks,
       COUNT(CASE WHEN exchange = 'NSE' THEN 1 END) AS nse,
       COUNT(CASE WHEN exchange != 'NSE' THEN 1 END) AS global
FROM stocks;
