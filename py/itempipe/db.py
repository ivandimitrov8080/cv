"""Connection, schema migration and transaction helpers.

One process may have several connections (one per asyncio task is safest for
SQLite), and several processes may share the file.  WAL + `busy_timeout` +
short `BEGIN IMMEDIATE` transactions is the whole concurrency story.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def now() -> str:
    """UTC 'YYYY-MM-DD HH:MM:SS', the shape SQLite's datetime('now') writes."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def connect(path: str | Path, *, timeout: float = 5.0) -> sqlite3.Connection:
    """Open a connection in autocommit mode with the pragmas the plan needs."""
    target = str(path)
    if target != ":memory:":
        Path(target).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target, timeout=timeout, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    """Apply schema.sql; every statement is IF NOT EXISTS, so this is idempotent."""
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))


def open_db(path: str | Path) -> sqlite3.Connection:
    conn = connect(path)
    migrate(conn)
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection, *, immediate: bool = True) -> Iterator[sqlite3.Connection]:
    """A short write transaction; nested calls join the outer one."""
    if conn.in_transaction:
        yield conn
        return
    conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")
