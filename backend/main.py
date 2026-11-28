from datetime import datetime, timezone, date, timedelta
from typing import List, Optional, Dict
from pathlib import Path
from uuid import uuid4
import hashlib
import json
from difflib import SequenceMatcher
from collections import Counter
from html import unescape
import re
from pydantic import BaseModel
from fastapi import FastAPI, HTTPException, Query, UploadFile, File, Form
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from .image_ocr import recognize_vocabulary_image, OCREngineUnavailable, get_vision_status
from .paddle_ocr import get_paddle_status
from .lemma import normalize_words
from .config import FRONTEND_DIR, HOST, PORT, IMAGE_IMPORT_DIR
from .search import search_words, get_word, random_word, get_stats, get_similar, get_root, get_lemma
from .db import get_db, init_favorites_table
from .mastery import init_mastery_tables, prepare_favorites, list_senses, set_sense_state, add_fragment, update_fragment_by_order, update_fragment, create_question, submit_answer, overview

class FavoriteRequest(BaseModel):
    word: str
    study_date: Optional[str] = None


class FavoriteImportRequest(BaseModel):
    words: List[str]


class FavoriteBulkItem(BaseModel):
    word: str
    study_date: Optional[str] = None


class FavoriteBulkRequest(BaseModel):
    # `words` remains supported for older clients; new clients send dated `items`.
    words: List[str] = []
    items: List[FavoriteBulkItem] = []


class ImageCorrectionItem(BaseModel):
    word: str = ""
    meaning: str = ""


class ImageCorrectionRequest(BaseModel):
    items: List[ImageCorrectionItem]


class ImageNormalizeRequest(BaseModel):
    words: List[str]


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def validate_study_date(value: Optional[str]) -> str:
    """Return a validated ISO calendar date; default to today's server-local date."""
    if not value:
        return date.today().isoformat()
    try:
        return date.fromisoformat(value).isoformat()
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail=f"无效日期：{value}，请使用 YYYY-MM-DD")


def distribute_dates(start_value: str, end_value: str, count: int) -> List[str]:
    """Spread ordered OCR rows evenly over an inclusive date interval."""
    start = date.fromisoformat(start_value)
    end = date.fromisoformat(end_value)
    if end < start:
        raise HTTPException(status_code=422, detail="日期段的结束日期不能早于开始日期")
    if count <= 0:
        return []
    span = (end - start).days
    if count == 1 or span == 0:
        return [start.isoformat()] * count
    # Include both endpoints; rounding distributes rows as evenly as possible.
    return [(start + timedelta(days=int((i * span / (count - 1)) + 0.5))).isoformat() for i in range(count)]


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

        # 排除与图片原词属于同一词条的其他词形。
    # normalize_words 使用项目现有的词形还原数据，不修改原始单词。
    if query_word and candidates:
        normalization_input = [raw_word] + [
            candidate["word"] for candidate in candidates
        ]
        normalized_items = normalize_words(normalization_input)

        original_lemma = _normalize_english(
            normalized_items[0].get("word") or raw_word
        )

        filtered_candidates = []

        for candidate, normalized in zip(
            candidates, normalized_items[1:]
        ):
            candidate_word = _normalize_english(candidate["word"])
            candidate_lemma = _normalize_english(
                normalized.get("word") or candidate["word"]
            )

            # 原词本身必须保留；其他与原词还原到同一词头的候选全部排除。
            if (
                candidate_word != query_word
                and original_lemma
                and candidate_lemma == original_lemma
            ):
                continue

            filtered_candidates.append(candidate)

        candidates = filtered_candidates

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
    init_mastery_tables()

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
    """Report Gemma 4 as the primary whole-image engine; PaddleOCR is not used."""
    vision = get_vision_status()
    paddle = get_paddle_status()
    return {
        **vision,
        # `available` means the engine actually used by /recognize is ready.
        "available": bool(vision.get("available")),
        "vision_available": bool(vision.get("available")),
        "ollama_available": bool(vision.get("ok")),
        "paddle": paddle,
        "primary_engine": "gemma4",
        "message": (
            f"Gemma 4（{vision.get('model') or 'configured model'}）已就绪，图片将整张交给视觉模型识别。"
            if vision.get("available")
            else f"Gemma 4 主识别引擎不可用：{vision.get('message', '未知错误')}"
        ),
    }


@app.post("/api/image-import/normalize")
def normalize_image_import(payload: ImageNormalizeRequest):
    """Convert OCR/manual words to ECDICT lemma forms using lemma.en.txt."""
    if len(payload.words) > 1000:
        raise HTTPException(status_code=413, detail="单次最多处理 1000 个单词")
    return {"ok": True, "items": normalize_words(payload.words)}


@app.post("/api/image-import/recognize")
async def recognize_image_import(
    files: List[UploadFile] = File(default=[]),
    file: Optional[UploadFile] = File(default=None),
    date_ranges: Optional[str] = Form(default=None),
):
    """Save each original photo, then OCR photos sequentially with per-photo date ranges."""
    uploads = list(files or [])
    if file is not None:
        uploads.append(file)
    if not uploads:
        raise HTTPException(status_code=422, detail="请至少选择一张图片")
    if len(uploads) > 10:
        raise HTTPException(status_code=413, detail="一次最多上传 10 张图片")

    try:
        supplied_ranges = json.loads(date_ranges) if date_ranges else []
        if not isinstance(supplied_ranges, list):
            raise ValueError("date_ranges 必须是数组")
    except (TypeError, ValueError, json.JSONDecodeError):
        raise HTTPException(status_code=422, detail="图片日期设置格式错误")

    today = date.today().isoformat()
    images = []
    all_rows = []
    total_bytes = 0
    IMAGE_IMPORT_DIR.mkdir(parents=True, exist_ok=True)
    extension_by_mime = {
        "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
        "image/bmp": ".bmp", "image/gif": ".gif", "image/tiff": ".tif",
    }

    for upload_index, upload in enumerate(uploads):
        filename = upload.filename or "vocabulary-image"
        content_type = (upload.content_type or "application/octet-stream").split(";")[0].lower()
        if not content_type.startswith("image/"):
            images.append({"filename": filename, "ok": False, "error": "不是有效的图片文件"})
            await upload.close()
            continue
        image_bytes = await upload.read(20 * 1024 * 1024 + 1)
        await upload.close()
        total_bytes += len(image_bytes)
        if total_bytes > 100 * 1024 * 1024:
            images.append({"filename": filename, "ok": False, "error": "本次上传总大小不能超过 100 MB"})
            for pending in uploads[upload_index + 1:]:
                pending_name = pending.filename or "vocabulary-image"
                await pending.close()
                images.append({"filename": pending_name, "ok": False, "error": "总上传大小超限，未处理此图片"})
            break
        if len(image_bytes) > 20 * 1024 * 1024:
            images.append({"filename": filename, "ok": False, "error": "单张图片不能超过 20 MB"})
            continue

        range_item = supplied_ranges[upload_index] if upload_index < len(supplied_ranges) else {}
        if not isinstance(range_item, dict):
            range_item = {}
        date_start = validate_study_date(range_item.get("start") or today)
        date_end = validate_study_date(range_item.get("end") or date_start)
        if date_end < date_start:
            raise HTTPException(status_code=422, detail=f"图片 {filename} 的日期段结束日期早于开始日期")

        # Persist the exact uploaded bytes before OCR, including images whose OCR later fails.
        image_id = uuid4().hex
        extension = extension_by_mime.get(content_type, ".img")
        stored_filename = f"{image_id}{extension}"
        stored_path = IMAGE_IMPORT_DIR / stored_filename
        try:
            stored_path.write_bytes(image_bytes)
            sha256 = hashlib.sha256(image_bytes).hexdigest()
            with get_db() as conn:
                conn.execute(
                    """INSERT INTO image_imports
                       (image_id, original_filename, stored_filename, mime_type, byte_size,
                        sha256, uploaded_at, date_start, date_end, recognized_words)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)""",
                    (image_id, filename, stored_filename, content_type, len(image_bytes),
                     sha256, utc_now_iso(), date_start, date_end),
                )
                conn.commit()
        except Exception:
            try:
                stored_path.unlink(missing_ok=True)
            except Exception:
                pass
            raise

        try:
            result = await run_in_threadpool(recognize_vocabulary_image, image_bytes)
            recognized = [dict(item) for item in result.get("rows", [])]
            assigned_dates = distribute_dates(date_start, date_end, len(recognized))
            rows = []
            for row_index, row in enumerate(recognized):
                row["raw_word"] = str(row.get("word") or "")
                row["source_image"] = filename
                row["source_image_id"] = image_id
                row["study_date"] = assigned_dates[row_index]
                rows.append(row)
                all_rows.append(row)
            with get_db() as conn:
                conn.execute("UPDATE image_imports SET recognized_words = ? WHERE image_id = ?", (len(rows), image_id))
                conn.commit()
            images.append({
                "filename": filename, "ok": True, "image_id": image_id,
                "saved": True, "date_start": date_start, "date_end": date_end,
                "engine": result.get("engine", "OCR"), "row_count": len(rows),
                "uncertain_count": sum(1 for row in rows if row.get("uncertain") or row.get("lemma_uncertain")),
                "elapsed_ms": result.get("elapsed_ms"), "rows": rows,
                "note": result.get("note", ""),
            })
        except OCREngineUnavailable as exc:
            images.append({"filename": filename, "ok": False, "image_id": image_id, "saved": True,
                           "date_start": date_start, "date_end": date_end, "error": str(exc)})
        except ValueError as exc:
            images.append({"filename": filename, "ok": False, "image_id": image_id, "saved": True,
                           "date_start": date_start, "date_end": date_end, "error": str(exc)})
        except Exception as exc:
            import logging
            logging.getLogger("lexicon.vision").exception("Image transcription failed: %s", filename)
            images.append({"filename": filename, "ok": False, "image_id": image_id, "saved": True,
                           "date_start": date_start, "date_end": date_end,
                           "error": f"识别失败：{type(exc).__name__}。原图已保存，请检查后端终端日志。"})

    normalized = normalize_words([str(row.get("raw_word") or row.get("word") or "") for row in all_rows])
    for row, lemma in zip(all_rows, normalized):
        row.update({
            "word": lemma["word"], "lemma_changed": lemma["lemma_changed"],
            "lemma_candidates": lemma["lemma_candidates"],
            "lemma_uncertain": lemma["lemma_uncertain"],
            "lemma_source": lemma.get("lemma_source", ""),
        })

    # Merge duplicate lemmas but retain all dates and source-image references.
    merged = {}
    for row in all_rows:
        key = str(row.get("word") or "").casefold()
        if not key:
            continue
        if key not in merged:
            merged[key] = dict(row)
            merged[key]["raw_words"] = [row.get("raw_word") or row.get("word")]
            merged[key]["source_images"] = [row.get("source_image", "")]
            merged[key]["source_image_ids"] = [row.get("source_image_id", "")]
            merged[key]["study_dates"] = [row.get("study_date", today)]
            first_meaning = str(row.get("meaning") or "").strip()
            merged[key]["_meanings"] = [first_meaning] if first_meaning else []
        else:
            current = merged[key]
            raw = row.get("raw_word") or row.get("word")
            if raw and raw not in current["raw_words"]:
                current["raw_words"].append(raw)
            source = row.get("source_image", "")
            if source and source not in current["source_images"]:
                current["source_images"].append(source)
            source_id = row.get("source_image_id", "")
            if source_id and source_id not in current["source_image_ids"]:
                current["source_image_ids"].append(source_id)
            study_date = row.get("study_date", today)
            if study_date and study_date not in current["study_dates"]:
                current["study_dates"].append(study_date)
            meaning = str(row.get("meaning") or "").strip()
            if meaning and meaning not in current["_meanings"]:
                current["_meanings"].append(meaning)
                current["meaning"] = "；".join(current["_meanings"])
            current["uncertain"] = bool(current.get("uncertain") or row.get("uncertain"))
            current["lemma_changed"] = bool(current.get("lemma_changed") or row.get("lemma_changed"))
            current["lemma_uncertain"] = bool(current.get("lemma_uncertain") or row.get("lemma_uncertain"))
            candidates = list(current.get("lemma_candidates") or [])
            for candidate in row.get("lemma_candidates") or []:
                if candidate not in candidates:
                    candidates.append(candidate)
            current["lemma_candidates"] = candidates
            if row.get("note") and row["note"] not in str(current.get("note") or ""):
                current["note"] = (str(current.get("note") or "") + "；" + str(row["note"])).strip("；")

    rows = list(merged.values())
    for index, row in enumerate(rows, 1):
        row["index"] = index
        row.pop("_meanings", None)
        row["raw_word"] = " / ".join(row.pop("raw_words", []))
        row["source_image"] = "、".join(row.pop("source_images", []))
        row["study_dates"] = sorted(set(row.get("study_dates") or []))
        # A word appearing in multiple photos defaults to its earliest assigned date;
        # the review table lets the user override it manually.
        row["study_date"] = row["study_dates"][0] if row["study_dates"] else today
        row["source_image_ids"] = row.pop("source_image_ids", [])

    return {
        "ok": True, "images": images, "image_count": len(uploads),
        "success_count": sum(1 for item in images if item.get("ok")),
        "failed_count": sum(1 for item in images if not item.get("ok")),
        "rows": rows, "row_count": len(rows),
        "uncertain_count": sum(1 for row in rows if row.get("uncertain") or row.get("lemma_uncertain")),
        "saved_image_count": sum(1 for item in images if item.get("saved")),
        "engine": "PaddleOCR CPU + Gemma 4 低置信度辅助",
    }


@app.get("/api/image-import/images/{image_id}")
def get_saved_import_image(image_id: str):
    """Retrieve an original image by its opaque server-generated identifier."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT original_filename, stored_filename, mime_type FROM image_imports WHERE image_id = ?",
            (image_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="找不到这张原始图片")
    path = IMAGE_IMPORT_DIR / row["stored_filename"]
    if not path.is_file():
        raise HTTPException(status_code=404, detail="原始图片文件不存在")
    return FileResponse(path, media_type=row["mime_type"], filename=row["original_filename"])


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
            "SELECT word, added_at, study_date FROM favorites ORDER BY study_date DESC, added_at DESC, id DESC"
        ).fetchall()
    return {"items": [dict(row) for row in rows]}


@app.post("/api/favorites")
def add_favorite(payload: FavoriteRequest):
    word = clean_word(payload.word)
    now = utc_now_iso()
    study_date = validate_study_date(payload.study_date)
    with get_db() as conn:
        cursor = conn.execute(
            "INSERT OR IGNORE INTO favorites (word, added_at, study_date) VALUES (?, ?, ?)",
            (word, now, study_date),
        )
        conn.commit()
        row = conn.execute(
            "SELECT word, added_at, study_date FROM favorites WHERE word = ? COLLATE NOCASE",
            (word,),
        ).fetchone()
    return {"ok": True, "created": cursor.rowcount == 1, **dict(row)}


@app.post("/api/favorites/import")
def import_favorites(payload: FavoriteImportRequest):
    """Import the browser's former localStorage favorites with today's study date."""
    now = utc_now_iso()
    study_date = date.today().isoformat()
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
        for word in reversed(words):
            cursor = conn.execute(
                "INSERT OR IGNORE INTO favorites (word, added_at, study_date) VALUES (?, ?, ?)",
                (word, now, study_date),
            )
            imported += max(cursor.rowcount, 0)
        conn.commit()
    return {"ok": True, "received": len(words), "imported": imported}


@app.post("/api/favorites/bulk")
def bulk_add_favorites(payload: FavoriteBulkRequest):
    """Add checked words with individual study dates; old `words` payloads remain valid."""
    if len(payload.words) + len(payload.items) > 500:
        raise HTTPException(status_code=413, detail="单次最多导入 500 个单词")

    now = utc_now_iso()
    today = date.today().isoformat()
    dated_items = [(value, None) for value in payload.words]
    dated_items.extend((item.word, item.study_date) for item in payload.items)
    words = []
    seen = set()
    for value, study_date_value in dated_items:
        if not isinstance(value, str):
            continue
        word = value.strip()
        key = word.casefold()
        if word and len(word) <= 100 and key not in seen:
            seen.add(key)
            words.append((word, validate_study_date(study_date_value or today)))

    created = 0
    with get_db() as conn:
        for word, study_date in words:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO favorites (word, added_at, study_date) VALUES (?, ?, ?)",
                (word, now, study_date),
            )
            created += max(cursor.rowcount, 0)
        conn.commit()

    return {
        "ok": True, "received": len(dated_items), "valid": len(words),
        "created": created, "skipped": len(words) - created,
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



class SenseStateRequest(BaseModel):
    state: str

class SenseFragmentRequest(BaseModel):
    fragment: str

class MasteryAnswerRequest(BaseModel):
    question_id: str
    answer: str

@app.post("/api/mastery/prepare")
def mastery_prepare():
    return {"ok": True, **prepare_favorites()}

@app.get("/api/mastery/overview")
def mastery_overview():
    return overview()

@app.get("/api/mastery/senses")
def mastery_senses(state: str = Query("draft", pattern="^(draft|active|ignored|all)$")):
    return {"items": list_senses(state)}

@app.patch("/api/mastery/senses/{sense_id}")
def mastery_update_sense(sense_id: int, payload: SenseStateRequest):
    try:
        result = set_sense_state(sense_id, payload.state)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if result is None:
        raise HTTPException(status_code=404, detail="找不到义项")
    return {"ok": True, **result}

@app.post("/api/mastery/senses/{sense_id}/fragments")
def mastery_add_fragment(sense_id: int, payload: SenseFragmentRequest):
    try:
        result = add_fragment(sense_id, payload.fragment)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if result is None:
        raise HTTPException(status_code=404, detail="找不到义项")
    return result


@app.patch("/api/mastery/fragments/{fragment_id}")
def mastery_update_fragment(fragment_id: int, payload: SenseFragmentUpdateRequest):
    try:
        result = update_fragment(fragment_id, payload.fragment)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if result is None:
        raise HTTPException(status_code=404, detail="找不到解释片段")
    return result


@app.patch("/api/mastery/senses/{sense_id}/fragments/{source_order}")
def mastery_correct_fragment(sense_id: int, source_order: int, payload: SenseFragmentRequest):
    try:
        result = update_fragment_by_order(sense_id, source_order, payload.fragment)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if result is None:
        raise HTTPException(status_code=404, detail="找不到解释片段")
    return result

@app.get("/api/mastery/question")
def mastery_question():
    return create_question()

@app.post("/api/mastery/answer")
def mastery_answer(payload: MasteryAnswerRequest):
    try:
        result = submit_answer(payload.question_id, payload.answer)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    if result is None:
        raise HTTPException(status_code=404, detail="题目不存在或已失效")
    return result

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
