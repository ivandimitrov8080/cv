"""Connection, schema, and one short write transaction.

SQLite is the shared state: WAL + busy_timeout + BEGIN IMMEDIATE is the whole
concurrency story. The schema is idempotent, so every command opens the same way.
"""

import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA = Path(__file__).with_name("schema.sql")


def open_db(path: str | Path) -> sqlite3.Connection:
    path = str(path)
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    for pragma in ("busy_timeout = 10000", "journal_mode = WAL",
                   "synchronous = NORMAL", "foreign_keys = ON"):
        conn.execute(f"PRAGMA {pragma}")
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    # Databases created by the first port predate these two columns.
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(items)")}
    for name, kind in (("question", "TEXT"), ("submitting", "INTEGER NOT NULL DEFAULT 0")):
        if name not in columns:
            conn.execute(f"ALTER TABLE items ADD COLUMN {name} {kind}")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection):
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")
