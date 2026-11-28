import re
import math
import random
from difflib import SequenceMatcher
from .db import connect

def _escape_like(value: str) -> str:
    return value.replace("!", "!!").repdef _frequency_rank(row):
    try:
        value = int(row["frq"] or 0)
        return value if value > 0 else 2147483647
    except (KeyError, TypeError, ValueError):
        return 2147483647


def search_words(q: str, limit: int = 15, offset: int = 0, mode: str = "prefix"):
    q = q.strip()
    if not q:
        return []
    limit = max(1, min(int(limit), 50))
    offset = max(0, int(offset))
    conn = connect()
    try:
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(words)")}
        fields = ["word", "translation", "definition", "phonetic", "pos"]
        selected = [f'"{name}"' for name in fields if name in columns]
        selected.append('"frq"' if "frq" in columns else "NULL AS frq")
        selected_sql = ", ".join(selected)
        if mode == "contains" and len(q) >= 3:
            pattern = "%" + _escape_like(q) + "%"
        else:
            pattern = _escape_like(q) + "%"
        rows = conn.execute(
            f"SELECT {selected_sql} FROM words WHERE word LIKE ? ESCAPE '!' LIMIT ?",
            (pattern, max(limit * 12, 120)),
        ).fetchall()
        candidates = {}
        for row in rows:
            item = dict(row)
            candidates[item["word"].casefold()] = item

        if mode == "prefix" and len(q) >= 4:
            low = max(1, len(q) - (2 if len(q) >= 7 else 1))
            high = len(q) + (2 if len(q) >= 7 else 1)
            freq_order = (
                "CASE WHEN CAST(frq AS INTEGER)>0 THEN CAST(frq AS INTEGER) ELSE 2147483647 END"
                if "frq" in columns else "word COLLATE NOCASE"
            )
            if q[0:1].isalpha():
                rows = conn.execute(
                    f"SELECT {selected_sql} FROM words WHERE word LIKE ? ESCAPE '!' "
                    f"AND length(word) BETWEEN ? AND ? ORDER BY {freq_order} LIMIT 700",
                    (q[0] + "%", low, high),
                ).fetchall()
                for row in rows:
                    item = dict(row)
                    candidates.setdefault(item["word"].casefold(), item)

            gram_size = 3 if len(q) >= 6 else 2
            grams = list(dict.fromkeys(q[i:i + gram_size] for i in range(len(q) - gram_size + 1)))[:6]
            if grams:
                clauses = " OR ".join("word LIKE ? ESCAPE '!'" for _ in grams)
                hit_expr = " + ".join("CASE WHEN word LIKE ? ESCAPE '!' THEN 1 ELSE 0 END" for _ in grams)
                rows = conn.execute(
                    f"SELECT {selected_sql}, ({hit_expr}) AS gram_hits FROM words "
                    f"WHERE {clauses} ORDER BY gram_hits DESC, {freq_order} LIMIT 250",
                    ["%" + _escape_like(g) + "%" for g in grams] * 2,
                ).fetchall()
                for row in rows:
                    item = dict(row)
                    candidates.setdefault(item["word"].casefold(), item)

        ranked = []
        query_folded = q.casefold()
        threshold = 0.72 if len(q) <= 4 else 0.64 if len(q) <= 6 else 0.60
        for item in candidates.values():
            word = str(item.get("word") or "")
            folded = word.casefold()
            if folded == query_folded:
                match_type, similarity = "exact", 1.0
            elif folded.startswith(query_folded):
                match_type, similarity = "prefix", 1.0
            elif mode == "contains" and query_folded in folded:
                match_type, similarity = "contains", 1.0
            else:
                similarity = SequenceMatcher(None, query_folded, folded, autojunk=False).ratio()
                if similarity < threshold:
                    continue
                match_type = "fuzzy"
            item["match_type"] = match_type
            item["similarity"] = round(similarity, 4)
            ranked.append(item)

        ranked.sort(key=lambda item: (
            0 if item["match_type"] == "exact" else 1,
            _frequency_rank(item),
            0 if item["match_type"] == "prefix" else 1,
            -item["similarity"],
            str(item["word"]).casefold(),
        ))
        return ranked[offset:offset + limit]
    finally:
        conn.close()
]
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
            weights.append(0.001 if rank <= 0 else 1.0 / math.sqrt(max(1, rank)))
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


def get_word_comparisons(word: str, limit: int = 10):
    """Return complete resemble.txt comparison groups that list this exact word."""
    target = (word or "").strip().casefold()
    if not target:
        return []
    conn = connect()
    try:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='resemble_raw'"
        ).fetchone()
        if not exists:
            return []
        headers = conn.execute(
            """SELECT line_no, content FROM resemble_raw
               WHERE substr(ltrim(content),1,1)='%' AND lower(content) LIKE ?
               ORDER BY line_no LIMIT 100""",
            ("%" + target + "%",),
        ).fetchall()
        results = []
        for header in headers:
            title = (header["content"] or "").strip().lstrip("%").strip()
            members = [part.strip().strip(" .;") for part in title.split(",")]
            if not any(member.casefold() == target for member in members):
                continue
            next_header = conn.execute(
                """SELECT MIN(line_no) FROM resemble_raw
                   WHERE line_no>? AND substr(ltrim(content),1,1)='%'""",
                (header["line_no"],),
            ).fetchone()[0]
            if next_header is None:
                body_rows = conn.execute(
                    "SELECT content FROM resemble_raw WHERE line_no>? ORDER BY line_no",
                    (header["line_no"],),
                ).fetchall()
            else:
                body_rows = conn.execute(
                    "SELECT content FROM resemble_raw WHERE line_no>? AND line_no<? ORDER BY line_no",
                    (header["line_no"], next_header),
                ).fetchall()
            body = "\n".join((row["content"] or "") for row in body_rows).strip()
            results.append({"title": title, "words": members, "content": body})
            if len(results) >= max(1, min(int(limit), 20)):
                break
        return results
    finally:
        conn.close()

def get_root(query: str):
    return _search_raw_table("wordroot_raw", query, 30)

def get_lemma(query: str):
    return _search_raw_table("lemma_raw", query, 30)
