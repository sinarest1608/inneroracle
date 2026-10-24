"""SQLite store for push notification state (`state/push.db`).

Kept apart from `verses.db`, which is built on the Mac and copied over with scp:
nothing that the server writes may live in a file that a data copy can replace.
One connection per operation (WAL + busy_timeout), so requests, the scheduler
and worker threads never share a connection.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from app.config import settings

# Every table uses AUTOINCREMENT so ids (device numbers above all) are never reused.
_SCHEMA = """
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
"""


def utcnow() -> str:
    """UTC timestamp as a fixed-format ISO string (sorts lexicographically)."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(path: str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or settings.push_db_path, timeout=5.0,
                           isolation_level=None)  # autocommit; explicit BEGIN for writes
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def db(path: str | None = None) -> Iterator[sqlite3.Connection]:
    conn = connect(path)
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def write_tx(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """BEGIN IMMEDIATE … COMMIT, rolling back on any error."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def migrate(path: str | None = None) -> None:
    p = Path(path or settings.push_db_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with db(str(p)) as conn:
        conn.executescript(_SCHEMA)


def audit(conn: sqlite3.Connection, action: str, detail: str = "") -> None:
    conn.execute(
        "INSERT INTO admin_actions(at, action, detail) VALUES (?, ?, ?)",
        (utcnow(), action, detail),
    )
