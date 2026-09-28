import unittest

from utils.text import (extract_faq, flesch_reading_ease, match_item, normalize_markdown, parse_blocks,
                        parse_inline, reading_time, slugify, word_count)


class TextUtilsTest(unittest.TestCase):
    def test_slugify_removes_brackets_and_symbols(self):
        self.assertEqual(slugify("Harvalem Waterfall( Arvalem Waterfall)"), "harvalem-waterfall")
        self.assertEqual(slugify("Rock & Roll", "_"), "rock_and_roll")
        self.assertEqual(slugify("???"), "untitled")

    def test_word_count_ignores_markdown(self):
        self.assertEqual(word_count("# Title\n\nThis is **bold** text."), 5)
        self.assertEqual(reading_time(400), 2)

    def test_parse_blocks_and_inline(self):
        blocks = parse_blocks("# T\n\n## H2\n\npara one\ncontinues\n\n- a\n- b\n\n1. x\n")
        self.assertEqual([k for k, _ in blocks], ["h1", "h2", "para", "bullet", "bullet", "number"])
        self.assertEqual(blocks[2][1], "para one continues")
        self.assertEqual(parse_inline("a **b** *c*"), [("a ", False, False), ("b", True, False), (" ", False, False), ("c", False, True)])

    def test_normalize_markdown_fixes_llm_slips(self):
        fixed = normalize_markdown("```markdown\n##**Bold Heading**\n> quote\n\n\n\ntext\n```")
        self.assertIn("## Bold Heading", fixed)
        self.assertNotIn(">", fixed)
        self.assertNotIn("```", fixed)

    def test_faq_extraction_supports_both_styles(self):
        md = "## Frequently Asked Questions\n\n### Is it open?\n\nYes.\n\n**When to go?**\nIn winter.\n\n## Conclusion\n\nbye"
        self.assertEqual(extract_faq(md), [("Is it open?", "Yes."), ("When to go?", "In winter.")])

    def test_match_item_handles_brackets_and_case(self):
        names = ["Dudhsagar Falls", "Harvalem Waterfall( Arvalem Waterfall)", "Kuske Waterfall"]
        self.assertEqual(match_item("Harvalem Waterfall (Arvalem Waterfall)", names), names[1])
        self.assertEqual(match_item("Kuske Waterfall", names), "Kuske Waterfall")
        self.assertIsNone(match_item("How to Reach", names))

    def test_flesch_in_range(self):
        self.assertTrue(0 <= flesch_reading_ease("The cat sat on the mat. It was a sunny day.") <= 100)


if __name__ == "__main__":
    unittest.main()
