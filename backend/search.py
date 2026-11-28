import re
import math
import random
from .db import connect

def _escape_like(value: str) -> str:
    return value.replace("!", "!!").replace("%", "!%").replace("_", "!_")

def search_words(q: str, limit: int = 15, offset: int = 0, mode: str = "prefix"):
    q = q.strip()
    if not q:
        return []
    limit = max(1, min(int(limit), 50))
    offset = max(0, int(offset))
    conn = connect()
    try:
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(words)")}
        preview_fields = [name for name in ("word", "translation", "definition", "phonetic", "pos") if name in columns]
        selected = ", ".join('"' + name + '"' for name in preview_fields)
        if mode == "contains" and len(q) >= 3:
            # Contains search is deliberately bounded and should be used only on demand.
            pattern = "%" + _escape_like(q) + "%"
        else:
            pattern = _escape_like(q) + "%"
        sql = f"SELECT {selected} FROM words WHERE word LIKE ? ESCAPE '!' ORDER BY word COLLATE NOCASE LIMIT ? OFFSET ?"
        rows = conn.execute(sql, (pattern, limit, offset)).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()

def get_word(word: str):
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM words WHERE word = ? COLLATE NOCASE LIMIT 1", (word.strip(),)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()

def random_word():
    """Sample a bounded pool and favor common ECDICT frequency ranks."""
    conn = connect()
    try:
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(words)")}
        if "frq" not in columns:
            row = conn.execute("SELECT word FROM words WHERE word<>'' ORDER BY RANDOM() LIMIT 1").fetchone()
            return row["word"] if row else None
        maximum = conn.execute("SELECT COALESCE(MAX(rowid),0) FROM words").fetchone()[0]
        if not maximum:
            return None
        candidates = {}
        for _ in range(12):
            start = random.randint(1, maximum)
            rows = conn.execute(
                "SELECT word,frq FROM words WHERE rowid>=? AND word<>'' LIMIT 900",
                (start,),
            ).fetchall()
            for row in rows:
                candidates.setdefault(row["word"].casefold(), row)
            if len(candidates) >= 5000:
                break
        if not candidates:
            return None
        words, weights = [], []
        for row in candidates.values():
            try:
                rank = int(row["frq"] or 0)
            except (TypeError, ValueError):
                rank = 0
            words.append(row["word"])
            weights.append(0.04 if rank <= 0 else 1.0 / math.sqrt(max(1, rank)))
        return random.choices(words, weights=weights, k=1)[0]
    finally:
        conn.close()

def get_stats():
    conn = connect()
    try:
        count = conn.execute("SELECT COUNT(*) FROM words").fetchone()[0]
        columns = [r["name"] for r in conn.execute("PRAGMA table_info(words)")]
        return {"total_entries": count, "fields": columns}
    finally:
        conn.close()

def _search_raw_table(table: str, query: str, limit: int = 30):
    """Search preserved auxiliary text lines without assuming a revision-specific schema."""
    conn = connect()
    try:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if not exists:
            return []
        rows = conn.execute(
            f'SELECT line_no, content FROM "{table}" WHERE content LIKE ? LIMIT ?',
            (f"%{query}%", max(1, min(int(limit), 100)))
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

def get_similar(query: str, limit: int = 20):
    return _search_raw_table("resemble_raw", query, limit)

def get_root(query: str):
    return _search_raw_table("wordroot_raw", query, 30)

def get_lemma(query: str):
    return _search_raw_table("lemma_raw", query, 30)
