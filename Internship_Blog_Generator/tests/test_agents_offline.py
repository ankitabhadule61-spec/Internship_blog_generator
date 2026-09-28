import unittest

import config
from agents.editor import EditorAgent
from agents.planner import PlannerAgent
from agents.retrieval import RetrievalAgent
from agents.seo import SEOAgent, trim_to
from agents.writer import WriterAgent
from utils.llm import LLMClient

LLM = LLMClient(offline=True)
S = config.Settings(topic="Best Waterfalls of Goa", offline=True, limit=6)
RES = RetrievalAgent().search(S.topic, limit=6)
OUTLINE = PlannerAgent(LLM).create_outline(S.topic, RES, S)
BLOG = WriterAgent(LLM).write_blog(S.topic, OUTLINE, RES, S)


class OfflineAgentsTest(unittest.TestCase):
    def test_outline_uses_only_dataset_names(self):
        self.assertEqual(sorted(OUTLINE.order), sorted(RES.names()))

    def test_draft_has_one_section_per_item(self):
        for name in RES.names():
            self.assertIn(f"## {name}", BLOG)
        self.assertEqual(BLOG.count("\n# "), 0)      # only the first line is H1
        self.assertTrue(BLOG.startswith("# "))

    def test_editor_passes_offline_draft_and_catches_problems(self):
        rep = EditorAgent().check(BLOG, RES, S, OUTLINE.primary_keyword, draft_mode=True)
        self.assertTrue(rep.passed, rep.feedback())
        bad = EditorAgent().check("# T\n\n## Only One\n\ntext ₹9999 in 1234.", RES, S)
        codes = {i.code for i in bad.issues}
        self.assertIn("missing_items", codes)
        self.assertIn("length", codes)
        self.assertFalse(bad.passed)

    def test_editor_flags_invented_prices(self):
        text = BLOG + "\n\nEntry costs ₹7777 and the trail is 91 km long.\n"
        self.assertTrue(EditorAgent.unsupported_facts(text, RES))
        self.assertFalse(EditorAgent.unsupported_facts(BLOG, RES))

    def test_seo_is_computed_locally_and_valid(self):
        seo = SEOAgent(LLM).generate_seo(S.topic, BLOG, OUTLINE, RES, S)
        self.assertLessEqual(len(seo["seo_title"]), 60)
        self.assertLessEqual(len(seo["meta_description"]), 160)
        self.assertEqual(len(seo["secondary_keywords"]), 5)
        self.assertEqual(len(seo["long_tail_keywords"]), 5)
        self.assertEqual(seo["slug"], "best-waterfalls-of-goa")
        self.assertEqual(set(seo["image_alt_text"]), set(RES.names()))
        self.assertEqual(seo["json_ld"][0]["@type"], "Article")
        self.assertEqual(seo["json_ld"][1]["@type"], "FAQPage")
        self.assertTrue(0 <= seo["seo_score"] <= 100)

    def test_trim_to(self):
        self.assertLessEqual(len(trim_to("word " * 100, 60)), 60)


if __name__ == "__main__":
    unittest.main()
