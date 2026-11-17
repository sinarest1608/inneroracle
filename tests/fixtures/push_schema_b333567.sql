-- push.db schema as deployed in production at b333567 (before the inbox, stats,
-- festival and release tables). Used by test_upgrade_keeps_every_existing_device.

CREATE TABLE IF NOT EXISTS push_subscriptions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    endpoint        TEXT NOT NULL UNIQUE,
    p256dh          TEXT NOT NULL,
    auth            TEXT NOT NULL,
    secret_hash     TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    last_success_at TEXT,
    last_check_at   TEXT,
    failure_count   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_subs_created ON push_subscriptions(created_at);

CREATE TABLE IF NOT EXISTS push_daily_runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date     TEXT NOT NULL UNIQUE,
    verse_id     TEXT,
    status       TEXT NOT NULL,
    forced       INTEGER NOT NULL DEFAULT 0,
    started_at   TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL,
    completed_at TEXT,
    delivered    INTEGER NOT NULL DEFAULT 0,
    failed       INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS daily_quotes (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    quote_date     TEXT NOT NULL UNIQUE,
    verse_id       TEXT NOT NULL,
    speaker        TEXT,
    speaker_source TEXT NOT NULL,
    quote          TEXT NOT NULL,
    quote_source   TEXT NOT NULL,
    model          TEXT,
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS push_deliveries (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    sent_at        TEXT NOT NULL,
    kind           TEXT NOT NULL,
    run_date       TEXT,
    verse_id       TEXT,
    speaker        TEXT,
    speaker_source TEXT,
    quote_source   TEXT,
    title          TEXT,
    body           TEXT,
    url            TEXT,
    device_id      INTEGER,
    status         TEXT NOT NULL,
    http_status    INTEGER,
    error_type     TEXT
);
CREATE INDEX IF NOT EXISTS idx_deliveries_sent ON push_deliveries(sent_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_deliveries_daily_claim
    ON push_deliveries(run_date, device_id) WHERE kind = 'daily';

CREATE TABLE IF NOT EXISTS verse_rotation (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    position INTEGER NOT NULL UNIQUE,
    verse_id TEXT NOT NULL UNIQUE,
    added_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS signup_attempts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    at          TEXT NOT NULL,
    ip          TEXT,
    ip_hash     TEXT NOT NULL,
    bucket_hash TEXT NOT NULL,
    wide_hash   TEXT,
    result      TEXT NOT NULL,
    charged     INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_attempts_bucket ON signup_attempts(bucket_hash, at);
CREATE INDEX IF NOT EXISTS idx_attempts_wide ON signup_attempts(wide_hash, at);
CREATE INDEX IF NOT EXISTS idx_attempts_at ON signup_attempts(at);

CREATE TABLE IF NOT EXISTS signup_rejections (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    hour        TEXT NOT NULL,
    bucket_hash TEXT NOT NULL,
    result      TEXT NOT NULL,
    count       INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_rejections_key
    ON signup_rejections(hour, bucket_hash, result);

CREATE TABLE IF NOT EXISTS admin_actions (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    at     TEXT NOT NULL,
    action TEXT NOT NULL,
    detail TEXT
);
