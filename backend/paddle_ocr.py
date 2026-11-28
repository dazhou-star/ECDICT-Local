"""PaddleOCR-first hybrid transcription for vocabulary photos."""
from __future__ import annotations

import importlib.util
import io
import re
import threading
import time

from PIL import Image

from .image_ocr import _prepare_image, _recognize_with_gemma

_ENGINE = None
_ENGINE_LOCK = threading.RLock()

_POS_ONLY = re.compile(r"^(?:n|v|vt|vi|adj|adv|a|prep|pron|conj|int|art|aux|abbr|num|det|pl|sing)\.?$", re.I)
_ENGLISH = re.compile(r"[A-Za-z][A-Za-z'-]*")
_CJK = re.compile(r"[\u3400-\u9fff]+")


def get_paddle_status() -> dict:
    has_ocr = importlib.util.find_spec("paddleocr") is not None
    has_paddle = importlib.util.find_spec("paddle") is not None
    return {
        "available": has_ocr and has_paddle,
        "paddleocr_installed": has_ocr,
        "paddle_installed": has_paddle,
        "device": "cpu",
        "message": "PaddleOCR CPU 已安装" if has_ocr and has_paddle else "当前 Python 环境未检测到完整的 PaddleOCR / PaddlePaddle 安装",
    }


def _get_engine():
    global _ENGINE
    if _ENGINE is not None:
        return _ENGINE
    with _ENGINE_LOCK:
        if _ENGINE is None:
            from paddleocr import PaddleOCR
            _ENGINE = PaddleOCR(use_angle_cls=True, lang="ch", use_gpu=False, cpu_threads=4, show_log=False)
    return _ENGINE


def _extract_paddle_rows(image_bytes: bytes) -> tuple[list[dict], float]:
    import numpy as np

    prepared, original_size = _prepare_image(image_bytes)
    with Image.open(io.BytesIO(prepared)) as image:
        image_array = np.asarray(image.convert("RGB"))

    # PaddleOCR's model instance is shared, but calls are serialized for CPU/RAM safety.
    with _ENGINE_LOCK:
        engine = _get_engine()
        result = engine.ocr(image_array, cls=True)

    boxes = []
    for page in result or []:
        for item in page or []:
            try:
                polygon, rec = item
                text, score = str(rec[0]).strip(), float(rec[1])
                xs = [float(point[0]) for point in polygon]
                ys = [float(point[1]) for point in polygon]
                if text:
                    boxes.append({
                        "text": text, "score": score,
                        "x1": min(xs), "x2": max(xs),
                        "y": (min(ys) + max(ys)) / 2,
                        "height": max(1.0, max(ys) - min(ys)),
                    })
            except (TypeError, ValueError, IndexError):
                continue

    if not boxes:
        return [], 0.0

    median_height = sorted(box["height"] for box in boxes)[len(boxes) // 2]
    y_tolerance = max(8.0, median_height * 0.65)
    lines = []
    for box in sorted(boxes, key=lambda value: (value["y"], value["x1"])):
        candidates = [(abs(line["center"] - box["y"]), line) for line in lines[-5:]]
        candidates = [(distance, line) for distance, line in candidates if distance <= y_tolerance]
        if candidates:
            _, line = min(candidates, key=lambda value: value[0])
            line["boxes"].append(box)
            line["center"] = sum(part["y"] for part in line["boxes"]) / len(line["boxes"])
        else:
            lines.append({"center": box["y"], "boxes": [box]})

    rows = []
    for line in sorted(lines, key=lambda value: value["center"]):
        parts = sorted(line["boxes"], key=lambda value: value["x1"])
        word = ""
        word_box_right = None
        meaning_parts = []

        # The first non-POS Latin token is treated as the headword. Remaining
        # text fragments on that visual row form the Chinese gloss.
        for part in parts:
            tokens = _ENGLISH.findall(part["text"])
            cjk = _CJK.findall(part["text"])
            candidate = next((token for token in tokens if not _POS_ONLY.fullmatch(token)), "")
            if not word and candidate:
                word = candidate.lower()
                word_box_right = part["x2"]
                remainder = part["text"].replace(candidate, "", 1).strip(" :-\t")
                if remainder:
                    meaning_parts.append(remainder)
                continue
            if word:
                # Keep everything to the right of the headword, including POS labels.
                if part["x1"] >= (word_box_right or 0) - 3:
                    meaning_parts.append(part["text"])
            elif cjk:
                meaning_parts.append(part["text"])

        if not word:
            continue
        meaning = " ".join(meaning_parts).strip()
        scores = [part["score"] for part in parts]
        confidence = sum(scores) / max(1, len(scores))
        uncertain = confidence < 0.88 or not meaning
        rows.append({
            "index": len(rows) + 1,
            "word": word,
            "meaning": meaning,
            "uncertain": uncertain,
            "note": "PaddleOCR 置信度较低或未识别到对应中文释义" if uncertain else "",
            "confidence": round(confidence, 4),
        })

    average = sum(row["confidence"] for row in rows) / max(1, len(rows))
    return rows[:200], average


def recognize_with_paddle(image_bytes: bytes, top=None, divider=None) -> dict:
    """PaddleOCR handles normal text; Gemma 4 is the fallback for weak OCR."""
    started = time.perf_counter()
    paddle_error = None
    paddle_rows = []
    confidence = 0.0
    try:
        paddle_rows, confidence = _extract_paddle_rows(image_bytes)
    except Exception as exc:
        paddle_error = f"PaddleOCR 识别不可用：{type(exc).__name__}: {exc}"

    # Use the vision model for handwriting/poor layout or low-confidence OCR.
    uncertain_ratio = sum(1 for row in paddle_rows if row["uncertain"]) / max(1, len(paddle_rows))
    needs_vision = not paddle_rows or confidence < 0.88 or uncertain_ratio >= 0.30
    if needs_vision:
        try:
            result = _recognize_with_gemma(image_bytes, top=top, divider=divider)
            result["engine"] = "Gemma 4 辅助识别（PaddleOCR 未达到可靠阈值）"
            result["paddle_confidence"] = round(confidence, 4) if paddle_rows else None
            result["paddle_rows"] = len(paddle_rows)
            result["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
            if paddle_error:
                result["note"] = paddle_error
            return result
        except Exception as vision_error:
            if not paddle_rows:
                if paddle_error:
                    raise RuntimeError(f"{paddle_error}；Gemma 4 备用识别也失败：{vision_error}") from vision_error
                raise
            # Gemma may be unavailable. Keep PaddleOCR's result but explicitly
            # mark every row for human verification rather than losing OCR text.
            for row in paddle_rows:
                row["uncertain"] = True
                row["note"] = (row["note"] + "；Gemma 4 辅助识别不可用，需人工核对").strip("；")

    return {
        "rows": paddle_rows,
        "row_count": len(paddle_rows),
        "uncertain_count": sum(1 for row in paddle_rows if row["uncertain"]),
        "engine": "PaddleOCR 2.x · 中文/英文 CPU OCR",
        "model": "PaddleOCR ch + CPU",
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
        "paddle_confidence": round(confidence, 4),
        "note": paddle_error or "",
    }
