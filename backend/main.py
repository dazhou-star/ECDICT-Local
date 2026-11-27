from datetime import datetime, timezone
from typing import List
from difflib import SequenceMatcher
from collections import Counter
from html import unescape
import re
from pydantic import BaseModel
from fastapi import FastAPI, HTTPException, Query, UploadFile, File
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from .image_ocr import recognize_vocabulary_image, OCREngineUnavailable, get_vision_status
from .config import FRONTEND_DIR, HOST, PORT
from .search import search_words, get_word, random_word, get_stats, get_similar, get_root, get_lemma
from .db import get_db, init_favorites_table

class FavoriteRequest(BaseModel):
    word: str


class FavoriteImportRequest(BaseModel):
    words: List[str]


class FavoriteBulkRequest(BaseModel):
    words: List[str]


class ImageCorrectionItem(BaseModel):
    word: str = ""
    meaning: str = ""


class ImageCorrectionRequest(BaseModel):
    items: List[ImageCorrectionItem]


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clean_word(value: str) -> str:
    word = value.strip()
    if not word:
        raise HTTPException(status_code=422, detail="单词不能为空")
    if len(word) > 100:
        raise HTTPException(status_code=422, detail="单词长度不能超过 100 个字符")
    return word


_POS_PREFIX_RE = re.compile(
    r"^(?:(?:adj|adv|n|v|vt|vi|prep|pron|conj|int|art|aux|abbr|num|det|s|r)\.?\s*)+",
    re.IGNORECASE,
)


def _normalize_english(value: str) -> str:
    return re.sub(r"[^a-z'-]", "", (value or "").casefold())


def _normalize_meaning(value: str) -> str:
    value = unescape(re.sub(r"<[^>]*>", " ", value or ""))
    value = _POS_PREFIX_RE.sub("", value.strip())
    # The handwritten list is Chinese-first. Keep CJK characters for reverse lookup
    # and similarity scoring; punctuation, arrows and part-of-speech labels are noise.
    return "".join(re.findall(r"[\u3400-\u9fff]", value))


def _escape_like(value: str) -> str:
    return value.replace("!", "!!").replace("%", "!%").replace("_", "!_")


def _word_similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right, autojunk=False).ratio()


def _meaning_similarity(query: str, translation: str) -> float:
    if not query or not translation:
        return 0.0
    target = _normalize_meaning(translation)
    if not target:
        return 0.0
    if query in target:
        return 1.0
    if target in query and len(target) >= 2:
        # A concise dictionary gloss may be embedded in a longer handwritten
        # explanation, e.g. "食物等的芳香" vs "芳香".
        return max(0.68, min(1.0, len(target) / len(query)))
    matcher = SequenceMatcher(None, query, target, autojunk=False)
    longest = matcher.find_longest_match(0, len(query), 0, len(target)).size
    coverage = longest / max(1, len(query))
    q_count, t_count = Counter(query), Counter(target)
    overlap = sum(min(count, t_count[char]) for char, count in q_count.items()) / max(1, len(query))
    return max(coverage, overlap * 0.82)


def _add_candidate(target: dict, row) -> None:
    word = str(row["word"] or "").strip()
    if not word:
        return
    key = word.casefold()
    target.setdefault(key, {"word": word, "translation": str(row["translation"] or "")})


def _fuzzy_candidates(conn, word: str, meaning: str, columns: set):
    """Gather a bounded candidate set from spelling and Chinese-translation searches."""
    candidates = {}
    if word:
        # Exact and prefix hits are cheap and should always be considered.
        for row in conn.execute(
            "SELECT word, translation FROM words WHERE word = ? COLLATE NOCASE LIMIT 5",
            (word,),
        ):
            _add_candidate(candidates, row)
        for row in conn.execute(
            "SELECT word, translation FROM words WHERE word LIKE ? ESCAPE '!' "
            "ORDER BY word COLLATE NOCASE LIMIT 80",
            (_escape_like(word) + "%",),
        ):
            _add_candidate(candidates, row)

        # Search a bounded same-initial/near-length bucket. This catches common
        # handwritten OCR errors such as a missing/doubled letter.
        if word[0:1].isalpha():
            low, high = max(1, len(word) - 2), len(word) + 2
            if "frq" in columns:
                sql = (
                    "SELECT word, translation FROM words "
                    "WHERE word LIKE ? ESCAPE '!' AND length(word) BETWEEN ? AND ? "
                    "ORDER BY ABS(length(word)-?), "
                    "CASE WHEN CAST(frq AS INTEGER)>0 THEN CAST(frq AS INTEGER) ELSE 999999 END "
                    "LIMIT 10000"
                )
                params = (word[0] + "%", low, high, len(word))
            else:
                sql = (
                    "SELECT word, translation FROM words "
                    "WHERE word LIKE ? ESCAPE '!' AND length(word) BETWEEN ? AND ? "
                    "ORDER BY ABS(length(word)-?), word COLLATE NOCASE LIMIT 10000"
                )
                params = (word[0] + "%", low, high, len(word))
            for row in conn.execute(sql, params):
                _add_candidate(candidates, row)

        # If the first letter was misread, shared trigrams can still recover the
        # intended spelling. The OR clauses perform one bounded dictionary scan.
        gram_size = 3 if len(word) >= 5 else 2
        grams = list(dict.fromkeys(word[i:i + gram_size] for i in range(max(0, len(word) - gram_size + 1))))
        if grams:
            grams = grams[:8]
            clauses = " OR ".join("word LIKE ? ESCAPE '!'" for _ in grams)
            params = ["%" + _escape_like(g) + "%" for g in grams]
            hits = " + ".join("CASE WHEN word LIKE ? ESCAPE '!' THEN 1 ELSE 0 END" for _ in grams)
            sql = f"SELECT word, translation, ({hits}) AS hits FROM words WHERE {clauses} ORDER BY hits DESC LIMIT 250"
            for row in conn.execute(sql, params + params):
                _add_candidate(candidates, row)

    if meaning and "translation" in columns:
        # Prefer 2-3 character fragments, which tolerate small OCR mistakes while
        # still narrowing the reverse Chinese lookup to useful candidates.
        size = 3 if len(meaning) >= 5 else 2
        fragments = []
        if len(meaning) >= 2:
            fragments.extend(meaning[i:i + 2] for i in range(len(meaning) - 1))
        fragments.extend(meaning[i:i + size] for i in range(max(0, len(meaning) - size + 1)))
        fragments = list(dict.fromkeys(fragments))[:12]
        if fragments:
            clauses = " OR ".join("translation LIKE ? ESCAPE '!'" for _ in fragments)
            hits = " + ".join("CASE WHEN translation LIKE ? ESCAPE '!' THEN 1 ELSE 0 END" for _ in fragments)
            patterns = ["%" + _escape_like(fragment) + "%" for fragment in fragments]
            sql = f"SELECT word, translation, ({hits}) AS hits FROM words WHERE {clauses} ORDER BY hits DESC LIMIT 400"
            for row in conn.execute(sql, patterns + patterns):
                _add_candidate(candidates, row)

    return list(candidates.values())


def _correct_image_item(conn, item: ImageCorrectionItem, columns: set):
    raw_word = (item.word or "").strip()
    raw_meaning = (item.meaning or "").strip()
    query_word = _normalize_english(raw_word)
    query_meaning = _normalize_meaning(raw_meaning)
    candidates = _fuzzy_candidates(conn, query_word, query_meaning, columns)

    ranked = []
    for candidate in candidates:
        candidate_word = _normalize_english(candidate["word"])
        en_score = _word_similarity(query_word, candidate_word) if query_word else 0.0
        zh_score = _meaning_similarity(query_meaning, candidate["translation"])
        if query_word and query_meaning:
            combined = 0.58 * en_score + 0.42 * zh_score
            # Preserve a literal dictionary hit even when the handwritten gloss is
            # unusual or abbreviated; report the meaning mismatch instead.
            if en_score >= 0.999:
                combined = max(combined, 0.78 + 0.20 * zh_score)
        elif query_word:
            combined = en_score
        else:
            combined = zh_score
        ranked.append({
            "word": candidate["word"],
            "translation": candidate["translation"],
            "english_score": round(en_score, 4),
            "meaning_score": round(zh_score, 4),
            "score": round(combined, 4),
        })

    ranked.sort(key=lambda x: (x["score"], x["english_score"], x["meaning_score"]), reverse=True)
    best = ranked[0] if ranked else None
    second_score = ranked[1]["score"] if len(ranked) > 1 else 0.0
    margin = (best["score"] - second_score) if best else 0.0

    # Do not present weak random neighbours as actionable repairs.
    suggestion = best["word"] if best and best["score"] >= 0.58 else ""
    same_word = bool(suggestion and query_word and _normalize_english(suggestion) == query_word)
    en_score = best["english_score"] if best else 0.0
    zh_score = best["meaning_score"] if best else 0.0
    score = best["score"] if best else 0.0

    bilingual_support = bool(query_word and query_meaning and en_score >= 0.72 and zh_score >= 0.72)
    meaning_led_support = bool(query_meaning and zh_score >= 0.92 and (not query_word or en_score >= 0.48))
    high_confidence = bool(best and (bilingual_support or meaning_led_support) and score >= 0.78)
    auto_correct = bool(best and suggestion and not same_word and high_confidence and margin >= 0.025)

    if not best:
        level, reason = "none", "没有找到足够接近的词典候选；请手动核对。"
    elif same_word:
        level = "verified" if zh_score >= 0.55 or not query_meaning else "meaning_check"
        if not query_meaning:
            reason = "英文词头在词典中精确命中；图片没有可用中文释义，因此本行只完成了英文核对。"
        elif zh_score >= 0.55:
            reason = "英文词头精确命中；中文释义也有较高匹配度。"
        else:
            reason = "英文词头精确命中，但图片释义与词典释义不够一致，请检查释义或行配对。"
    elif auto_correct:
        level = "high"
        reason = (
            "英文拼写相似度与中文反向释义同时吻合，已将建议词填入待校对栏；原识别内容仍保留。"
            if query_word else
            "OCR 未识别出英文词头，但中文释义在词典中高度匹配，已反向补全建议词；请确认后再导入。"
        )
    elif best and score >= 0.58:
        level = "review"
        reason = "找到可能的词典候选，但中英文证据尚不足以自动修复，请比较后决定。"
    else:
        level = "low"
        reason = "未找到可靠的双语匹配；候选仅供参考，请手动检查。"

    return {
        "original_word": raw_word,
        "suggested_word": suggestion,
        "dictionary_meaning": best["translation"] if best else "",
        "english_score": en_score,
        "meaning_score": zh_score,
        "score": score,
        "margin": round(margin, 4),
        "level": level,
        "auto_correct": auto_correct,
        "reason": reason,
        "alternatives": ranked[:3],
    }


app = FastAPI(
    title="Lexicon 本地英汉辞典",
    description="基于 ECDICT 与 SQLite 的局域网离线词典",
    version="1.0.0"
)


@app.on_event("startup")
def startup_initialize_database():
    init_favorites_table()

@app.get("/api/health")
def health():
    stats = get_stats()
    return {"ok": True, **stats}

@app.get("/api/stats")
def stats():
    return get_stats()

@app.get("/api/search")
def search(
    q: str = Query(..., min_length=1, max_length=100),
    limit: int = Query(15, ge=1, le=50),
    offset: int = Query(0, ge=0),
    mode: str = Query("prefix", pattern="^(prefix|contains)$")
):
    return {"query": q, "items": search_words(q, limit, offset, mode)}

@app.get("/api/image-import/status")
def image_import_status():
    """Check the local Ollama service and configured Gemma 4 vision model."""
    return get_vision_status()


@app.post("/api/image-import/recognize")
async def recognize_image_import(file: UploadFile = File(...)):
    """Upload a full vocabulary photo and transcribe it with local Gemma 4 vision."""
    content_type = (file.content_type or "").lower()
    if content_type and not content_type.startswith("image/"):
        raise HTTPException(status_code=415, detail="请上传图片文件")
    image_bytes = await file.read(20 * 1024 * 1024 + 1)
    await file.close()
    if len(image_bytes) > 20 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="图片不能超过 20 MB")
    try:
        result = await run_in_threadpool(recognize_vocabulary_image, image_bytes)
    except OCREngineUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        import logging
        logging.getLogger("lexicon.vision").exception("Gemma 4 image transcription failed")
        raise HTTPException(status_code=500, detail=f"Gemma 4 图片识别失败：{type(exc).__name__}。请检查后端终端日志。") from exc
    return {"ok": True, **result}


@app.post("/api/image-import/correct")
def correct_image_import(payload: ImageCorrectionRequest):
    """Bidirectionally verify OCR rows against English headwords and Chinese glosses."""
    if len(payload.items) > 100:
        raise HTTPException(status_code=413, detail="单次最多校对 100 行")
    if any(len(item.word or "") > 100 or len(item.meaning or "") > 500 for item in payload.items):
        raise HTTPException(status_code=422, detail="单词最多 100 个字符，释义最多 500 个字符")
    with get_db() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(words)")}
        if not {"word", "translation"}.issubset(columns):
            raise HTTPException(status_code=503, detail="词库缺少 word 或 translation 字段，无法进行双语校对")
        results = [_correct_image_item(conn, item, columns) for item in payload.items]
    return {"ok": True, "count": len(results), "results": results}


@app.get("/api/favorites")
def list_favorites():
    with get_db() as conn:
        rows = conn.execute(
            "SELECT word, added_at FROM favorites ORDER BY added_at DESC, id DESC"
        ).fetchall()
    return {"items": [dict(row) for row in rows]}


@app.post("/api/favorites")
def add_favorite(payload: FavoriteRequest):
    word = clean_word(payload.word)
    now = utc_now_iso()
    with get_db() as conn:
        cursor = conn.execute(
            "INSERT OR IGNORE INTO favorites (word, added_at) VALUES (?, ?)",
            (word, now),
        )
        conn.commit()
        row = conn.execute(
            "SELECT word, added_at FROM favorites WHERE word = ? COLLATE NOCASE",
            (word,),
        ).fetchone()
    return {"ok": True, "created": cursor.rowcount == 1, **dict(row)}


@app.post("/api/favorites/import")
def import_favorites(payload: FavoriteImportRequest):
    """One-time import of the browser's former localStorage favorites."""
    now = utc_now_iso()
    words = []
    seen = set()
    for value in payload.words:
        if not isinstance(value, str):
            continue
        word = value.strip()
        key = word.casefold()
        if word and len(word) <= 100 and key not in seen:
            seen.add(key)
            words.append(word)

    imported = 0
    with get_db() as conn:
        # Legacy localStorage is newest-first; insert in reverse so id DESC
        # keeps that order when all imported words share one migration time.
        for word in reversed(words):
            cursor = conn.execute(
                "INSERT OR IGNORE INTO favorites (word, added_at) VALUES (?, ?)",
                (word, now),
            )
            imported += max(cursor.rowcount, 0)
        conn.commit()
    return {"ok": True, "received": len(words), "imported": imported}


@app.post("/api/favorites/bulk")
def bulk_add_favorites(payload: FavoriteBulkRequest):
    """Add a checked batch of words to the shared favorites list."""
    if len(payload.words) > 500:
        raise HTTPException(status_code=413, detail="单次最多导入 500 个单词")

    now = utc_now_iso()
    words = []
    seen = set()
    for value in payload.words:
        if not isinstance(value, str):
            continue
        word = value.strip()
        key = word.casefold()
        if word and len(word) <= 100 and key not in seen:
            seen.add(key)
            words.append(word)

    created = 0
    with get_db() as conn:
        for word in words:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO favorites (word, added_at) VALUES (?, ?)",
                (word, now),
            )
            created += max(cursor.rowcount, 0)
        conn.commit()

    return {
        "ok": True,
        "received": len(payload.words),
        "valid": len(words),
        "created": created,
        "skipped": len(words) - created,
    }


@app.delete("/api/favorites/{word}")
def remove_favorite(word: str):
    word = clean_word(word)
    with get_db() as conn:
        cursor = conn.execute(
            "DELETE FROM favorites WHERE word = ? COLLATE NOCASE",
            (word,),
        )
        conn.commit()
    return {"ok": True, "deleted": cursor.rowcount > 0}


@app.get("/api/word/{word}")
def word_detail(word: str):
    result = get_word(word)
    if result is None:
        raise HTTPException(status_code=404, detail="未找到该词条")
    return result

@app.get("/api/random")
def random():
    word = random_word()
    if not word:
        raise HTTPException(status_code=503, detail="词库尚未导入")
    return {"word": word}

@app.get("/api/similar")
def similar(q: str = Query(..., min_length=1, max_length=100)):
    return {"query": q, "items": get_similar(q)}

@app.get("/api/root")
def root_lookup(q: str = Query(..., min_length=1, max_length=100)):
    return {"query": q, "items": get_root(q)}

@app.get("/api/lemma")
def lemma_lookup(q: str = Query(..., min_length=1, max_length=100)):
    return {"query": q, "items": get_lemma(q)}

# Serve the web UI from the same origin. API and frontend need no CORS configuration.
@app.get("/favicon.ico")
def favicon():
    return Response(status_code=204)

@app.get("/")
def index():
    path = FRONTEND_DIR / "index.html"
    if not path.exists():
        raise HTTPException(status_code=404, detail="前端文件缺失")
    return FileResponse(path)

app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host=HOST, port=PORT, reload=False)
