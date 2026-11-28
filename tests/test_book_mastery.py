import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from backend import db
from backend.book_mastery import initialize_books_mastery, get_overview, list_book_senses, next_write_question, answer_write_question, guess_write_question, create_question


class IndependentBookMasteryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "mastery.sqlite3"
        self.path_patch = patch.object(db, "DATABASE_PATH", self.database_path)
        self.path_patch.start()
        db.init_favorites_table()
        db.init_vocabulary_books()
        with db.get_db() as conn:
            conn.execute("CREATE TABLE words (word TEXT PRIMARY KEY COLLATE NOCASE, translation TEXT)")
            conn.execute("INSERT INTO words(word,translation) VALUES ('light','n. 光；灯\nv. 点燃')")
            study_date = (date.today() - timedelta(days=1)).isoformat()
            for mode in ("read", "write"):
                conn.execute(
                    "INSERT INTO vocabulary_books(word,mode,added_at,study_date) VALUES ('light',?,?,?)",
                    (mode, "2026-11-28T00:00:00+00:00", study_date),
                )
            conn.commit()
        initialize_books_mastery()

    def tearDown(self):
        self.path_patch.stop()
        self.temp_dir.cleanup()

    def test_same_word_has_separate_senses_and_review_rows(self):
        read = list_book_senses("read")
        write = list_book_senses("write")
        self.assertEqual({x["word"] for x in read}, {"light"})
        self.assertEqual({x["word"] for x in write}, {"light"})
        self.assertNotEqual({x["id"] for x in read}, {x["id"] for x in write})
        with db.get_db() as conn:
            modes = conn.execute(
                "SELECT DISTINCT mode FROM sense_mastery ORDER BY mode"
            ).fetchall()
        self.assertEqual([x["mode"] for x in modes], ["read", "write"])

    def test_reinitialization_preserves_existing_read_mastery_and_history(self):
        with db.get_db() as conn:
            read_id = conn.execute(
                "SELECT id FROM word_senses WHERE word='light' AND mode='read' ORDER BY id LIMIT 1"
            ).fetchone()["id"]
            conn.execute(
                "UPDATE sense_mastery SET total_correct=7, streak=4, next_review_date='2030-01-01' WHERE sense_id=? AND mode='read'",
                (read_id,),
            )
            conn.execute(
                """INSERT INTO sense_test_history(sense_id,fragment_id,mode,fragment_snapshot,
                   selected_answer,correct_answer,options_json,is_correct,tested_at)
                   SELECT ?,id,'read','光','光','光','[]',1,'2026-11-28T00:00:00+00:00'
                   FROM sense_fragments WHERE sense_id=? LIMIT 1""",
                (read_id, read_id),
            )
            conn.commit()
        initialize_books_mastery()
        with db.get_db() as conn:
            mastery = conn.execute(
                "SELECT total_correct,streak,next_review_date FROM sense_mastery WHERE sense_id=? AND mode='read'",
                (read_id,),
            ).fetchone()
            history = conn.execute(
                "SELECT COUNT(*) FROM sense_test_history WHERE sense_id=? AND mode='read'",
                (read_id,),
            ).fetchone()[0]
        self.assertEqual(tuple(mastery), (7, 4, "2030-01-01"))
        self.assertEqual(history, 1)

    def test_dictionary_supported_synonym_is_not_marked_wrong_and_unlocks_hints(self):
        with db.get_db() as conn:
            conn.execute("INSERT INTO words(word,translation) VALUES ('dispute','n. 争论；辩论')")
            sense = conn.execute(
                "SELECT id FROM word_senses WHERE word='light' AND mode='write' ORDER BY id LIMIT 1"
            ).fetchone()
            fragment = conn.execute(
                "SELECT id FROM sense_fragments WHERE sense_id=? ORDER BY id LIMIT 1",
                (sense["id"],),
            ).fetchone()
            conn.execute(
                """INSERT INTO mastery_questions
                   (question_id,sense_id,fragment_id,mode,prompt,correct_answer,options_json,created_at)
                   VALUES ('synonym-test',?,?, 'write','争论','argument','[]','2026-11-28T00:00:00+00:00')""",
                (sense["id"], fragment["id"]),
            )
            conn.execute("INSERT INTO write_question_progress(question_id,attempts) VALUES('synonym-test',0)")
            conn.commit()

        first = guess_write_question("synonym-test", "dispute")
        second = guess_write_question("synonym-test", "dispute")
        self.assertTrue(first["accepted_synonym"])
        self.assertFalse(first["finished"])
        self.assertIn("首字母", first["hint"])
        self.assertIn("字母", second["hint"])
        self.assertEqual(get_overview("write")["tests"], 0)

    def test_read_book_multiple_choice_detection_is_available_again(self):
        with patch("backend.mastery._dictionary_distractors", return_value=["苹果", "树木", "书本"]):
            question = create_question()
        self.assertFalse(question["done"])
        self.assertEqual(len(question["options"]), 4)
        self.assertTrue(question["word"])


    def test_write_question_uses_chinese_prompt_and_spelling_answer(self):
        question = next_write_question()
        self.assertFalse(question["done"])
        self.assertTrue(question["prompt"])
        result = answer_write_question(question["question_id"], "not-the-answer")
        self.assertFalse(result["correct"])
        self.assertEqual(result["interval_days"], 1)
        self.assertEqual(get_overview("read")["tests"], 0)
        self.assertEqual(get_overview("write")["tests"], 1)


if __name__ == "__main__":
    unittest.main()
