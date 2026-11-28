import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import db
from backend.search import get_word_comparisons, search_words


class SearchRecommendationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "search.sqlite3"
        self.path_patch = patch.object(db, "DATABASE_PATH", self.database_path)
        self.path_patch.start()
        with db.get_db() as conn:
            conn.execute(
                "CREATE TABLE words (word TEXT PRIMARY KEY COLLATE NOCASE, "
                "translation TEXT, definition TEXT, phonetic TEXT, pos TEXT, frq TEXT)"
            )
            conn.executemany(
                "INSERT INTO words(word,translation,frq) VALUES (?,?,?)",
                [
                    ("apple", "苹果", "30"),
                    ("apply", "申请；应用", "10"),
                    ("application", "应用；申请", "5"),
                    ("abandon", "放弃", "100"),
                    ("abacus", "算盘", "200"),
                ],
            )
            conn.execute("CREATE TABLE resemble_raw (line_no INTEGER PRIMARY KEY, content TEXT)")
            conn.executemany(
                "INSERT INTO resemble_raw(line_no,content) VALUES (?,?)",
                [
                    (1, "% quite, rather, pretty, fairly"),
                    (2, "这组词都有“相当，颇”的意思，其区别是："),
                    (3, "- quite: 含义比 fairly 稍强。"),
                    (4, ""),
                    (5, "% repeal, delete, cancel, abolish"),
                    (6, "- repeal: 撤销法案。"),
                ],
            )
            conn.commit()

    def tearDown(self):
        self.path_patch.stop()
        self.temp_dir.cleanup()

    def test_prefix_suggestions_prioritize_frequency(self):
        items = search_words("app", limit=3)
        self.assertEqual([item["word"] for item in items], ["application", "apply", "apple"])

    def test_typo_query_returns_fuzzy_candidate(self):
        items = search_words("abndon", limit=10)
        abandon = next(item for item in items if item["word"] == "abandon")
        self.assertEqual(abandon["match_type"], "fuzzy")
        self.assertGreaterEqual(abandon["similarity"], 0.9)

    def test_comparison_group_requires_exact_word_membership(self):
        groups = get_word_comparisons("quite")
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["title"], "quite, rather, pretty, fairly")
        self.assertIn("quite: 含义比 fairly 稍强", groups[0]["content"])
        self.assertEqual(get_word_comparisons("quiet"), [])


if __name__ == "__main__":
    unittest.main()
