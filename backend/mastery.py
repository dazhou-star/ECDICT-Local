"""Read-mode vocabulary mastery: conservative ECDICT parsing and spaced review."""
from datetime import date, datetime, timedelta, timezone
from collections import defaultdict
import random
import re
from uuid import uuid4

from .db import get_db

POS_RE = re.compile(r"(?i)(?<![A-Za-z])(adj|adv|vt|vi|v|n|prep|pron|conj|int|art|aux|abbr|num|det|ad|a)\.\s*")
POS_NAMES = {
    "n": "名词", "v": "动词", "vt": "及物动词", "vi": "不及物动词",
    "adj": "形容词", "a": "形容词", "adv": "副词", "ad": "副词", "prep": "介词", "pron": "代词",
    "conj": "连词", "int": "感叹词", "art": "冠词", "aux": "助动词",
    "abbr": "缩写", "num": "数词", "det": "限定词",
}
INTERVALS = [1, 3, 7, 15, 30, 60, 90, 120]


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def init_mastery_tables():
    with get_db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS word_senses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            word TEXT NOT NULL COLLATE NOCASE,
            mode TEXT NOT NULL DEFAULT 'read',
            pos TEXT NOT NULL DEFAULT '',
            sense_text TEXT NOT NULL,
            source_translation TEXT NOT NULL DEFAULT '',
            source_order INTEGER NOT NULL DEFAULT 0,
            review_state TEXT NOT NULL DEFAULT 'draft',
            created_at TEXT NOT NULL,
            UNIQUE(word, mode, source_order)
        );
        CREATE INDEX IF NOT EXISTS idx_word_senses_word ON word_senses(word COLLATE NOCASE);
        CREATE INDEX IF NOT EXISTS idx_word_senses_state ON word_senses(review_state, mode);
        CREATE TABLE IF NOT EXISTS sense_fragments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sense_id INTEGER NOT NULL REFERENCES word_senses(id) ON DELETE CASCADE,
            fragment TEXT NOT NULL,
            source_order INTEGER NOT NULL DEFAULT 0,
            enabled INTEGER NOT NULL DEFAULT 1
        );
        CREATE INDEX IF NOT EXISTS idx_fragments_sense ON sense_fragments(sense_id, enabled);
        CREATE TABLE IF NOT EXISTS sense_mastery (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sense_id INTEGER NOT NULL REFERENCES word_senses(id) ON DELETE CASCADE,
            mode TEXT NOT NULL DEFAULT 'read',
            next_review_date TEXT NOT NULL,
            last_test_date TEXT,
            streak INTEGER NOT NULL DEFAULT 0,
            interval_days INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'learning',
            total_correct INTEGER NOT NULL DEFAULT 0,
            total_wrong INTEGER NOT NULL DEFAULT 0,
            UNIQUE(sense_id, mode)
        );
        CREATE INDEX IF NOT EXISTS idx_mastery_due ON sense_mastery(mode, next_review_date);
        CREATE TABLE IF NOT EXISTS mastery_questions (
            question_id TEXT PRIMARY KEY,
            sense_id INTEGER NOT NULL REFERENCES word_senses(id) ON DELETE CASCADE,
            fragment_id INTEGER NOT NULL REFERENCES sense_fragments(id) ON DELETE CASCADE,
            mode TEXT NOT NULL,
            prompt TEXT NOT NULL,
            correct_answer TEXT NOT NULL,
            options_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            answered_at TEXT
        );
        CREATE TABLE IF NOT EXISTS sense_test_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sense_id INTEGER NOT NULL REFERENCES word_senses(id) ON DELETE CASCADE,
            fragment_id INTEGER REFERENCES sense_fragments(id) ON DELETE SET NULL,
            mode TEXT NOT NULL,
            fragment_snapshot TEXT NOT NULL,
            selected_answer TEXT NOT NULL,
            correct_answer TEXT NOT NULL,
            options_json TEXT NOT NULL,
            is_correct INTEGER NOT NULL,
            tested_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_history_sense ON sense_test_history(sense_id, tested_at DESC);
        CREATE TABLE IF NOT EXISTS word_recognition (
            word TEXT PRIMARY KEY COLLATE NOCASE,
            is_unknown INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL
        );
        """)
        conn.commit()


def set_word_unknown(word, unknown=True, mode=None, question_id=None):
    """Persist a word-level unknown marker; quiz marking also records a wrong answer."""
    word = (word or "").strip()
    if not word:
        raise ValueError("单词不能为空")
    if mode not in (None, "read", "write"):
        raise ValueError("无效检测类型")
    today = date.today().isoformat()
    tested_at = now_iso()
    with get_db() as conn:
        if unknown:
            conn.execute(
                """INSERT INTO word_recognition(word,is_unknown,updated_at) VALUES(?,1,?)
                   ON CONFLICT(word) DO UPDATE SET is_unknown=1,updated_at=excluded.updated_at""",
                (word, tested_at),
            )
        else:
            conn.execute("DELETE FROM word_recognition WHERE word=? COLLATE NOCASE", (word,))
        recorded = False
        if unknown and question_id:
            params = [question_id]
            sql = "SELECT q.*,s.word FROM mastery_questions q JOIN word_senses s ON s.id=q.sense_id WHERE q.question_id=?"
            if mode:
                sql += " AND q.mode=?"
                params.append(mode)
            q = conn.execute(sql, params).fetchone()
            if q and not q["answered_at"] and q["word"].casefold() == word.casefold():
                conn.execute(
                    """INSERT INTO sense_test_history
                       (sense_id,fragment_id,mode,fragment_snapshot,selected_answer,correct_answer,options_json,is_correct,tested_at)
                       VALUES(?,?,?,?,?,?,?,0,?)""",
                    (q["sense_id"], q["fragment_id"], q["mode"], q["prompt"],
                     "不认识", q["correct_answer"], q["options_json"], tested_at),
                )
                conn.execute("UPDATE mastery_questions SET answered_at=? WHERE question_id=?",
                             (tested_at, question_id))
                conn.execute(
                    """UPDATE sense_mastery SET next_review_date=?,last_test_date=?,streak=0,
                       interval_days=1,status='learning',total_wrong=total_wrong+1
                       WHERE sense_id=? AND mode=?""",
                    (today, today, q["sense_id"], q["mode"]),
                )
                recorded = True
        if unknown and not recorded:
            conn.execute(
                """UPDATE sense_mastery SET next_review_date=?,streak=0,interval_days=1,status='learning'
                   WHERE sense_id IN (SELECT id FROM word_senses WHERE word=? COLLATE NOCASE)
                     AND mode IN ('read','write')""",
                (today, word),
            )
        conn.commit()
    return {"word": word, "is_unknown": bool(unknown), "recorded_as_wrong": recorded}


def is_word_unknown(conn, word):
    return bool(conn.execute(
        "SELECT 1 FROM word_recognition WHERE word=? COLLATE NOCASE AND is_unknown=1",
        (word,),
    ).fetchone())


def _clean_fragment(value):
    value = re.sub(r"<[^>]*>", " ", value or "")
    value = re.sub(r"^\s*\[[^]]{1,16}\]\s*", "", value)
    value = re.sub(r"\s+", " ", value).strip()
    # Remove punctuation fragments left behind by splitting dictionary glosses.
    value = value.strip(" ，,；;。.!?！？:：、…“”‘’")
    # Remove unmatched bracket characters without disturbing balanced examples.
    pairs = {"(": ")", "（": "）", "[": "]", "【": "】", "{": "}"}
    for opening, closing in pairs.items():
        while value.count(opening) > value.count(closing):
            index = value.rfind(opening)
            value = value[:index] + value[index + 1:]
        while value.count(closing) > value.count(opening):
            index = value.find(closing)
            value = value[:index] + value[index + 1:]
    value = value.strip(" ，,；;。.!?！？:：、…“”‘’")
    return value.strip()


def _split_glosses(raw):
    # ECDICT commonly uses commas, enumeration commas and semicolons between glosses.
    pieces = re.split(r"[；;，,、\n]+", raw or "")
    cleaned = [_clean_fragment(piece) for piece in pieces]
    return list(dict.fromkeys(x for x in cleaned if x and len(x) <= 100))


def split_translation(translation):
    """Split ECDICT translation into POS groups and short, independently tested glosses."""
    text = (translation or "")
    # Normalize literal backslash-n sequences from imported data.
    text = text.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\r", "\n")
    groups = []
    current_pos = ""
    current_parts = []

    def flush():
        nonlocal current_parts
        raw = "\n".join(part for part in current_parts if part).strip()
        fragments = _split_glosses(raw)
        if fragments:
            groups.append((current_pos, fragments))
        current_parts = []

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        matches = list(POS_RE.finditer(line))
        if matches:
            for index, match in enumerate(matches):
                flush()
                current_pos = match.group(1).lower()
                end = matches[index + 1].start() if index + 1 < len(matches) else len(line)
                current_parts.append(line[match.end():end].strip())
        else:
            current_parts.append(line)
    flush()

    if not groups:
        fragments = _split_glosses(text)
        if fragments:
            groups.append(("", fragments))
    return groups

def prepare_favorites():
    created_words = 0
    created_senses = 0
    activated_senses = 0
    with get_db() as conn:
        favorites = conn.execute(
            "SELECT f.word, f.study_date, w.translation FROM favorites f "
            "JOIN words w ON w.word = f.word COLLATE NOCASE ORDER BY f.study_date, f.word"
        ).fetchall()
        for favorite in favorites:
            word = favorite["word"]
            first_due = (
                date.fromisoformat(favorite["study_date"] or date.today().isoformat())
                + timedelta(days=1)
            ).isoformat()
            existing = conn.execute(
                "SELECT id, review_state, source_order, pos FROM word_senses "
                "WHERE word = ? COLLATE NOCASE AND mode='read'",
                (word,),
            ).fetchall()
            if existing:
                # Re-split fragments in place; old fragment rows remain for history references.
                groups = split_translation(favorite["translation"] or "")
                for sense in existing:
                    if sense["review_state"] == "draft":
                        conn.execute("UPDATE word_senses SET review_state='active' WHERE id=?", (sense["id"],))
                        activated_senses += 1
                    if sense["review_state"] != "ignored":
                        conn.execute(
                            """INSERT OR IGNORE INTO sense_mastery
                               (sense_id, mode, next_review_date, interval_days)
                               VALUES (?, 'read', ?, 1)""",
                            (sense["id"], first_due),
                        )
                    order = sense["source_order"]
                    if 0 <= order < len(groups):
                        new_pos, new_fragments = groups[order]
                        old_fragments = [row["fragment"] for row in conn.execute(
                            "SELECT fragment FROM sense_fragments WHERE sense_id=? AND enabled=1 ORDER BY source_order",
                            (sense["id"],),
                        ).fetchall()]
                        if old_fragments != new_fragments:
                            conn.execute("UPDATE sense_fragments SET enabled=0 WHERE sense_id=?", (sense["id"],))
                            for fragment_order, fragment in enumerate(new_fragments):
                                conn.execute(
                                    "INSERT INTO sense_fragments(sense_id,fragment,source_order,enabled) VALUES (?,?,?,1)",
                                    (sense["id"], fragment, fragment_order),
                                )
                            conn.execute(
                                "UPDATE word_senses SET pos=?, sense_text=?, source_translation=? WHERE id=?",
                                (new_pos, "；".join(new_fragments), favorite["translation"] or "", sense["id"]),
                            )
                continue

            groups = split_translation(favorite["translation"] or "")
            if not groups:
                continue
            created_words += 1
            for order, (pos, fragments) in enumerate(groups):
                sense_text = "；".join(fragments)
                cursor = conn.execute(
                    """INSERT INTO word_senses
                       (word, mode, pos, sense_text, source_translation, source_order, review_state, created_at)
                       VALUES (?, 'read', ?, ?, ?, ?, 'active', ?)""",
                    (word, pos, sense_text, favorite["translation"] or "", order, now_iso()),
                )
                sense_id = cursor.lastrowid
                for fragment_order, fragment in enumerate(fragments):
                    conn.execute(
                        "INSERT INTO sense_fragments(sense_id, fragment, source_order) VALUES (?, ?, ?)",
                        (sense_id, fragment, fragment_order),
                    )
                conn.execute(
                    """INSERT OR IGNORE INTO sense_mastery
                       (sense_id, mode, next_review_date, interval_days)
                       VALUES (?, 'read', ?, 1)""",
                    (sense_id, first_due),
                )
                created_senses += 1
                activated_senses += 1
        conn.commit()
    return {
        "created_words": created_words,
        "created_senses": created_senses,
        "activated_senses": activated_senses,
    }


def list_senses(state="draft", limit=500):
    with get_db() as conn:
        rows = conn.execute(
            """SELECT s.id, s.word, s.pos, s.sense_text, s.source_translation,
                      s.source_order, s.review_state,
                      GROUP_CONCAT(f.fragment, '；') AS fragments
               FROM word_senses s LEFT JOIN sense_fragments f ON f.sense_id=s.id AND f.enabled=1
               WHERE s.mode='read' AND (?='all' OR s.review_state=?)
               GROUP BY s.id ORDER BY s.word COLLATE NOCASE, s.source_order LIMIT ?""",
            (state, state, max(1, min(limit, 1000))),
        ).fetchall()
        return [dict(row) for row in rows]


def set_sense_state(sense_id, state):
    if state not in ("draft", "active", "ignored"):
        raise ValueError("无效的义项状态")
    with get_db() as conn:
        row = conn.execute(
            "SELECT s.id, s.word, f.study_date FROM word_senses s "
            "LEFT JOIN favorites f ON f.word=s.word COLLATE NOCASE WHERE s.id=? AND s.mode='read'",
            (sense_id,),
        ).fetchone()
        if not row:
            return None
        conn.execute("UPDATE word_senses SET review_state=? WHERE id=?", (state, sense_id))
        if state == "active":
            first_due = (date.fromisoformat(row["study_date"] or date.today().isoformat()) + timedelta(days=1)).isoformat()
            conn.execute(
                """INSERT OR IGNORE INTO sense_mastery
                   (sense_id, mode, next_review_date, interval_days) VALUES (?, 'read', ?, 1)""",
                (sense_id, first_due),
            )
        conn.commit()
        return {"id": sense_id, "review_state": state, "next_review_date": first_due if state == "active" else None}


def add_fragment(sense_id, fragment):
    fragment = _clean_fragment(fragment)
    if not fragment or len(fragment) > 160:
        raise ValueError("解释片段不能为空，且不能超过 160 个字符")
    with get_db() as conn:
        row = conn.execute("SELECT id FROM word_senses WHERE id=? AND mode='read'", (sense_id,)).fetchone()
        if not row:
            return None
        order = conn.execute("SELECT COALESCE(MAX(source_order),-1)+1 FROM sense_fragments WHERE sense_id=?", (sense_id,)).fetchone()[0]
        conn.execute("INSERT INTO sense_fragments(sense_id,fragment,source_order) VALUES (?,?,?)", (sense_id, fragment, order))
        fragments = conn.execute("SELECT fragment FROM sense_fragments WHERE sense_id=? AND enabled=1 ORDER BY source_order", (sense_id,)).fetchall()
        conn.execute("UPDATE word_senses SET sense_text=? WHERE id=?", ("；".join(r["fragment"] for r in fragments), sense_id))
        conn.commit()
        return {"ok": True}



def update_fragment(fragment_id, fragment):
    fragment = _clean_fragment(fragment)
    if not fragment or len(fragment) > 160:
        raise ValueError("解释片段不能为空，且不能超过 160 个字符")
    with get_db() as conn:
        row = conn.execute(
            "SELECT sense_id FROM sense_fragments WHERE id=? AND enabled=1",
            (fragment_id,),
        ).fetchone()
        if not row:
            return None
        conn.execute("UPDATE sense_fragments SET fragment=? WHERE id=?", (fragment, fragment_id))
        fragments = conn.execute(
            "SELECT fragment FROM sense_fragments WHERE sense_id=? AND enabled=1 ORDER BY source_order",
            (row["sense_id"],),
        ).fetchall()
        conn.execute(
            "UPDATE word_senses SET sense_text=? WHERE id=?",
            ("；".join(r["fragment"] for r in fragments), row["sense_id"]),
        )
        conn.commit()
        return {"ok": True}


def update_fragment_by_order(sense_id, source_order, fragment):
    fragment = _clean_fragment(fragment)
    if not fragment or len(fragment) > 160:
        raise ValueError("解释片段不能为空，且不能超过 160 个字符")
    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM sense_fragments WHERE sense_id=? AND source_order=? AND enabled=1",
            (sense_id, source_order),
        ).fetchone()
        if not row:
            return None
        conn.execute("UPDATE sense_fragments SET fragment=? WHERE id=?", (fragment, row["id"]))
        fragments = conn.execute(
            "SELECT fragment FROM sense_fragments WHERE sense_id=? AND enabled=1 ORDER BY source_order",
            (sense_id,),
        ).fetchall()
        conn.execute("UPDATE word_senses SET sense_text=? WHERE id=?",
                     ("；".join(r["fragment"] for r in fragments), sense_id))
        conn.commit()
        return {"ok": True}


def split_fragment_to_sense(sense_id, source_order):
    with get_db() as conn:
        source = conn.execute(
            "SELECT * FROM word_senses WHERE id=? AND mode='read' AND review_state='draft'",
            (sense_id,),
        ).fetchone()
        fragment = conn.execute(
            "SELECT * FROM sense_fragments WHERE sense_id=? AND source_order=? AND enabled=1",
            (sense_id, source_order),
        ).fetchone()
        if not source or not fragment:
            return None
        next_order = conn.execute(
            "SELECT COALESCE(MAX(source_order),-1)+1 FROM word_senses WHERE word=? COLLATE NOCASE AND mode='read'",
            (source["word"],),
        ).fetchone()[0]
        cursor = conn.execute(
            """INSERT INTO word_senses
               (word,mode,pos,sense_text,source_translation,source_order,review_state,created_at)
               VALUES (?,'read',?,?,?,?, 'draft',?)""",
            (source["word"], source["pos"], fragment["fragment"],
             source["source_translation"], next_order, now_iso()),
        )
        new_sense_id = cursor.lastrowid
        conn.execute(
            "UPDATE sense_fragments SET sense_id=?, source_order=0 WHERE id=?",
            (new_sense_id, fragment["id"]),
        )
        remaining = conn.execute(
            "SELECT fragment FROM sense_fragments WHERE sense_id=? AND enabled=1 ORDER BY source_order",
            (sense_id,),
        ).fetchall()
        conn.execute("UPDATE word_senses SET sense_text=? WHERE id=?",
                     ("；".join(row["fragment"] for row in remaining), sense_id))
        conn.commit()
        return {"ok": True, "new_sense_id": new_sense_id}

def _norm(value):
    return re.sub(r"\s+", "", re.sub(r"[，,；;。！？!?、]", "", value or "")).casefold()


def _weighted_fragment(conn, sense_id):
    rows = conn.execute(
        "SELECT id, fragment FROM sense_fragments WHERE sense_id=? AND enabled=1",
        (sense_id,),
    ).fetchall()
    if not rows:
        return None
    weighted = []
    for row in rows:
        recent = conn.execute(
            "SELECT is_correct FROM sense_test_history WHERE fragment_id=? ORDER BY tested_at DESC LIMIT 5",
            (row["id"],),
        ).fetchall()
        wrongs = sum(1 for r in recent if not r["is_correct"])
        recent_correct = sum(1 for r in recent[:2] if r["is_correct"])
        weight = 1.0 + wrongs * 2.5 + max(0, 2 - len(recent)) * 0.8 - recent_correct * 0.25
        weighted.append((row, max(0.25, weight)))
    return random.choices([x[0] for x in weighted], weights=[x[1] for x in weighted], k=1)[0]



DISTRACTOR_DOMAIN_MARKERS = {
    "化", "计", "計", "医", "醫", "生", "物", "数", "數", "地", "理",
    "法", "经", "經", "农", "農", "航", "军", "軍", "建", "电", "電",
    "机", "機", "纺", "紡", "矿", "礦", "气", "氣", "心", "哲", "语",
    "語", "音", "天文", "数学", "物理", "化学", "医学", "计算机",
}
DISTRACTOR_LANGUAGE_MARKERS = {
    "日", "德", "俄", "西", "意", "葡", "荷", "希", "拉", "阿",
    "梵", "波", "土", "印", "波斯", "阿拉伯", "梵文", "拉丁",
}
DISTRACTOR_NAME_RE = re.compile(
    r"(?:人名|姓氏|某人|某某|地名|国名|城市名|人名用|姓)",
    re.IGNORECASE,
)
DISTRACTOR_FOREIGN_RE = re.compile(
    r"(?:来自|源自|借自|借词|外来语|外来词|"
    r"日语|法语|德语|俄语|西班牙语|意大利语|葡萄牙语|荷兰语|"
    r"希腊语|拉丁语|阿拉伯语|梵语|波斯语|土耳其语|印地语|希伯来语)"
)


def _split_translation_for_distractors(translation):
    """Return (pos, clean gloss, leading ECDICT marker) without losing metadata."""
    text = (translation or "")
    text = text.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\r", "\n")
    groups = []
    current_pos = ""
    current_parts = []

    def flush():
        nonlocal current_parts
        raw = "\n".join(part for part in current_parts if part).strip()
        for piece in re.split(r"[；;，,、\n]+", raw):
            piece = re.sub(r"<[^>]*>", " ", piece or "").strip()
            marker_match = re.match(r"^\s*\[([^]]{1,16})\]\s*", piece)
            marker = marker_match.group(1).strip() if marker_match else ""
            if marker_match:
                piece = piece[marker_match.end():]
            fragment = _clean_fragment(piece)
            if fragment and len(fragment) <= 100:
                groups.append((current_pos, fragment, marker))
        current_parts = []

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        matches = list(POS_RE.finditer(line))
        if matches:
            for index, match in enumerate(matches):
                flush()
                current_pos = match.group(1).lower()
                end = matches[index + 1].start() if index + 1 < len(matches) else len(line)
                current_parts.append(line[match.end():end].strip())
        else:
            current_parts.append(line)
    flush()
    if not groups:
        for piece in re.split(r"[；;，,、\\n]+", text):
            piece = (piece or "").strip()
            marker_match = re.match(r"^\s*\[([^]]{1,16})\]\s*", piece)
            marker = marker_match.group(1).strip() if marker_match else ""
            if marker_match:
                piece = piece[marker_match.end():]
            fragment = _clean_fragment(piece)
            if fragment and len(fragment) <= 100:
                groups.append(("", fragment, marker))
    return groups


def _is_excluded_distractor_sense(fragment, marker="", tag_tokens=None):
    """Hard exclusions for names, specialist glosses, and explicitly foreign terms."""
    marker = (marker or "").strip()
    normalized_marker = marker.casefold()
    if normalized_marker in {x.casefold() for x in DISTRACTOR_DOMAIN_MARKERS}:
        return True
    if normalized_marker in {x.casefold() for x in DISTRACTOR_LANGUAGE_MARKERS}:
        return True
    if DISTRACTOR_NAME_RE.search(fragment or "") or DISTRACTOR_FOREIGN_RE.search(fragment or ""):
        return True
    if tag_tokens and tag_tokens.intersection({
        "name", "proper", "person", "place", "人名", "地名", "surname", "firstname"
    }):
        return True
    return False


_INFLECTED_FORM_CACHE = None


def _inflected_forms(conn, candidate_words):
    """Find words listed as inflections in other entries' ECDICT exchange fields."""
    global _INFLECTED_FORM_CACHE
    if not candidate_words:
        return set()
    if _INFLECTED_FORM_CACHE is None:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(words)")}
        if "exchange" not in columns:
            _INFLECTED_FORM_CACHE = set()
        else:
            forms_found = set()
            # ECDICT stores forms on the lemma as typed entries such as p:apples;d:walked.
            for row in conn.execute("SELECT exchange FROM words WHERE exchange IS NOT NULL AND exchange<>''"):
                for item in re.split(r"[;；]", row["exchange"] or ""):
                    if ":" not in item:
                        continue
                    _, forms = item.split(":", 1)
                    for form in re.split(r"[,，/\s]+", forms):
                        form = form.strip().casefold()
                        if form:
                            forms_found.add(form)
            _INFLECTED_FORM_CACHE = forms_found
    return {word.casefold() for word in candidate_words if word.casefold() in _INFLECTED_FORM_CACHE}


def _pos_family(pos):
    pos = (pos or "").lower()
    if pos == "n": return "n"
    if pos in ("v", "vt", "vi"): return "v"
    if pos in ("adj", "a"): return "adj"
    if pos in ("adv", "ad"): return "adv"
    return pos


def _meaning_similarity(left, right):
    """A lightweight character-set similarity check, not a semantic model."""
    a = set(re.findall(r"[\u3400-\u9fff]", left or ""))
    b = set(re.findall(r"[\u3400-\u9fff]", right or ""))
    if not a or not b:
        return 0.0
    return len(a & b) / max(1, len(a | b))


def _too_similar_meaning(left, right, threshold=0.25):
    """Reject exact/contained glosses and obvious lexical near-duplicates."""
    left_key, right_key = _norm(left), _norm(right)
    if not left_key or not right_key:
        return True
    if left_key == right_key or left_key in right_key or right_key in left_key:
        return True
    return _meaning_similarity(left, right) >= threshold


def _dictionary_distractors(conn, correct, target_pos, target_word, limit=3):
    """Prefer useful, common vocabulary while retaining safe fallbacks.

    ECDICT metadata is used in tiers:
    1) high-school (gk) tagged entries;
    2) entries with non-zero frequency (frq), or Oxford 3000 entries;
    3) the wider dictionary only when the preferred pools are insufficient.
    """
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(words)")}
    if "translation" not in columns:
        return []

    maximum = conn.execute("SELECT COALESCE(MAX(rowid),0) FROM words").fetchone()[0]
    if not maximum:
        return []

    metadata_columns = []
    for name in ("frq", "tag", "oxford"):
        if name in columns:
            metadata_columns.append(name)
    select_columns = ["word", "translation"] + metadata_columns
    rows_by_word = {}
    for _ in range(12):
        start = random.randint(1, maximum)
        rows = conn.execute(
            f"SELECT {', '.join(select_columns)} FROM words "
            "WHERE rowid>=? AND translation<>'' AND word<>? COLLATE NOCASE LIMIT 900",
            (start, target_word),
        ).fetchall()
        for row in rows:
            rows_by_word.setdefault(row["word"].casefold(), row)
        if len(rows_by_word) >= 5000:
            break

    target_family = _pos_family(target_pos)
    inflected_words = _inflected_forms(conn, [row["word"] for row in rows_by_word.values()])
    # Keep separate pools so rare dictionary entries are only used as a fallback.
    pools = {
        "same_gk": [], "same_common": [], "same_any": [],
        "other_gk": [], "other_common": [], "other_any": [],
    }
    seen_glosses = {_norm(correct)}
    for row in rows_by_word.values():
        tag = str(row["tag"] or "").casefold() if "tag" in columns else ""
        tag_tokens = set(re.findall(r"[a-z0-9_]+", tag))
        is_high_school = "gk" in tag_tokens
        try:
            has_frequency = int(row["frq"] or 0) > 0 if "frq" in columns else False
        except (TypeError, ValueError):
            has_frequency = False
        try:
            is_oxford = int(row["oxford"] or 0) > 0 if "oxford" in columns else False
        except (TypeError, ValueError):
            is_oxford = False

        # Proper-name entries are excluded before their glosses are considered.
        if tag_tokens.intersection({"name", "proper", "person", "place", "地名", "人名", "surname", "firstname"}):
            continue
        if row["word"].casefold() in inflected_words:
            continue

        if is_high_school:
            tier = "gk"
        elif has_frequency or is_oxford:
            tier = "common"
        else:
            tier = "any"

        for candidate_pos, value, marker in _split_translation_for_distractors(row["translation"] or ""):
            if _is_excluded_distractor_sense(value, marker, tag_tokens):
                continue
            pos_group = "same" if _pos_family(candidate_pos) == target_family else "other"
            destination = pools[f"{pos_group}_{tier}"]
            key = _norm(value)
            if len(value) < 2 or not key or key in seen_glosses:
                continue
            if key in _norm(correct) or _norm(correct) in key:
                continue
            seen_glosses.add(key)
            destination.append(value)

    for pool in pools.values():
        random.shuffle(pool)

    selected = []
    # Same part of speech first; within each POS group, prefer high-school,
    # then frequency/Oxford-backed vocabulary, and only then the full dictionary.
    for pool_name in ("same_gk", "same_common", "same_any",
                      "other_gk", "other_common", "other_any"):
        for value in pools[pool_name]:
            if any(_too_similar_meaning(value, chosen) for chosen in [correct, *selected]):
                continue
            selected.append(value)
            if len(selected) >= limit:
                return selected

    # Last resort: keep the question usable even if strict similarity filtering
    # leaves too few options. Exact duplicates and direct substring variants stay out.
    for pool_name in ("same_gk", "same_common", "same_any",
                      "other_gk", "other_common", "other_any"):
        for value in pools[pool_name]:
            key = _norm(value)
            if not key or any(key == _norm(chosen) for chosen in [correct, *selected]):
                continue
            if key in _norm(correct) or _norm(correct) in key:
                continue
            selected.append(value)
            if len(selected) >= limit:
                return selected
    return selected

def create_question(include_future=False):
    today = date.today().isoformat()
    with get_db() as conn:
        # Lazily initialize missing review rows without changing favorites.study_date.
        active = conn.execute(
            """SELECT s.id, s.word, s.sense_text, s.pos, f.study_date
               FROM word_senses s JOIN vocabulary_books f ON f.word=s.word COLLATE NOCASE AND f.mode='read'
               WHERE s.mode='read' AND s.review_state='active'"""
        ).fetchall()
        for row in active:
            due = (date.fromisoformat(row["study_date"] or today) + timedelta(days=1)).isoformat()
            conn.execute(
                "INSERT OR IGNORE INTO sense_mastery(sense_id,mode,next_review_date) VALUES (?,'read',?)",
                (row["id"], due),
            )
        due_rows = conn.execute(
            """SELECT s.id, s.word, s.pos, s.sense_text, m.next_review_date, m.streak
               FROM word_senses s JOIN vocabulary_books fav ON fav.word=s.word COLLATE NOCASE AND fav.mode='read'
               JOIN sense_mastery m ON m.sense_id=s.id AND m.mode='read'
               WHERE s.mode='read' AND s.review_state='active' AND (?=1 OR m.next_review_date<=?)
               ORDER BY CASE WHEN m.next_review_date<=? THEN 0 ELSE 1 END, m.next_review_date ASC, m.streak ASC, RANDOM() LIMIT 30""",
            (int(bool(include_future)), today, today),
        ).fetchall()
        if not due_rows:
            conn.commit()
            upcoming = conn.execute(
                "SELECT MIN(m.next_review_date) FROM sense_mastery m JOIN word_senses s ON s.id=m.sense_id "
                "JOIN vocabulary_books f ON f.word=s.word COLLATE NOCASE AND f.mode='read' "
                "WHERE m.mode='read' AND m.next_review_date>?",
                (today,),
            ).fetchone()[0]
            return {"done": True, "next_review_date": upcoming}
        sense = due_rows[0]
        fragment = _weighted_fragment(conn, sense["id"])
        if fragment is None:
            return {"done": True, "next_review_date": None, "message": "该义项没有可用解释片段"}
        correct = fragment["fragment"]
        distractors = _dictionary_distractors(conn, correct, sense["pos"], sense["word"], 3)
        options = distractors + [correct]
        random.shuffle(options)
        if len(options) < 2:
            return {"done": True, "next_review_date": None, "message": "全词库中暂时找不到可靠干扰项，本题跳过"}
        question_id = uuid4().hex
        conn.execute(
            """INSERT INTO mastery_questions
               (question_id,sense_id,fragment_id,mode,prompt,correct_answer,options_json,created_at)
               VALUES (?,?,?,'read',?,?,?,?)""",
            (question_id, sense["id"], fragment["id"], sense["word"], correct,
             __import__("json").dumps(options, ensure_ascii=False), now_iso()),
        )
        conn.commit()
        return {
            "done": False, "question_id": question_id, "word": sense["word"],
            "pos": POS_NAMES.get(sense["pos"], sense["pos"]), "options": options,
            "is_unknown": is_word_unknown(conn, sense["word"]),
            "due_date": sense["next_review_date"],
        }


def submit_answer(question_id, selected_answer):
    import json
    today = date.today()
    with get_db() as conn:
        question = conn.execute(
            "SELECT * FROM mastery_questions WHERE question_id=? AND mode='read'",
            (question_id,),
        ).fetchone()
        if not question:
            return None
        if question["answered_at"]:
            raise ValueError("这道题已经提交过")
        options = json.loads(question["options_json"])
        if selected_answer not in options:
            raise ValueError("请选择题目中的一个选项")
        correct = selected_answer == question["correct_answer"]
        tested_at = now_iso()
        conn.execute(
            """INSERT INTO sense_test_history
               (sense_id,fragment_id,mode,fragment_snapshot,selected_answer,correct_answer,options_json,is_correct,tested_at)
               VALUES (?,?,'read',?,?,?,?,?,?)""",
            (question["sense_id"], question["fragment_id"], question["correct_answer"],
             selected_answer, question["correct_answer"], question["options_json"], int(correct), tested_at),
        )
        conn.execute("UPDATE mastery_questions SET answered_at=? WHERE question_id=?", (tested_at, question_id))
        mastery = conn.execute(
            "SELECT * FROM sense_mastery WHERE sense_id=? AND mode='read'",
            (question["sense_id"],),
        ).fetchone()
        if mastery is None:
            mastery = {"streak": 0}
            conn.execute(
                "INSERT INTO sense_mastery(sense_id,mode,next_review_date) VALUES (?,'read',?)",
                (question["sense_id"], today.isoformat()),
            )
        if correct:
            streak = mastery["streak"] + 1
            # Do not lengthen the sense-level interval until every enabled
            # fragment has been answered correctly at least once.
            uncovered = conn.execute(
                """SELECT COUNT(*) FROM sense_fragments f
                   LEFT JOIN sense_test_history h
                     ON h.fragment_id=f.id AND h.is_correct=1
                   WHERE f.sense_id=? AND f.enabled=1 AND h.id IS NULL""",
                (question["sense_id"],),
            ).fetchone()[0]
            interval = 1 if uncovered else INTERVALS[min(streak, len(INTERVALS)-1)]
            status = "stable" if streak >= 6 and not uncovered else "learning"
            total_correct = conn.execute(
                "SELECT total_correct FROM sense_mastery WHERE sense_id=? AND mode='read'",
                (question["sense_id"],),
            ).fetchone()[0] + 1
            total_wrong = mastery["total_wrong"] if "total_wrong" in mastery.keys() else 0
        else:
            streak, interval, status = 0, 1, "learning"
            total_correct = mastery["total_correct"]
            total_wrong = mastery["total_wrong"] + 1
        if correct:
            total_wrong = mastery["total_wrong"]
        next_date = (today + timedelta(days=interval)).isoformat()
        conn.execute(
            """UPDATE sense_mastery SET next_review_date=?,last_test_date=?,streak=?,
               interval_days=?,status=?,total_correct=?,total_wrong=? WHERE sense_id=? AND mode='read'""",
            (next_date, today.isoformat(), streak, interval, status, total_correct, total_wrong, question["sense_id"]),
        )
        conn.commit()
        return {
            "correct": correct, "correct_answer": question["correct_answer"],
            "selected_answer": selected_answer, "next_review_date": next_date,
            "streak": streak, "interval_days": interval, "status": status,
        }


def overview():
    today = date.today().isoformat()
    with get_db() as conn:
        favorites = conn.execute("SELECT COUNT(*) FROM favorites").fetchone()[0]
        senses = conn.execute("SELECT COUNT(*) FROM word_senses WHERE mode='read'").fetchone()[0]
        drafts = conn.execute("SELECT COUNT(*) FROM word_senses WHERE mode='read' AND review_state='draft'").fetchone()[0]
        active = conn.execute("SELECT COUNT(*) FROM word_senses WHERE mode='read' AND review_state='active'").fetchone()[0]
        due = conn.execute("SELECT COUNT(*) FROM sense_mastery WHERE mode='read' AND next_review_date<=?", (today,)).fetchone()[0]
        total = conn.execute("SELECT COUNT(*) FROM sense_test_history WHERE mode='read'").fetchone()[0]
    return {"favorites": favorites, "senses": senses, "drafts": drafts, "active": active, "due": due, "tests": total}



def _build_print_distractor_index(conn):
    """Sample and parse the dictionary once for the entire weekly print job."""
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(words)")}
    if "translation" not in columns:
        return {}
    maximum = conn.execute("SELECT COALESCE(MAX(rowid),0) FROM words").fetchone()[0]
    if not maximum:
        return {}
    metadata_columns = [name for name in ("frq", "tag", "oxford") if name in columns]
    select_columns = ["word", "translation"] + metadata_columns
    rows_by_word = {}
    for _ in range(12):
        start = random.randint(1, maximum)
        rows = conn.execute(
            f"SELECT {', '.join(select_columns)} FROM words "
            "WHERE rowid>=? AND translation<>'' LIMIT 900",
            (start,),
        ).fetchall()
        for row in rows:
            rows_by_word.setdefault(row["word"].casefold(), row)
        if len(rows_by_word) >= 5000:
            break

    inflected_words = _inflected_forms(conn, [row["word"] for row in rows_by_word.values()])
    pools = {
        "same_gk": [], "same_common": [], "same_any": [],
        "other_gk": [], "other_common": [], "other_any": [],
    }
    for row in rows_by_word.values():
        tag = str(row["tag"] or "").casefold() if "tag" in columns else ""
        tag_tokens = set(re.findall(r"[a-z0-9_]+", tag))
        if tag_tokens.intersection({"name", "proper", "person", "place", "地名", "人名", "surname", "firstname"}):
            continue
        if row["word"].casefold() in inflected_words:
            continue
        is_high_school = "gk" in tag_tokens
        try:
            has_frequency = int(row["frq"] or 0) > 0 if "frq" in columns else False
        except (TypeError, ValueError):
            has_frequency = False
        try:
            is_oxford = int(row["oxford"] or 0) > 0 if "oxford" in columns else False
        except (TypeError, ValueError):
            is_oxford = False
        tier = "gk" if is_high_school else ("common" if has_frequency or is_oxford else "any")
        for candidate_pos, value, marker in _split_translation_for_distractors(row["translation"] or ""):
            if _is_excluded_distractor_sense(value, marker, tag_tokens):
                continue
            key = _norm(value)
            if len(value) < 2 or not key:
                continue
            family = "same" if _pos_family(candidate_pos) else "other"
            # Store the actual POS family so selection can prefer the target POS.
            pools[f"{family}_{tier}"].append((row["word"].casefold(), candidate_pos, value))
    for pool in pools.values():
        random.shuffle(pool)
    return pools


def _select_print_distractors(index, correct, target_pos, target_word, limit=3):
    target_family = _pos_family(target_pos)
    correct_key = _norm(correct)
    selected = []
    selected_keys = {correct_key}
    for wanted_same_pos in (True, False):
        for tier in ('gk', 'common', 'any'):
            candidates = index.get('same_' + tier, []) + index.get('other_' + tier, [])
            for candidate_word, candidate_pos, value in candidates:
                same_pos = _pos_family(candidate_pos) == target_family
                if same_pos != wanted_same_pos:
                    continue
                key = _norm(value)
                if candidate_word == target_word.casefold() or not key or key in selected_keys:
                    continue
                if key in correct_key or correct_key in key:
                    continue
                if _too_similar_meaning(value, correct) or any(_too_similar_meaning(value, chosen) for chosen in selected):
                    continue
                selected.append(value)
                selected_keys.add(key)
                if len(selected) >= limit:
                    return selected
    for tier in ('gk', 'common', 'any'):
        candidates = index.get('same_' + tier, []) + index.get('other_' + tier, [])
        for candidate_word, candidate_pos, value in candidates:
            key = _norm(value)
            if candidate_word == target_word.casefold() or not key or key in selected_keys:
                continue
            if key in correct_key or correct_key in key:
                continue
            selected.append(value)
            selected_keys.add(key)
            if len(selected) >= limit:
                return selected
    return selected

def _next_week_range(today=None):
    """Return the next Sunday-Friday paper week; Saturday is a catch-up buffer."""
    today = today or date.today()
    days_until_sunday = (6 - today.weekday()) % 7
    if days_until_sunday == 0:
        days_until_sunday = 7
    start = today + timedelta(days=days_until_sunday)
    return start, start + timedelta(days=5)


def _distribute_print_words(word_groups, day_count=6):
    """Balance word-level workload while keeping each group near its due date.

    Each group has word, due_date (ISO), and weight (enabled review fragments).
    The weight-based greedy assignment avoids splitting one word across days.
    """
    if not word_groups:
        return [[] for _ in range(day_count)]
    total_weight = sum(max(1, int(item.get("weight", 1))) for item in word_groups)
    target = max(1.0, total_weight / day_count)
    capacity = max(1, int((target * 1.35) + 0.9999))
    buckets = [[] for _ in range(day_count)]
    loads = [0] * day_count

    ordered = sorted(
        word_groups,
        key=lambda item: (item["due_date"], item["word"].casefold())
    )
    for item in ordered:
        due_day = min(day_count - 1, max(0, (date.fromisoformat(item["due_date"]) -
                       date.fromisoformat(ordered[0]["week_start"])).days)) if item.get("week_start") else 0
        weight = max(1, int(item.get("weight", 1)))
        candidates = [i for i in range(day_count) if loads[i] + weight <= capacity]
        if not candidates:
            candidates = list(range(day_count))
        # Workload is the primary balancing objective; distance from the due
        # date is a softer penalty so crowded due dates can be spread sensibly.
        chosen = min(
            candidates,
            key=lambda i: ((loads[i] + weight) / target) + (abs(i - due_day) * 0.04)
        )
        buckets[chosen].append(item)
        loads[chosen] += weight
    return buckets


def export_print_data():
    """Build a read-only, automatically balanced six-day review paper strip."""
    week_start, week_end = _next_week_range()
    start_iso, end_iso = week_start.isoformat(), week_end.isoformat()
    weekday_names = ["周日", "周一", "周二", "周三", "周四", "周五"]
    days = [{
        "date": (week_start + timedelta(days=offset)).isoformat(),
        "label": weekday_names[offset], "words": [], "exercises": [],
    } for offset in range(6)]

    with get_db() as conn:
        due_senses = conn.execute(
            """SELECT s.id AS sense_id, s.word, s.pos, s.source_order,
                      m.next_review_date
               FROM word_senses s
               JOIN sense_mastery m ON m.sense_id=s.id AND m.mode='read'
               JOIN favorites f ON f.word=s.word COLLATE NOCASE
               WHERE s.mode='read' AND s.review_state='active'
                 AND m.next_review_date<=?
               ORDER BY m.next_review_date, s.word COLLATE NOCASE, s.source_order""",
            (end_iso,),
        ).fetchall()

        # Group all due senses of one headword so a word is studied on one day.
        senses_by_word = {}
        for sense in due_senses:
            key = sense["word"].casefold()
            group = senses_by_word.setdefault(key, {
                "word": sense["word"], "due_date": sense["next_review_date"],
                "senses": [], "weight": 0,
            })
            if sense["next_review_date"] < group["due_date"]:
                group["due_date"] = sense["next_review_date"]
            group["senses"].append(sense)

        all_words = {}
        for row in conn.execute(
            """SELECT f.word AS favorite_word, w.* FROM favorites f
               LEFT JOIN words w ON w.word=f.word COLLATE NOCASE"""
        ).fetchall():
            item = dict(row)
            word = item.pop("favorite_word", None) or item.get("word") or ""
            item["word"] = word
            all_words[word.casefold()] = item

        # Fragment count is a better workload estimate than word count alone.
        fragment_counts = {
            row["sense_id"]: row["fragment_count"]
            for row in conn.execute(
                """SELECT sense_id, COUNT(*) AS fragment_count
                   FROM sense_fragments WHERE enabled=1 GROUP BY sense_id"""
            ).fetchall()
        }
        for group in senses_by_word.values():
            for sense in group["senses"]:
                group["weight"] += max(1, fragment_counts.get(sense["sense_id"], 0))

        groups = list(senses_by_word.values())
        for group in groups:
            group["week_start"] = start_iso
        daily_groups = _distribute_print_words(groups, day_count=6)
        candidate_index = _build_print_distractor_index(conn) if due_senses else {}
        skipped = 0
        exercise_count = 0

        for day, groups_for_day in zip(days, daily_groups):
            for group in groups_for_day:
                word_key = group["word"].casefold()
                word_item = all_words.get(word_key)
                if word_item is not None:
                    day["words"].append(word_item)
                for sense in group["senses"]:
                    fragments = conn.execute(
                        """SELECT id, fragment FROM sense_fragments
                           WHERE sense_id=? AND enabled=1 ORDER BY source_order""",
                        (sense["sense_id"],),
                    ).fetchall()
                    if not fragments:
                        skipped += 1
                        continue
                    for fragment in fragments:
                        correct = fragment["fragment"]
                        distractors = _select_print_distractors(
                            candidate_index, correct, sense["pos"], sense["word"], 3
                        )
                        if len(distractors) < 3:
                            skipped += 1
                            continue
                        options = distractors + [correct]
                        random.shuffle(options)
                        day["exercises"].append({
                            "word": sense["word"],
                            "pos": POS_NAMES.get(sense["pos"], sense["pos"]),
                            "options": options,
                            "answer": options.index(correct),
                            "meaning": correct,
                        })
                        exercise_count += 1

    for day in days:
        day["words"].sort(key=lambda item: item["word"].casefold())
    return {
        "week_start": start_iso, "week_end": end_iso, "days": days,
        "counts": {
            "words": sum(len(day["words"]) for day in days),
            "exercises": exercise_count, "skipped_exercises": skipped,
            "scheduled_senses": len(due_senses),
            "daily_word_counts": [len(day["words"]) for day in days],
            "daily_exercise_counts": [len(day["exercises"]) for day in days],
        },
    }
