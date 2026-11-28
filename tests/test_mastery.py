import unittest

from backend.mastery import (
    split_translation, _too_similar_meaning, _pos_family,
    _clean_fragment, _split_translation_for_distractors,
    _is_excluded_distractor_sense,
)


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


class DistractorQualityTests(unittest.TestCase):
    def test_punctuation_residue_is_removed(self):
        self.assertEqual(_clean_fragment("，木材；"), "木材")
        self.assertEqual(_clean_fragment("西)艾森曼"), "西艾森曼")
        self.assertEqual(_clean_fragment("（木材）"), "（木材）")
        self.assertEqual(_clean_fragment("木材）"), "木材")
        self.assertEqual(_clean_fragment("“木材"), "木材")

    def test_distractor_parser_preserves_domain_and_language_markers(self):
        parsed = _split_translation_for_distractors(
            "n. 木材；[化] 化学反应；[日] 日语借词；[美] 美国英语用法"
        )
        self.assertIn(("n", "木材", ""), parsed)
        self.assertIn(("n", "化学反应", "化"), parsed)
        self.assertIn(("n", "日语借词", "日"), parsed)
        self.assertIn(("n", "美国英语用法", "美"), parsed)

    def test_hard_exclusions_are_applied_to_senses_not_entire_entry(self):
        self.assertTrue(_is_excluded_distractor_sense("化学反应", "化"))
        self.assertTrue(_is_excluded_distractor_sense("计算机术语", "计"))
        self.assertTrue(_is_excluded_distractor_sense("日语借词", "日"))
        self.assertTrue(_is_excluded_distractor_sense("某人姓氏", ""))
        self.assertTrue(_is_excluded_distractor_sense("普通释义", "", {"name"}))
        self.assertFalse(_is_excluded_distractor_sense("木材", ""))
        self.assertFalse(_is_excluded_distractor_sense("美国英语用法", "美"))

    def test_near_synonym_options_are_flagged_as_too_similar(self):
        self.assertTrue(_too_similar_meaning("特征", "特点"))
        self.assertTrue(_too_similar_meaning("特征", "特性"))

    def test_clearly_different_meanings_are_not_flagged(self):
        self.assertFalse(_too_similar_meaning("特征", "苹果"))
        self.assertFalse(_too_similar_meaning("远征", "苹果"))

    def test_verb_forms_share_a_pos_family(self):
        self.assertEqual(_pos_family("vt"), _pos_family("vi"))
        self.assertEqual(_pos_family("v"), _pos_family("vt"))


if __name__ == "__main__":
    unittest.main()
