-- Idempotent, additive worker protocol migration. No existing data is dropped.
CREATE TABLE IF NOT EXISTS broker_accounts (
  id BIGSERIAL PRIMARY KEY,
  owner_telegram_id BIGINT NOT NULL,
  mode VARCHAR(8) NOT NULL,
  credential_ref VARCHAR(160) NOT NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(owner_telegram_id, mode)
);

CREATE TABLE IF NOT EXISTS workers (
  id VARCHAR(128) PRIMARY KEY,
  hostname VARCHAR(128) NOT NULL,
  version VARCHAR(64) NOT NULL,
  heartbeat_at TIMESTAMP NOT NULL,
  status VARCHAR(20) NOT NULL,
  capabilities JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS worker_leases (
  account_id BIGINT PRIMARY KEY,
  worker_id VARCHAR(128) NOT NULL,
  lease_until TIMESTAMP NOT NULL,
  generation BIGINT NOT NULL DEFAULT 1,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS worker_commands (
  id BIGSERIAL PRIMARY KEY,
  account_id BIGINT NOT NULL,
  type VARCHAR(32) NOT NULL,
  payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  claimed_at TIMESTAMP,
  completed_at TIMESTAMP,
  claimed_by VARCHAR(128),
  result JSONB,
  error VARCHAR(256),
  idempotency_key VARCHAR(128) NOT NULL UNIQUE
);

CREATE INDEX IF NOT EXISTS ix_worker_commands_pending
  ON worker_commands (account_id, status, created_at);
ALTER TABLE auto_trade_sessions ADD COLUMN IF NOT EXISTS account_id BIGINT;
ALTER TABLE auto_trade_sessions ADD COLUMN IF NOT EXISTS version BIGINT NOT NULL DEFAULT 0;
ALTER TABLE auto_trade_legs ADD COLUMN IF NOT EXISTS broker_order_id VARCHAR(128);
ALTER TABLE auto_trade_legs ADD COLUMN IF NOT EXISTS idempotency_key VARCHAR(128);
CREATE UNIQUE INDEX IF NOT EXISTS ux_auto_trade_legs_idempotency
  ON auto_trade_legs (idempotency_key) WHERE idempotency_key IS NOT NULL;
ALTER TABLE auto_trade_events ADD COLUMN IF NOT EXISTS event_id VARCHAR(64);
ALTER TABLE auto_trade_events ADD COLUMN IF NOT EXISTS sequence BIGINT;
ALTER TABLE auto_trade_events ADD COLUMN IF NOT EXISTS source_ts TIMESTAMP;
CREATE UNIQUE INDEX IF NOT EXISTS ux_auto_trade_events_sequence
  ON auto_trade_events (session_id, sequence) WHERE sequence IS NOT NULL;
