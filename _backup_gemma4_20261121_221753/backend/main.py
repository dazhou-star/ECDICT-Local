from datetime import datetime, timezone
from typing import List
from pydantic import BaseModel
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from .config import FRONTEND_DIR, HOST, PORT
from .search import search_words, get_word, random_word, get_stats, get_similar, get_root, get_lemma
from .db import get_db, init_favorites_table

class FavoriteRequest(BaseModel):
    word: str


class FavoriteImportRequest(BaseModel):
    words: List[str]


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clean_word(value: str) -> str:
    word = value.strip()
    if not word:
        raise HTTPException(status_code=422, detail="单词不能为空")
    if len(word) > 100:
        raise HTTPException(status_code=422, detail="单词长度不能超过 100 个字符")
    return word


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
