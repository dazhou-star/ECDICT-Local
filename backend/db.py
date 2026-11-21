import sqlite3
from contextlib import contextmanager
from .config import DATABASE_PATH

def connect():
    conn = sqlite3.connect(DATABASE_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn

@contextmanager
def get_db():
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()

def table_columns(conn):
    return [row["name"] for row in conn.execute("PRAGMA table_info(words)")]


def init_favorites_table():
    """Create the shared LAN favorites table if it does not exist."""
    with get_db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS favorites (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                word TEXT NOT NULL COLLATE NOCASE UNIQUE,
                added_at TEXT NOT NULL
            )
            """
        )
        conn.commit()
