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
        """)
        conn.commit()


def _clean_fragment(value):
    value = re.sub(r"<[^>]*>", " ", value or "")
    value = re.sub(r"^\s*\[[^]]{1,16}\]\s*", "", value)
    value = re.sub(r"\s+", " ", value).strip(" ，,；;。")
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



def _pos_family(pos):
    pos = (pos or "").lower()
    if pos == "n": return "n"
    if pos in ("v", "vt", "vi"): return "v"
    if pos in ("adj", "a"): return "adj"
    if pos in ("adv", "ad"): return "adv"
    return pos


def _meaning_similarity(left, right):
    a = set(re.findall(r"[\u3400-\u9fff]", left or ""))
    b = set(re.findall(r"[\u3400-\u9fff]", right or ""))
    if not a or not b:
        return 0.0
    return len(a & b) / max(1, len(a | b))


def _dictionary_distractors(conn, correct, target_pos, target_word, limit=3):
    """Find hard distractors from all ECDICT entries, not just favorites."""
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(words)")}
    if "translation" not in columns:
        return []
    chars = list(dict.fromkeys(
        char for char in re.findall(r"[\u3400-\u9fff]", correct)
        if char not in "的了是和与及或其一个不在有为"
    ))
    random.shuffle(chars)
    rows_by_word = {}
    # Shared Chinese characters produce a useful, bounded semantic neighborhood.
    for char in chars[:4]:
        rows = conn.execute(
            "SELECT word, translation FROM words WHERE translation LIKE ? AND word<>? COLLATE NOCASE LIMIT 120",
            ("%" + char + "%", target_word),
        ).fetchall()
        for row in rows:
            rows_by_word.setdefault(row["word"].casefold(), row)
    # If that neighborhood is small, sample a bounded window from the entire dictionary.
    if len(rows_by_word) < 80:
        maximum = conn.execute("SELECT COALESCE(MAX(rowid),0) FROM words").fetchone()[0]
        if maximum:
            start = random.randint(1, max(1, maximum))
            sampled = conn.execute(
                "SELECT word, translation FROM words WHERE rowid>=? AND translation<>'' AND word<>? COLLATE NOCASE LIMIT 700",
                (start, target_word),
            ).fetchall()
            for row in sampled:
                rows_by_word.setdefault(row["word"].casefold(), row)

    target_family = _pos_family(target_pos)
    correct_norm = _norm(correct)
    ranked = []
    for row in rows_by_word.values():
        for candidate_pos, fragments in split_translation(row["translation"] or ""):
            if _pos_family(candidate_pos) != target_family:
                continue
            for value in fragments:
                key = _norm(value)
                if not key or key == correct_norm or len(value) < 2:
                    continue
                if key in correct_norm or correct_norm in key:
                    continue
                overlap = _meaning_similarity(correct, value)
                length_score = 1.0 - min(1.0, abs(len(value) - len(correct)) / max(5, len(correct) * 1.5))
                # Prefer similar-length phrases with partial lexical overlap, not duplicates.
                if overlap < 0.12 or overlap > 0.72:
                    continue
                ranked.append((overlap * 0.65 + length_score * 0.35, value))
    ranked.sort(key=lambda item: item[0], reverse=True)
    selected, seen = [], {correct_norm}
    for _, value in ranked:
        key = _norm(value)
        if key in seen:
            continue
        seen.add(key)
        selected.append(value)
        if len(selected) >= limit:
            break
    return selected


def create_question():
    today = date.today().isoformat()
    with get_db() as conn:
        # Lazily initialize missing review rows without changing favorites.study_date.
        active = conn.execute(
            """SELECT s.id, s.word, s.sense_text, s.pos, f.study_date
               FROM word_senses s JOIN favorites f ON f.word=s.word COLLATE NOCASE
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
               FROM word_senses s JOIN favorites fav ON fav.word=s.word COLLATE NOCASE
               JOIN sense_mastery m ON m.sense_id=s.id AND m.mode='read'
               WHERE s.review_state='active' AND m.next_review_date<=?
               ORDER BY m.next_review_date ASC, m.streak ASC, RANDOM() LIMIT 30""",
            (today,),
        ).fetchall()
        if not due_rows:
            conn.commit()
            upcoming = conn.execute(
                "SELECT MIN(m.next_review_date) FROM sense_mastery m JOIN word_senses s ON s.id=m.sense_id "
                "JOIN favorites f ON f.word=s.word COLLATE NOCASE WHERE m.mode='read' AND m.next_review_date>?",
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
