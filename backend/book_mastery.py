"""Independent mastery and review plans for read/write vocabulary books."""
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4
import json
import random
import re

from .db import get_db
from .mastery import init_mastery_tables, split_translation, INTERVALS, POS_NAMES, _pos_family, create_question, submit_answer

def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def initialize_books_mastery():
    init_mastery_tables()
    stats = {}
    with get_db() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS write_question_progress (
            question_id TEXT PRIMARY KEY REFERENCES mastery_questions(question_id) ON DELETE CASCADE,
            attempts INTEGER NOT NULL DEFAULT 0
        )""")
        rows = conn.execute(
            """SELECT b.word,b.mode,b.study_date,w.translation
               FROM vocabulary_books b JOIN words w ON w.word=b.word COLLATE NOCASE
               ORDER BY b.mode,b.study_date,b.word"""
        ).fetchall()
        for book in rows:
            word, mode = book["word"], book["mode"]
            first_due = (date.fromisoformat(book["study_date"] or date.today().isoformat()) + timedelta(days=1)).isoformat()
            groups = split_translation(book["translation"] or "")
            existing = conn.execute(
                "SELECT id,source_order,review_state FROM word_senses WHERE word=? COLLATE NOCASE AND mode=?",
                (word,mode)
            ).fetchall()
            if existing:
                for sense in existing:
                    conn.execute(
                        """INSERT OR IGNORE INTO sense_mastery(sense_id,mode,next_review_date,interval_days)
                           VALUES(?,?,?,1)""",(sense["id"],mode,first_due)
                    )
                continue
            for order,(pos,fragments) in enumerate(groups):
                cur=conn.execute(
                    """INSERT INTO word_senses(word,mode,pos,sense_text,source_translation,source_order,review_state,created_at)
                       VALUES(?,?,?,?,?,?,?,?)""",
                    (word,mode,pos,"；".join(fragments),book["translation"] or "",order,"active",_now())
                )
                sid=cur.lastrowid
                for fi,fragment in enumerate(fragments):
                    conn.execute("INSERT INTO sense_fragments(sense_id,fragment,source_order) VALUES(?,?,?)",(sid,fragment,fi))
                conn.execute(
                    "INSERT OR IGNORE INTO sense_mastery(sense_id,mode,next_review_date,interval_days) VALUES(?,?,?,1)",
                    (sid,mode,first_due)
                )
            stats[mode]=stats.get(mode,0)+1
        conn.commit()
    return stats

def get_overview(mode):
    if mode not in ("read","write"): raise ValueError("无效单词本类型")
    today=date.today().isoformat()
    with get_db() as conn:
        books=conn.execute("SELECT COUNT(*) FROM vocabulary_books WHERE mode=?",(mode,)).fetchone()[0]
        senses=conn.execute("SELECT COUNT(*) FROM word_senses s JOIN vocabulary_books b ON b.word=s.word COLLATE NOCASE AND b.mode=s.mode WHERE s.mode=?",(mode,)).fetchone()[0]
        active=conn.execute("SELECT COUNT(*) FROM word_senses s JOIN vocabulary_books b ON b.word=s.word COLLATE NOCASE AND b.mode=s.mode WHERE s.mode=? AND s.review_state='active'",(mode,)).fetchone()[0]
        draft=conn.execute("SELECT COUNT(*) FROM word_senses s JOIN vocabulary_books b ON b.word=s.word COLLATE NOCASE AND b.mode=s.mode WHERE s.mode=? AND s.review_state='draft'",(mode,)).fetchone()[0]
        ignored=conn.execute("SELECT COUNT(*) FROM word_senses s JOIN vocabulary_books b ON b.word=s.word COLLATE NOCASE AND b.mode=s.mode WHERE s.mode=? AND s.review_state='ignored'",(mode,)).fetchone()[0]
        due=conn.execute("""SELECT COUNT(*) FROM sense_mastery m JOIN word_senses s ON s.id=m.sense_id JOIN vocabulary_books b ON b.word=s.word COLLATE NOCASE AND b.mode=s.mode WHERE m.mode=? AND s.review_state='active' AND m.next_review_date<=?""",(mode,today)).fetchone()[0]
        history=conn.execute("SELECT COUNT(*) FROM sense_test_history WHERE mode=?",(mode,)).fetchone()[0]
        upcoming=conn.execute("""SELECT MIN(m.next_review_date) FROM sense_mastery m JOIN word_senses s ON s.id=m.sense_id JOIN vocabulary_books b ON b.word=s.word COLLATE NOCASE AND b.mode=s.mode WHERE m.mode=? AND s.review_state='active' AND m.next_review_date>?""",(mode,today)).fetchone()[0]
    return {"mode":mode,"words":books,"senses":senses,"active":active,"draft":draft,"ignored":ignored,
            "due":due,"tests":history,"next_review_date":upcoming}

def list_book_senses(mode,state="all",limit=1000):
    if mode not in ("read","write"): raise ValueError("无效单词本类型")
    with get_db() as conn:
        rows=conn.execute(
            """SELECT s.id,s.word,s.pos,s.sense_text,s.source_order,s.review_state,
                      m.next_review_date,m.last_test_date,m.streak,m.interval_days,m.status,
                      m.total_correct,m.total_wrong,
                      GROUP_CONCAT(f.fragment,'；') AS fragments
               FROM word_senses s JOIN vocabulary_books b ON b.word=s.word COLLATE NOCASE AND b.mode=s.mode
               LEFT JOIN sense_mastery m ON m.sense_id=s.id AND m.mode=s.mode
               LEFT JOIN sense_fragments f ON f.sense_id=s.id AND f.enabled=1
               WHERE s.mode=? AND (?='all' OR s.review_state=?)
               GROUP BY s.id ORDER BY COALESCE(m.next_review_date,'9999-12-31'),s.word COLLATE NOCASE,s.source_order
               LIMIT ?""",(mode,state,state,max(1,min(limit,2000)))
        ).fetchall()
    return [dict(r) for r in rows]

def set_sense_state(sense_id,mode,state):
    if mode not in ("read","write") or state not in ("draft","active","ignored"):
        raise ValueError("无效的单词本类型或义项状态")
    with get_db() as conn:
        row=conn.execute(
            """SELECT s.id,b.study_date FROM word_senses s JOIN vocabulary_books b
               ON b.word=s.word COLLATE NOCASE AND b.mode=s.mode WHERE s.id=? AND s.mode=?""",
            (sense_id,mode)
        ).fetchone()
        if not row:return None
        conn.execute("UPDATE word_senses SET review_state=? WHERE id=?",(state,sense_id))
        if state=="active":
            due=(date.fromisoformat(row["study_date"] or date.today().isoformat())+timedelta(days=1)).isoformat()
            conn.execute(
                """INSERT OR IGNORE INTO sense_mastery(sense_id,mode,next_review_date,interval_days)
                   VALUES(?,?,?,1)""",(sense_id,mode,due)
            )
        conn.commit()
    return {"id":sense_id,"mode":mode,"review_state":state}

def next_write_question(include_future=False):
    today=date.today().isoformat()
    with get_db() as conn:
        due=conn.execute(
            """SELECT s.id,s.word,s.pos,m.next_review_date
               FROM word_senses s JOIN vocabulary_books b ON b.word=s.word COLLATE NOCASE AND b.mode=s.mode
               JOIN sense_mastery m ON m.sense_id=s.id AND m.mode=s.mode
               WHERE s.mode='write' AND s.review_state='active' AND (?=1 OR m.next_review_date<=?)
               ORDER BY CASE WHEN m.next_review_date<=? THEN 0 ELSE 1 END,m.next_review_date,m.streak,RANDOM() LIMIT 1""",(int(bool(include_future)),today,today)
        ).fetchone()
        if not due:
            upcoming=conn.execute(
                """SELECT MIN(m.next_review_date) FROM sense_mastery m JOIN word_senses s ON s.id=m.sense_id
                   JOIN vocabulary_books b ON b.word=s.word COLLATE NOCASE AND b.mode=s.mode
                   WHERE s.mode='write' AND s.review_state='active' AND m.next_review_date>?""",(today,)
            ).fetchone()[0]
            return {"done":True,"next_review_date":upcoming}
        fragments=conn.execute(
            "SELECT id,fragment FROM sense_fragments WHERE sense_id=? AND enabled=1 ORDER BY source_order",
            (due["id"],)
        ).fetchall()
        if not fragments:return {"done":True,"message":"该义项没有可用释义"}
        frag=random.choice(fragments)
        qid=uuid4().hex
        conn.execute(
            """INSERT INTO mastery_questions(question_id,sense_id,fragment_id,mode,prompt,correct_answer,options_json,created_at)
               VALUES(?,?,?,'write',?,?,?,?)""",
            (qid,due["id"],frag["id"],frag["fragment"],due["word"],"[]",_now())
        )
        conn.execute("INSERT INTO write_question_progress(question_id,attempts) VALUES(?,0)", (qid,))
        conn.commit()
        return {"done":False,"question_id":qid,"prompt":frag["fragment"],
                "word":due["word"],"pos":POS_NAMES.get(due["pos"],due["pos"]),"due_date":due["next_review_date"],
                "is_unknown": bool(conn.execute("SELECT 1 FROM word_recognition WHERE word=? COLLATE NOCASE AND is_unknown=1",(due["word"],)).fetchone())}

def answer_write_question(question_id,answer):
    answer=(answer or "").strip()
    today=date.today()
    with get_db() as conn:
        q=conn.execute("SELECT * FROM mastery_questions WHERE question_id=? AND mode='write'",(question_id,)).fetchone()
        if not q:return None
        if q["answered_at"]:raise ValueError("这道题已经提交过")
        correct=answer.casefold()==q["correct_answer"].casefold()
        now=_now()
        conn.execute(
            """INSERT INTO sense_test_history(sense_id,fragment_id,mode,fragment_snapshot,selected_answer,
               correct_answer,options_json,is_correct,tested_at) VALUES(?,?,'write',?,?,?,?,?,?)""",
            (q["sense_id"],q["fragment_id"],q["prompt"],answer,q["correct_answer"],"[]",int(correct),now)
        )
        conn.execute("UPDATE mastery_questions SET answered_at=? WHERE question_id=?",(now,question_id))
        m=conn.execute("SELECT * FROM sense_mastery WHERE sense_id=? AND mode='write'",(q["sense_id"],)).fetchone()
        if correct:
            streak=m["streak"]+1
            uncovered=conn.execute(
                """SELECT COUNT(*) FROM sense_fragments f LEFT JOIN sense_test_history h
                   ON h.fragment_id=f.id AND h.mode='write' AND h.is_correct=1
                   WHERE f.sense_id=? AND f.enabled=1 AND h.id IS NULL""",(q["sense_id"],)
            ).fetchone()[0]
            interval=1 if uncovered else INTERVALS[min(streak,len(INTERVALS)-1)]
            status="stable" if streak>=6 and not uncovered else "learning"
            right,wrong=m["total_correct"]+1,m["total_wrong"]
        else:
            streak,interval,status=0,1,"learning"
            right,wrong=m["total_correct"],m["total_wrong"]+1
        next_date=(today+timedelta(days=interval)).isoformat()
        conn.execute(
            """UPDATE sense_mastery SET next_review_date=?,last_test_date=?,streak=?,interval_days=?,
               status=?,total_correct=?,total_wrong=? WHERE sense_id=? AND mode='write'""",
            (next_date,today.isoformat(),streak,interval,status,right,wrong,q["sense_id"])
        )
        conn.commit()
    return {"correct":correct,"correct_answer":q["correct_answer"] if not correct else None,
            "next_review_date":next_date,"streak":streak,"interval_days":interval,"status":status}


def _meaning_key(value):
    return "".join(re.findall(r"[\u3400-\u9fff]", value or ""))


def _candidate_has_prompt_meaning(conn, candidate, prompt, target_pos):
    """Check a proposed synonym against the local ECDICT entry and POS."""
    row = conn.execute(
        "SELECT translation FROM words WHERE word=? COLLATE NOCASE LIMIT 1",
        (candidate,),
    ).fetchone()
    if not row:
        return False
    target = _meaning_key(prompt)
    if not target:
        return False
    target_family = _pos_family(target_pos)
    for pos, fragments in split_translation(row["translation"] or ""):
        if target_family and _pos_family(pos) != target_family:
            continue
        for fragment in fragments:
            gloss = _meaning_key(fragment)
            if gloss and (target == gloss or target in gloss or gloss in target):
                return True
    return False


def guess_write_question(question_id, answer):
    """Accept dictionary-supported synonyms without penalizing them, then hint toward the target."""
    answer = (answer or "").strip()
    if not answer:
        raise ValueError("请输入一个英文单词")
    with get_db() as conn:
        q = conn.execute(
            "SELECT q.*,s.pos FROM mastery_questions q JOIN word_senses s ON s.id=q.sense_id "
            "WHERE q.question_id=? AND q.mode='write'",
            (question_id,),
        ).fetchone()
        if not q:
            return None
        if q["answered_at"]:
            raise ValueError("这道题已经提交过")
        if answer.casefold() == q["correct_answer"].casefold():
            exact = True
            synonym = False
            attempts = 0
        else:
            exact = False
            synonym = _candidate_has_prompt_meaning(conn, answer, q["prompt"], q["pos"])
            progress = conn.execute(
                "SELECT attempts FROM write_question_progress WHERE question_id=?",
                (question_id,),
            ).fetchone()
            attempts = progress["attempts"] if progress else 0
            if synonym:
                attempts += 1
                conn.execute(
                    """INSERT INTO write_question_progress(question_id,attempts) VALUES(?,?)
                       ON CONFLICT(question_id) DO UPDATE SET attempts=excluded.attempts""",
                    (question_id, attempts),
                )
                conn.commit()
    if exact:
        result = answer_write_question(question_id, answer)
        return {"finished": True, "accepted_synonym": False, **(result or {})}
    if synonym:
        target = q["correct_answer"]
        stage = min(attempts, 4)
        if stage == 1:
            hint = f"目标词首字母是 “{target[0]}”。"
        elif stage == 2:
            hint = f"目标词共 {len(target)} 个字母。"
        elif stage == 3:
            hint = f"目标词前两个字母是 “{target[:2]}”。"
        else:
            pattern = target[:2] + "·" * max(0, len(target)-3) + (target[-1] if len(target) > 2 else "")
            hint = f"目标词的字母结构：{pattern}（中间部分尚未揭示）。"
        return {
            "finished": False, "accepted_synonym": True, "hint_stage": stage,
            "feedback": f"“{answer}”也有“{q['prompt']}”这个意思，不算错。继续接近本题目标词！",
            "hint": hint,
        }
    result = answer_write_question(question_id, answer)
    return {"finished": True, "accepted_synonym": False, **(result or {})}

def _week_range(today=None):
    today=today or date.today()
    delta=(6-today.weekday())%7
    if delta==0:delta=7
    start=today+timedelta(days=delta)
    return start,start+timedelta(days=5)

def _balance(groups,start,days=6):
    buckets=[[] for _ in range(days)]
    loads=[0]*days
    total=sum(max(1,g["weight"]) for g in groups)
    target=max(1,total/days)
    cap=max(1,int(target*1.35+0.9999))
    for g in sorted(groups,key=lambda x:(x["due"],x["word"].casefold())):
        weight=max(1,g["weight"])
        due=max(0,min(days-1,(date.fromisoformat(g["due"])-start).days))
        choices=[i for i in range(days) if loads[i]+weight<=cap] or list(range(days))
        idx=min(choices,key=lambda i:((loads[i]+weight)/target)+abs(i-due)*0.04)
        buckets[idx].append(g);loads[idx]+=weight
    return buckets

def export_book_print(mode):
    if mode not in ("read","write"):raise ValueError("无效单词本类型")
    start,end=_week_range()
    labels=["周日","周一","周二","周三","周四","周五"]
    days=[{"date":(start+timedelta(days=i)).isoformat(),"label":labels[i],"words":[],"exercises":[],"answers":[]} for i in range(6)]
    with get_db() as conn:
        senses=conn.execute(
            """SELECT s.id AS sense_id,s.word,s.pos,m.next_review_date
               FROM word_senses s JOIN sense_mastery m ON m.sense_id=s.id AND m.mode=s.mode
               JOIN vocabulary_books b ON b.word=s.word COLLATE NOCASE AND b.mode=s.mode
               WHERE s.mode=? AND s.review_state='active' AND m.next_review_date<=?
               ORDER BY m.next_review_date,s.word COLLATE NOCASE,s.source_order""",(mode,end.isoformat())
        ).fetchall()
        by_word={}
        for s in senses:
            k=s["word"].casefold()
            g=by_word.setdefault(k,{"word":s["word"],"due":s["next_review_date"],"senses":[],"weight":0})
            g["due"]=min(g["due"],s["next_review_date"]);g["senses"].append(s)
        counts={r["sense_id"]:r["n"] for r in conn.execute(
            "SELECT sense_id,COUNT(*) n FROM sense_fragments WHERE enabled=1 GROUP BY sense_id"
        ).fetchall()}
        for g in by_word.values():
            g["weight"]=sum(max(1,counts.get(s["sense_id"],0)) for s in g["senses"])
        buckets=_balance(list(by_word.values()),start)
        word_rows={}
        for r in conn.execute(
            """SELECT b.word AS book_word,w.* FROM vocabulary_books b LEFT JOIN words w
               ON w.word=b.word COLLATE NOCASE WHERE b.mode=?""",(mode,)
        ).fetchall():
            d=dict(r);word=d.pop("book_word",None) or d.get("word") or "";d["word"]=word;word_rows[word.casefold()]=d
        for day,groups in zip(days,buckets):
            for g in groups:
                if g["word"].casefold() in word_rows:day["words"].append(word_rows[g["word"].casefold()])
                if mode=="write":
                    # A spelling exercise is per word, not per sense fragment.
                    # Combine distinct prompts from all active senses into one hint.
                    prompts=[]
                    seen_prompts=set()
                    for s in g["senses"]:
                        fs=conn.execute("SELECT fragment FROM sense_fragments WHERE sense_id=? AND enabled=1 ORDER BY source_order",(s["sense_id"],)).fetchall()
                        for f in fs:
                            prompt=(f["fragment"] or "").strip()
                            key=prompt.casefold()
                            if prompt and key not in seen_prompts:
                                seen_prompts.add(key)
                                prompts.append(prompt)
                    if prompts:
                        combined_prompt="；".join(prompts)
                        pos=" / ".join(dict.fromkeys(s["pos"] for s in g["senses"] if s["pos"]))
                        day["exercises"].append({"word":g["word"],"pos":pos,"prompt":combined_prompt})
                        day["answers"].append({"word":g["word"],"prompt":combined_prompt})
            day["words"].sort(key=lambda x:x["word"].casefold())
    return {"mode":mode,"week_start":start.isoformat(),"week_end":end.isoformat(),"days":days,
            "counts":{"words":sum(len(d["words"]) for d in days),
                      "exercises":sum(len(d["exercises"]) for d in days),
                      "scheduled_senses":len(senses),
                      "daily_word_counts":[len(d["words"]) for d in days],
                      "daily_exercise_counts":[len(d["exercises"]) for d in days]}}
