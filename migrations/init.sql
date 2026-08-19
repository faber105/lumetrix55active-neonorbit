CREATE TABLE IF NOT EXISTS users (
    id BIGINT PRIMARY KEY,
    username VARCHAR(64),
    first_name VARCHAR(64),
    full_name VARCHAR(128),
    language_code VARCHAR(8),
    created_at TIMESTAMP DEFAULT NOW() NOT NULL,
    is_banned BOOLEAN DEFAULT false NOT NULL,
    verification_status VARCHAR(20) DEFAULT 'NEW' NOT NULL CHECK (verification_status IN ('NEW','CLICKED','PENDING','VERIFIED','BLOCKED')),
    click_time TIMESTAMP,
    pending_time TIMESTAMP,
    verified_time TIMESTAMP,
    attempts_count INTEGER DEFAULT 0 NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_users_verification_status ON users (verification_status);

CREATE TABLE IF NOT EXISTS subscriptions (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    plan VARCHAR(10) NOT NULL CHECK (plan IN ('week', 'month', 'year')),
    started_at TIMESTAMP NOT NULL,
    expires_at TIMESTAMP NOT NULL,
    is_active BOOLEAN DEFAULT true NOT NULL,
    payment_id VARCHAR(128),
    created_at TIMESTAMP DEFAULT NOW() NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_subscriptions_user_active ON subscriptions (user_id, is_active, expires_at);

CREATE TABLE IF NOT EXISTS signals (
    id SERIAL PRIMARY KEY,
    asset VARCHAR(40) NOT NULL,
    asset_category VARCHAR(20) DEFAULT 'otc' NOT NULL CHECK (asset_category IN ('otc','forex','crypto','stocks','commodities','indices')),
    direction VARCHAR(4) NOT NULL CHECK (direction IN ('CALL','PUT')),
    timeframe VARCHAR(5) NOT NULL CHECK (timeframe IN ('1m','3m','5m')),
    duration_sec INTEGER NOT NULL,
    open_price DECIMAL(18,8),
    close_price DECIMAL(18,8),
    confidence FLOAT NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    indicator_score FLOAT NOT NULL CHECK (indicator_score >= -1 AND indicator_score <= 1),
    ml_confidence FLOAT NOT NULL CHECK (ml_confidence >= 0 AND ml_confidence <= 1),
    strategy VARCHAR(40) DEFAULT 'unknown' NOT NULL,
    market_regime VARCHAR(20) DEFAULT 'unknown' NOT NULL,
    data_source VARCHAR(40) DEFAULT 'pocketoption_otc' NOT NULL,
    feature_snapshot JSONB,
    created_at TIMESTAMP DEFAULT NOW() NOT NULL,
    entry_time TIMESTAMP NOT NULL,
    expires_at TIMESTAMP NOT NULL,
    result VARCHAR(10) DEFAULT 'PENDING' NOT NULL CHECK (result IN ('WIN','LOSS','DRAW','PENDING')),
    agent_id VARCHAR(32) DEFAULT 'otc_scanner' NOT NULL,
    requested_by_user_id BIGINT REFERENCES users(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS ix_signals_active ON signals (expires_at, result, timeframe, asset_category);
CREATE INDEX IF NOT EXISTS ix_signals_created_at ON signals (created_at DESC);
CREATE INDEX IF NOT EXISTS ix_signals_strategy ON signals (strategy);
CREATE INDEX IF NOT EXISTS ix_signals_requested_by_user_id ON signals (requested_by_user_id);

CREATE TABLE IF NOT EXISTS sessions (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    goal_amount DECIMAL(10,2) NOT NULL CHECK (goal_amount > 0),
    trade_amount DECIMAL(10,2) NOT NULL CHECK (trade_amount > 0),
    timeframe_filter VARCHAR(5) CHECK (timeframe_filter IN ('1m','3m','5m')),
    started_at TIMESTAMP DEFAULT NOW() NOT NULL,
    ended_at TIMESTAMP,
    status VARCHAR(10) DEFAULT 'active' NOT NULL CHECK (status IN ('active','completed','cancelled')),
    total_trades INTEGER DEFAULT 0 NOT NULL CHECK (total_trades >= 0),
    wins INTEGER DEFAULT 0 NOT NULL CHECK (wins >= 0),
    losses INTEGER DEFAULT 0 NOT NULL CHECK (losses >= 0),
    pnl DECIMAL(10,2) DEFAULT 0 NOT NULL,
    goal_reached BOOLEAN DEFAULT false NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_sessions_user_status ON sessions (user_id, status, started_at DESC);

CREATE TABLE IF NOT EXISTS session_trades (
    id SERIAL PRIMARY KEY,
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    signal_id INTEGER NOT NULL REFERENCES signals(id) ON DELETE CASCADE,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    result VARCHAR(4) NOT NULL CHECK (result IN ('WIN','LOSS')),
    trade_amount DECIMAL(10,2) NOT NULL CHECK (trade_amount > 0),
    pnl DECIMAL(10,2) NOT NULL,
    marked_at TIMESTAMP DEFAULT NOW() NOT NULL,
    UNIQUE (session_id, signal_id)
);
CREATE INDEX IF NOT EXISTS ix_session_trades_user_marked ON session_trades (user_id, marked_at DESC);

CREATE TABLE IF NOT EXISTS payments (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    plan VARCHAR(10) NOT NULL CHECK (plan IN ('week','month','year')),
    amount DECIMAL(10,2) NOT NULL CHECK (amount > 0),
    currency VARCHAR(5) NOT NULL,
    status VARCHAR(20) DEFAULT 'pending' NOT NULL CHECK (status IN ('pending','review','paid','rejected','cancelled','failed')),
    provider VARCHAR(20) NOT NULL CHECK (provider IN ('stars','crypto')),
    provider_payment_id VARCHAR(128),
    created_at TIMESTAMP DEFAULT NOW() NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_payments_user_status ON payments (user_id, status, created_at DESC);

CREATE TABLE IF NOT EXISTS channel_join_requests (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    username VARCHAR(64),
    status VARCHAR(20) DEFAULT 'pending' NOT NULL CHECK (status IN ('pending','approved','rejected')),
    requested_at TIMESTAMP DEFAULT NOW() NOT NULL,
    reviewed_at TIMESTAMP,
    reviewed_by BIGINT,
    note TEXT
);
CREATE INDEX IF NOT EXISTS ix_channel_join_requests_status ON channel_join_requests (status, requested_at DESC);

CREATE TABLE IF NOT EXISTS app_settings (
    key VARCHAR(64) PRIMARY KEY,
    value VARCHAR(512) NOT NULL,
    updated_at TIMESTAMP DEFAULT NOW() NOT NULL
);

CREATE TABLE IF NOT EXISTS ml_state (
    id INTEGER PRIMARY KEY,
    payload BYTEA NOT NULL,
    samples INTEGER DEFAULT 0 NOT NULL,
    updated_at TIMESTAMP DEFAULT NOW() NOT NULL
);
