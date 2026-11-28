import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import db


class VocabularyBookMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "test.sqlite3"
        self.path_patch = patch.object(db, "DATABASE_PATH", self.database_path)
        self.path_patch.start()
        db.init_favorites_table()

    def tearDown(self):
        self.path_patch.stop()
        self.temp_dir.cleanup()

    def test_legacy_favorites_migrate_to_read_book_without_losing_dates(self):
        with db.get_db() as conn:
            conn.execute(
                "INSERT INTO favorites(word, added_at, study_date) VALUES (?, ?, ?)",
                ("abandon", "2026-11-20T10:00:00+00:00", "2026-11-19"),
            )
            conn.commit()

        db.init_vocabulary_books()

        with db.get_db() as conn:
            rows = conn.execute(
                "SELECT word, mode, added_at, study_date FROM vocabulary_books"
            ).fetchall()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["word"], "abandon")
        self.assertEqual(rows[0]["mode"], "read")
        self.assertEqual(rows[0]["study_date"], "2026-11-19")

    def test_same_word_can_belong_to_both_books_independently(self):
        db.init_vocabulary_books()
        with db.get_db() as conn:
            conn.execute(
                "INSERT INTO vocabulary_books(word, mode, added_at, study_date) VALUES (?, 'read', ?, ?)",
                ("light", "2026-11-20T10:00:00+00:00", "2026-11-20"),
            )
            conn.execute(
                "INSERT INTO vocabulary_books(word, mode, added_at, study_date) VALUES (?, 'write', ?, ?)",
                ("light", "2026-11-25T10:00:00+00:00", "2026-11-25"),
            )
            conn.commit()
            rows = conn.execute(
                "SELECT mode, study_date FROM vocabulary_books WHERE word=? COLLATE NOCASE ORDER BY mode",
                ("light",),
            ).fetchall()

        self.assertEqual(
            [(row["mode"], row["study_date"]) for row in rows],
            [("read", "2026-11-20"), ("write", "2026-11-25")],
        )


if __name__ == "__main__":
    unittest.main()
