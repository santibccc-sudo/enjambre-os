"""SQLite storage: one file, WAL mode, explicit schema version."""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS processes (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    host       TEXT NOT NULL DEFAULT '',
    kind       TEXT NOT NULL DEFAULT 'agent',
    task       TEXT NOT NULL DEFAULT '',
    detail     TEXT NOT NULL DEFAULT '',
    period_s   INTEGER NOT NULL DEFAULT 0,
    started_at REAL NOT NULL,
    last_beat  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS leases (
    resource   TEXT PRIMARY KEY,
    holder     TEXT NOT NULL,
    purpose    TEXT NOT NULL DEFAULT '',
    since      REAL NOT NULL,
    expires_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id                  TEXT PRIMARY KEY,
    title               TEXT NOT NULL,
    detail              TEXT NOT NULL DEFAULT '',
    priority            INTEGER NOT NULL DEFAULT 5 CHECK (priority BETWEEN 1 AND 9),
    status              TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'running', 'done', 'failed', 'dead', 'cancelled')),
    creator             TEXT NOT NULL DEFAULT '',
    agent               TEXT NOT NULL DEFAULT '',
    assignee            TEXT NOT NULL DEFAULT '',
    result              TEXT NOT NULL DEFAULT '',
    error_code          TEXT NOT NULL DEFAULT '',
    attempts            INTEGER NOT NULL DEFAULT 0,
    max_attempts        INTEGER NOT NULL DEFAULT 2,
    lease_expires       REAL,
    idempotency_key     TEXT,
    proof               TEXT NOT NULL DEFAULT '',
    operation           TEXT NOT NULL DEFAULT '',
    project             TEXT NOT NULL DEFAULT '',
    verification        TEXT NOT NULL DEFAULT 'n/a',
    verification_detail TEXT NOT NULL DEFAULT '',
    cancel_requested    INTEGER NOT NULL DEFAULT 0,
    created_at          REAL NOT NULL,
    updated_at          REAL NOT NULL,
    started_at          REAL,
    finished_at         REAL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_tasks_idempotency
    ON tasks(idempotency_key) WHERE idempotency_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_tasks_queue ON tasks(status, priority, created_at);

CREATE TABLE IF NOT EXISTS task_deps (
    task_id    TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    depends_on TEXT NOT NULL REFERENCES tasks(id),
    PRIMARY KEY (task_id, depends_on)
);
CREATE INDEX IF NOT EXISTS idx_task_deps_upstream ON task_deps(depends_on);

CREATE TABLE IF NOT EXISTS traces (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    event   TEXT NOT NULL,
    actor   TEXT NOT NULL DEFAULT '',
    detail  TEXT NOT NULL DEFAULT '',
    at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_traces_task ON traces(task_id, id);

CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     TEXT NOT NULL,
    worker      TEXT NOT NULL,
    status      TEXT NOT NULL,
    error_code  TEXT NOT NULL DEFAULT '',
    duration_ms INTEGER NOT NULL DEFAULT 0,
    at          REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_runs_worker ON runs(worker, at);
"""


class SchemaTooNew(RuntimeError):
    pass


def connect(path: str | Path) -> sqlite3.Connection:
    """Autocommit connection; callers open explicit transactions when they write."""
    conn = sqlite3.connect(str(path), timeout=30, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init(path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    try:
        conn.executescript(SCHEMA)
        row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        if row is None:
            conn.execute("INSERT INTO meta (key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
        elif int(row["value"]) > SCHEMA_VERSION:
            raise SchemaTooNew(
                f"{path} uses schema v{row['value']}; this enjambre only knows v{SCHEMA_VERSION}"
            )
    finally:
        conn.close()
