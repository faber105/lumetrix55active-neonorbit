-- Run this once only when upgrading an EXISTING database from tradepocketbotsees.
ALTER TABLE users ADD COLUMN IF NOT EXISTS full_name VARCHAR(128);
ALTER TABLE users ADD COLUMN IF NOT EXISTS verification_status VARCHAR(20) DEFAULT 'NEW' NOT NULL;
ALTER TABLE users ADD COLUMN IF NOT EXISTS click_time TIMESTAMP;
ALTER TABLE users ADD COLUMN IF NOT EXISTS pending_time TIMESTAMP;
ALTER TABLE users ADD COLUMN IF NOT EXISTS verified_time TIMESTAMP;
ALTER TABLE users ADD COLUMN IF NOT EXISTS attempts_count INTEGER DEFAULT 0 NOT NULL;

ALTER TABLE signals ADD COLUMN IF NOT EXISTS strategy VARCHAR(40) DEFAULT 'unknown' NOT NULL;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS market_regime VARCHAR(20) DEFAULT 'unknown' NOT NULL;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS data_source VARCHAR(40) DEFAULT 'pocketoption_otc' NOT NULL;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS feature_snapshot JSONB;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS entry_time TIMESTAMP;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS requested_by_user_id BIGINT REFERENCES users(id) ON DELETE SET NULL;
UPDATE signals SET entry_time = created_at WHERE entry_time IS NULL;
ALTER TABLE signals ALTER COLUMN entry_time SET NOT NULL;
CREATE INDEX IF NOT EXISTS ix_signals_requested_by_user_id ON signals (requested_by_user_id);


-- Widen checks from the original schema for unified OTC mode.
ALTER TABLE signals DROP CONSTRAINT IF EXISTS signals_asset_category_check;
ALTER TABLE signals ADD CONSTRAINT signals_asset_category_check CHECK (asset_category IN ('otc','forex','crypto','stocks','commodities','indices'));
ALTER TABLE signals DROP CONSTRAINT IF EXISTS signals_result_check;
ALTER TABLE signals ADD CONSTRAINT signals_result_check CHECK (result IN ('WIN','LOSS','DRAW','PENDING'));
ALTER TABLE payments DROP CONSTRAINT IF EXISTS payments_status_check;
ALTER TABLE payments ADD CONSTRAINT payments_status_check CHECK (status IN ('pending','review','paid','rejected','cancelled','failed'));
