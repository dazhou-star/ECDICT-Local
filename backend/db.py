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
    """Create/migrate shared vocabulary and original-image metadata tables."""
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
        favorite_columns = {row["name"] for row in conn.execute("PRAGMA table_info(favorites)")}
        if "study_date" not in favorite_columns:
            conn.execute("ALTER TABLE favorites ADD COLUMN study_date TEXT")
        # Existing entries keep their historical insertion date as the best
        # available initial study date. This is a one-time, non-destructive migration.
        conn.execute(
            "UPDATE favorites SET study_date = substr(added_at, 1, 10) "
            "WHERE study_date IS NULL OR study_date = ''"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS image_imports (
                image_id TEXT PRIMARY KEY,
                original_filename TEXT NOT NULL,
                stored_filename TEXT NOT NULL,
                mime_type TEXT NOT NULL,
                byte_size INTEGER NOT NULL,
                sha256 TEXT NOT NULL,
                uploaded_at TEXT NOT NULL,
                date_start TEXT NOT NULL,
                date_end TEXT NOT NULL,
                recognized_words INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.commit()
