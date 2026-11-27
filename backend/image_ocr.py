"""Gemma 4 vision transcription through the local Ollama HTTP API.

The historical module name is retained so this can replace the previous
server-side OCR implementation without changing its import path elsewhere.
Uploaded images are normalized in memory and are never persisted.
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
import time
import urllib.error
import urllib.request
import warnings
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_IMAGE_PIXELS = 40_000_000
MAX_IMAGE_SIDE = 2560
OLLAMA_BASE_URL = os.getenv("LEXICON_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("LEXICON_OLLAMA_MODEL", "gemma4:latest").strip()
OLLAMA_TIMEOUT = max(30, int(os.getenv("LEXICON_OLLAMA_TIMEOUT", "300")))


class VisionEngineUnavailable(RuntimeError):
    """Ollama is unavailable or the configured vision model is not installed."""


# Backwards-compatible name used by the existing FastAPI endpoint.
OCREngineUnavailable = VisionEngineUnavailable


TRANSCRIPTION_PROMPT = r"""
你是一个严格的手写英语单词表转录器。请直接观察附带的整张图片，只做忠实抄录，不要解释图片。

这是一张通常具有左右两栏的英语单词表：左侧是英文单词，右侧是中文释义。请按纸面从上到下的顺序，把同一横行左右两侧的内容配成一条记录。

必须遵守：
1. 逐行抄录图片中真实可见的内容。不要总结、润色、翻译、扩写、改写或补充。
2. 英文单词尽量按纸面原样抄写。不要根据词典知识擅自纠正拼写；后续程序会单独进行词典校对。
3. 中文释义必须忠实保留原字、标点、括号、顿号、逗号、箭头及其顺序。不要擅自补充同义词，也不要删掉你认为重复或不重要的内容。
4. 词性缩写（例如 n.、v.、adj.、adv.）属于右栏释义的一部分，必须放在 meaning 字段中，不要另建词性列。
5. 不要把图片变成 Markdown 表格，不要添加 DATE、Words、Meaning 等表头，不要输出总结或说明。
6. 一条纸面横行对应一个 rows 元素。不要把一行拆成多条，也不要把相邻行合并。若左右文字难以对应，仍按最接近的行位置配对，并将 uncertain 设为 true。
7. 对无法辨认的局部不要凭语义猜成确定内容。能辨认的部分照实抄录；无法辨认的片段用 [?] 标记，并将 uncertain 设为 true。note 简短说明疑点；没有疑点时 note 留空。
8. 忽略日期填写区域、页面标题、表头以及装饰线条。只输出真正的单词记录。
9. 如果整张图没有可辨认的单词记录，返回空 rows 数组。

只输出符合给定 JSON Schema 的 JSON 对象，不要输出 Markdown 代码围栏或其他文字。
""".strip()

ROW_SCHEMA = {
    "type": "object",
    "properties": {
        "rows": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "word": {"type": "string"},
                    "meaning": {"type": "string"},
                    "uncertain": {"type": "boolean"},
                    "note": {"type": "string"},
                },
                "required": ["index", "word", "meaning", "uncertain", "note"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["rows"],
    "additionalProperties": False,
}


def _request_json(path: str, payload: dict | None = None, timeout: int = 8) -> dict:
    url = f"{OLLAMA_BASE_URL}{path}"
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers, method="GET" if body is None else "POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Ollama 返回了非 JSON 对象")
        if data.get("error"):
            raise VisionEngineUnavailable(str(data["error"]))
        return data
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:1000]
        except Exception:
            pass
        try:
            detail_json = json.loads(detail)
            detail = str(detail_json.get("error") or detail_json.get("detail") or detail)
        except Exception:
            pass
        raise VisionEngineUnavailable(f"Ollama HTTP {exc.code}: {detail or exc.reason}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise VisionEngineUnavailable(
            f"无法连接本机 Ollama（{OLLAMA_BASE_URL}）。请确认 Ollama 正在运行。原始错误：{exc}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise VisionEngineUnavailable("Ollama 返回内容不是有效 JSON。请检查 Ollama 服务状态。") from exc


def _canonical_model_name(name: str) -> str:
    value = (name or "").strip().casefold()
    if not value:
        return ""
    return value if ":" in value else value + ":latest"


def get_vision_status() -> dict:
    """Return whether Ollama is reachable and the configured model is installed."""
    try:
        data = _request_json("/api/tags", timeout=5)
        models = [str(item.get("name") or item.get("model") or "") for item in data.get("models", [])]
        configured = _canonical_model_name(OLLAMA_MODEL)
        installed = any(_canonical_model_name(name) == configured for name in models)
        return {
            "ok": True,
            "available": installed,
            "ollama": OLLAMA_BASE_URL,
            "model": OLLAMA_MODEL,
            "models": models,
            "message": "Gemma 4 已就绪。" if installed else f"Ollama 已连接，但没有找到 {OLLAMA_MODEL}。请先运行：ollama pull {OLLAMA_MODEL}",
        }
    except VisionEngineUnavailable as exc:
        return {
            "ok": False,
            "available": False,
            "ollama": OLLAMA_BASE_URL,
            "model": OLLAMA_MODEL,
            "models": [],
            "message": str(exc),
        }


def _prepare_image(image_bytes: bytes) -> tuple[bytes, tuple[int, int]]:
    if not image_bytes:
        raise ValueError("没有收到图片文件")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
            image = Image.open(io.BytesIO(image_bytes))
            image = ImageOps.exif_transpose(image)
            image.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("无法读取图片，请上传有效的 JPG、PNG 或 WebP 文件") from exc
    except (Image.DecompressionBombWarning, Image.DecompressionBombError) as exc:
        raise ValueError("图片像素过大，服务器已拒绝处理") from exc

    if image.width * image.height > MAX_IMAGE_PIXELS:
        raise ValueError("图片像素过大，请压缩到 4000 万像素以内")
    original_size = image.size
    image = image.convert("RGB")
    longest = max(image.size)
    if longest > MAX_IMAGE_SIDE:
        scale = MAX_IMAGE_SIDE / longest
        image = image.resize(
            (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
            Image.Resampling.LANCZOS,
        )

    output = io.BytesIO()
    image.save(output, format="JPEG", quality=94, optimize=True, progressive=False)
    return output.getvalue(), original_size


def _extract_json_object(content: str) -> dict:
    text = (content or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    try:
        value = json.loads(text)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass

    # Defensive fallback for models/versions that wrap JSON in a short sentence.
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            value = json.loads(text[start:end + 1])
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            pass
    raise ValueError("Gemma 4 没有返回有效的 JSON 识别结果。请重试；若持续发生，请更新 Ollama。")


def _clean_rows(payload: dict) -> list[dict]:
    raw_rows = payload.get("rows")
    if not isinstance(raw_rows, list):
        raise ValueError("Gemma 4 返回的数据缺少 rows 数组")
    rows = []
    for position, item in enumerate(raw_rows, start=1):
        if not isinstance(item, dict):
            continue
        word = str(item.get("word") or "").strip()
        meaning = str(item.get("meaning") or "").strip()
        note = str(item.get("note") or "").strip()
        if len(word) > 120:
            word = word[:120]
            note = (note + "；英文内容过长，已截断").strip("；")
        if len(meaning) > 800:
            meaning = meaning[:800]
            note = (note + "；释义过长，已截断").strip("；")
        if not word and not meaning:
            continue
        rows.append({
            "index": int(item.get("index") or position),
            "word": word,
            "meaning": meaning,
            "uncertain": bool(item.get("uncertain", False)),
            "note": note,
        })
    # Keep the model's visible top-to-bottom order rather than re-sorting by index.
    return rows[:200]


def recognize_vocabulary_image(image_bytes: bytes, top: float | None = None, divider: float | None = None) -> dict:
    """Send one complete vocabulary photo to Gemma 4 through local Ollama."""
    prepared, original_size = _prepare_image(image_bytes)
    image_b64 = base64.b64encode(prepared).decode("ascii")

    payload = {
        "model": OLLAMA_MODEL,
        "messages": [
            {
                "role": "system",
                "content": "你只负责忠实转录图片，不负责词典纠错。绝对不能根据语义推测并改写原文。",
            },
            {
                "role": "user",
                "content": TRANSCRIPTION_PROMPT,
                "images": [image_b64],
            },
        ],
        "format": ROW_SCHEMA,
        "stream": False,
        "think": False,
        "keep_alive": "5m",
        "options": {
            "temperature": 0,
            "num_ctx": 8192,
            "num_predict": 4096,
        },
    }

    started = time.perf_counter()
    response = _request_json("/api/chat", payload, timeout=OLLAMA_TIMEOUT)
    elapsed_ms = round((time.perf_counter() - started) * 1000)
    content = ((response.get("message") or {}).get("content") or "").strip()
    if not content:
        raise ValueError("Gemma 4 返回了空结果。请检查模型是否支持图片输入，并确认使用的是 Gemma 4 视觉版本。")
    parsed = _extract_json_object(content)
    rows = _clean_rows(parsed)
    if not rows:
        raise ValueError("Gemma 4 没有识别到单词行。请上传更清晰、完整且文字无遮挡的图片。")

    return {
        "rows": rows,
        "row_count": len(rows),
        "uncertain_count": sum(1 for row in rows if row["uncertain"]),
        "engine": "Ollama · Gemma 4 多模态识别",
        "model": response.get("model") or OLLAMA_MODEL,
        "elapsed_ms": elapsed_ms,
        "image_size": {"width": original_size[0], "height": original_size[1]},
    }
