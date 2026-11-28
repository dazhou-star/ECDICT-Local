import unittest

from backend.mastery import split_translation


class TranslationSplitTests(unittest.TestCase):
    def test_groups_explicit_parts_of_speech(self):
        source = "n. 罩；风帽\nv. 覆盖；用头巾包"
        self.assertEqual(
            split_translation(source),
            [("n", ["罩", "风帽"]), ("v", ["覆盖", "用头巾包"])],
        )

    def test_plain_translation_splits_semicolon_fragments(self):
        self.assertEqual(
            split_translation("地址；住址；门牌"),
            [("", ["地址", "住址", "门牌"])],
        )

    def test_duplicate_fragments_are_removed(self):
        self.assertEqual(
            split_translation("n. 地址；住址；地址"),
            [("n", ["地址", "住址"])],
        )

    def test_empty_translation_returns_no_senses(self):
        self.assertEqual(split_translation("  "), [])


if __name__ == "__main__":
    unittest.main()
