"""Open the database and bring its schema up to date."""

from __future__ import annotations

import sqlite3
from pathlib import Path

# Append new steps; never edit a step that has shipped.
MIGRATIONS: tuple[str, ...] = (
    """
    CREATE TABLE runs (
        id INTEGER PRIMARY KEY,
        started_at TEXT NOT NULL,
        finished_at TEXT,
        status TEXT NOT NULL DEFAULT 'running'
    );
    CREATE TABLE searches (
        id INTEGER PRIMARY KEY,
        run_id INTEGER REFERENCES runs(id),
        source TEXT NOT NULL,
        group_id TEXT NOT NULL,
        direction TEXT NOT NULL,
        origins TEXT NOT NULL,
        destinations TEXT NOT NULL,
        travel_date TEXT NOT NULL,
        status TEXT NOT NULL,
        result_count INTEGER NOT NULL DEFAULT 0,
        error TEXT,
        latency_ms INTEGER,
        searched_at TEXT NOT NULL
    );
    CREATE INDEX searches_key ON searches
        (source, group_id, direction, origins, destinations, travel_date, searched_at);
    CREATE TABLE offers (
        id INTEGER PRIMARY KEY,
        search_id INTEGER NOT NULL REFERENCES searches(id),
        source TEXT NOT NULL,
        group_id TEXT NOT NULL,
        direction TEXT NOT NULL,
        segments_json TEXT NOT NULL,
        price_minor INTEGER NOT NULL,
        currency TEXT NOT NULL,
        total_minutes INTEGER NOT NULL,
        self_transfer INTEGER NOT NULL,
        booking_url TEXT
    );
    CREATE INDEX offers_search ON offers (search_id);
    CREATE TABLE fx_rates (
        as_of TEXT NOT NULL,
        currency TEXT NOT NULL,
        per_eur TEXT NOT NULL,
        PRIMARY KEY (as_of, currency)
    );
    CREATE TABLE snapshots (
        id INTEGER PRIMARY KEY,
        run_id INTEGER REFERENCES runs(id),
        window_id TEXT NOT NULL,
        plan TEXT NOT NULL,
        entry TEXT NOT NULL,
        exit TEXT NOT NULL,
        total_minor INTEGER NOT NULL,
        score_minor INTEGER NOT NULL,
        taken_at TEXT NOT NULL
    );
    CREATE INDEX snapshots_window ON snapshots (window_id, plan, taken_at);
    CREATE TABLE alerts (
        id INTEGER PRIMARY KEY,
        kind TEXT NOT NULL,
        window_id TEXT NOT NULL,
        total_minor INTEGER NOT NULL,
        sent_at TEXT NOT NULL
    );
    CREATE TABLE summaries (
        id INTEGER PRIMARY KEY,
        week TEXT NOT NULL UNIQUE,
        totals_json TEXT NOT NULL,
        sent_at TEXT NOT NULL
    );
    CREATE TABLE todos (
        id INTEGER PRIMARY KEY,
        text TEXT NOT NULL,
        added_by TEXT NOT NULL,
        added_at TEXT NOT NULL,
        done_by TEXT,
        done_at TEXT
    );
    CREATE TABLE settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """,
)


LOCK_TIMEOUT_S = 30


def connect(path: Path | str) -> sqlite3.Connection:
    """Open the database (":memory:" for tests) and apply pending migrations.

    The bot and the nightly run share the file, so it runs in WAL mode and waits for locks.
    """
    if isinstance(path, Path):
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=LOCK_TIMEOUT_S)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if isinstance(path, Path):
        conn.execute("PRAGMA journal_mode = WAL")
    migrate(conn)
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    (version,) = conn.execute("PRAGMA user_version").fetchone()
    for number, script in enumerate(MIGRATIONS[version:], start=version + 1):
        with conn:
            conn.executescript(script)
            conn.execute(f"PRAGMA user_version = {number}")
