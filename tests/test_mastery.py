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


    def test_literal_backslash_newlines_and_pos_markers(self):
        source = r"n. 动力, 动量\n[化] 动量\nvt. 投掷, 抛, 摇摆"
        self.assertEqual(
            split_translation(source),
            [("n", ["动力", "动量"]), ("vt", ["投掷", "抛", "摇摆"])],
        )

    def test_toss_translation_splits_into_individual_glosses(self):
        source = (
            "n. 动力, 动量\\n[化] 动量"
            "\\nvt. 投掷, 抛, 摇摆, 震荡, 掷钱币决定"
            "\\nvi. 被到处扔, 摇摆, 颠簸, 辗转, 掷钱币决定某事"
        )
        groups = split_translation(source)
        self.assertEqual(groups[0], ("n", ["动力", "动量"]))
        self.assertIn("投掷", groups[1][1])
        self.assertIn("掷钱币决定某事", groups[2][1])
        self.assertTrue(all(len(fragment) < 30 for _, fragments in groups for fragment in fragments))


if __name__ == "__main__":
    unittest.main()
