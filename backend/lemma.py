"""Offline English word-form normalization using ECDICT exchange and lemma data."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import re
from typing import Dict, List

from .config import REPO_ROOT
from .db import get_db

_WORD_RE = re.compile(r"^[A-Za-z][A-Za-z'-]*$")


@lru_cache(maxsize=1)
def _load_lemma_index() -> Dict[str, tuple]:
    """Build inflected-form -> lemma index from the bundled lemma.en.txt."""
    path = REPO_ROOT / "lemma.en.txt"
    index: Dict[str, List[str]] = {}
    if not path.is_file():
        return {}
    with path.open("r", encoding="utf-8-sig", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith(";") or "->" not in line:
                continue
            stem, forms = line.split("->", 1)
            lemma = stem.split("/", 1)[0].strip().casefold()
            if not lemma:
                continue
            index.setdefault(lemma, [])
            if lemma not in index[lemma]:
                index[lemma].append(lemma)
            for form in forms.split(","):
                form = form.split("/", 1)[0].strip().casefold()
                if form:
                    values = index.setdefault(form, [])
                    if lemma not in values:
                        values.append(lemma)
    return {word: tuple(lemmas) for word, lemmas in index.items()}


def _exchange_info(word: str, conn, columns=None):
    """Read ECDICT exchange 0:lemma and detect an independent dictionary sense."""
    if conn is None:
        return None, False
    try:
        if columns is None:
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(words)")}
        if not {"word", "exchange"}.issubset(columns):
            return None, False
        selected = "exchange" + (", translation" if "translation" in columns else "")
        row = conn.execute(
            f"SELECT {selected} FROM words WHERE word = ? COLLATE NOCASE LIMIT 1", (word,)
        ).fetchone()
        if not row:
            return None, False
        exchange = str(row["exchange"] or "")
        translation = str(row["translation"] or "") if "translation" in columns else ""
        lemma = None
        for part in exchange.split("/"):
            if ":" not in part:
                continue
            kind, value = part.split(":", 1)
            if kind.strip() == "0":
                candidate = value.strip().casefold()
                if candidate and candidate != word.casefold() and _WORD_RE.fullmatch(candidate):
                    lemma = candidate
                    break
        # If ECDICT also gives the spelling its own ordinary POS/meaning,
        # keep it as a selectable alternative (e.g. saw as a noun vs. see's past).
        has_independent_sense = bool(re.search(r"(?:^|\n)\s*(?:n|v|vt|vi|adj|adv|a)\.", translation, re.I))
        return lemma, has_independent_sense
    except Exception:
        return None, False

def normalize_word(word: str, conn=None, columns=None) -> dict:
    """Return the preferred lemma and candidates; ambiguous forms remain flagged."""
    raw = (word or "").strip()
    if not _WORD_RE.fullmatch(raw):
        return {
            "original_word": raw, "word": raw, "lemma_changed": False,
            "lemma_candidates": [], "lemma_uncertain": False,
        }
    key = raw.casefold()

    # ECDICT exchange is the most specific source: its 0: field explicitly
    # identifies the base form (e.g. better -> good, went -> go, saw -> see).
    exchange_lemma, independent_sense = _exchange_info(key, conn, columns)
    if exchange_lemma:
        candidates = [exchange_lemma]
        if independent_sense:
            candidates.append(key)
        return {
            "original_word": raw, "word": exchange_lemma,
            "lemma_changed": exchange_lemma != key,
            "lemma_candidates": candidates, "lemma_uncertain": independent_sense,
            "lemma_source": "ecdict_exchange",
        }

    candidates = list(_load_lemma_index().get(key, ()))
    if not candidates:
        candidates = [key]
    lemma = candidates[0]
    return {
        "original_word": raw, "word": lemma,
        "lemma_changed": lemma != key,
        "lemma_candidates": candidates,
        "lemma_uncertain": len(candidates) > 1,
        "lemma_source": "lemma.en.txt" if lemma != key or len(candidates) > 1 else "unchanged",
    }


def normalize_words(words: list[str]) -> list[dict]:
    """Normalize a batch with one SQLite connection, falling back to lemma file."""
    try:
        with get_db() as conn:
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(words)")}
            return [normalize_word(word, conn, columns) for word in words]
    except Exception:
        return [normalize_word(word) for word in words]
