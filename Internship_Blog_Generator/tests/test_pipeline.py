import json
import tempfile
import unittest
from pathlib import Path

from docx import Document

import config
from agents.retrieval import RetrievalAgent
from pipeline import BlogPipeline, PipelineError
from tests.helpers import FakeLLM
from utils.llm import LLMClient

RETRIEVAL = RetrievalAgent()


class PipelineOfflineTest(unittest.TestCase):
    def test_full_offline_run_creates_every_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = config.Settings(topic="Best Waterfalls of Goa", offline=True, limit=5, output_dir=Path(tmp))
            res = BlogPipeline(s, llm=LLMClient(offline=True), retrieval=RETRIEVAL).run()
            for name in ("blog.md", "outline.md", "seo.json", "records.json", "images.json", "quality_report.json",
                         "final_blog.docx", "blog.html", "blog_publish.md", "run.json"):
                self.assertTrue((res.run_dir / name).exists(), name)
            self.assertEqual(len(list((res.run_dir / "images").glob("*.png"))), 6)   # hero + 5
            doc = Document(res.run_dir / "final_blog.docx")
            self.assertEqual(len(doc.inline_shapes), 6)
            self.assertGreaterEqual(len(doc.tables), 5)                              # quick-facts tables
            html = (res.run_dir / "blog.html").read_text(encoding="utf-8")
            self.assertIn("application/ld+json", html)
            self.assertEqual(html.count("<img"), 6)
            run = json.loads((res.run_dir / "run.json").read_text())
            self.assertEqual(run["mode"], "offline")

    def test_resume_reuses_existing_blog(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = config.Settings(topic="Historic Forts of Goa", offline=True, limit=4, images=False, output_dir=Path(tmp))
            first = BlogPipeline(s, llm=LLMClient(offline=True), retrieval=RETRIEVAL).run()
            blog = first.run_dir / "blog.md"
            blog.write_text(blog.read_text() + "\nCUSTOM EDIT\n", encoding="utf-8")
            s.resume = True
            second = BlogPipeline(s, llm=LLMClient(offline=True), retrieval=RETRIEVAL).run()
            self.assertEqual(second.mode, "resumed")
            self.assertIn("CUSTOM EDIT", blog.read_text())

    def test_missing_data_raises_helpful_error(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as empty_data:
            s = config.Settings(topic="Best Waterfalls of Goa", offline=True, output_dir=Path(tmp))
            pipe = BlogPipeline(s, llm=LLMClient(offline=True), retrieval=RetrievalAgent(data_folder=empty_data))
            with self.assertRaises(PipelineError) as ctx:
                pipe.run()
            self.assertIn("No data matched", str(ctx.exception))


class PipelineWithFakeGeminiTest(unittest.TestCase):
    """Exercises the AI code path (planner -> writer -> editor -> revise -> SEO) without network access."""

    def test_ai_path_with_revision_loop(self):
        s0 = config.Settings(topic="Best Waterfalls of Goa", limit=4)
        res = RETRIEVAL.search(s0.topic, limit=4)
        # a deliberately bad first draft (missing items) -> editor errors -> revision returns a good one
        good = "\n".join(["# Goa Waterfalls Guide", "", "## Introduction", "", "Goa waterfalls are lovely. " * 20, ""]
                         + [f"## {n}\n\n{'Water falls softly. ' * 40}\n" for n in res.names()]
                         + ["## Frequently Asked Questions"]
                         + [f"### Question {i}?\n\nAnswer {i}.\n" for i in range(5)] + ["## Conclusion", "", "Bye."])
        llm = FakeLLM(blog_text=good)
        with tempfile.TemporaryDirectory() as tmp:
            s = config.Settings(topic="Best Waterfalls of Goa", limit=4, images=False, output_dir=Path(tmp),
                                min_words=300, max_words=900)
            pipe = BlogPipeline(s, llm=llm, retrieval=RETRIEVAL)
            # first call returns a bad draft, later calls (revise) return the good one
            original = llm.generate
            state = {"n": 0}

            def gen(prompt, temperature=0.7, json_mode=False):
                if "Write a polished" in prompt or "award-winning travel writer" in prompt:
                    state["n"] += 1
                    llm.prompts.append(prompt)
                    return "# Bad\n\n## Only\n\nshort"
                return original(prompt, temperature, json_mode)

            llm.generate = gen
            out = pipe.run()
            run = json.loads((out.run_dir / "run.json").read_text())
            self.assertEqual(run["mode"], "llm")
            self.assertGreaterEqual(run["revisions"], 1)
            self.assertIn("Goa Waterfalls Guide", (out.run_dir / "blog.md").read_text())
            seo = json.loads((out.run_dir / "seo.json").read_text())
            self.assertEqual(seo["primary_keyword"], "Goa waterfalls")
            self.assertEqual(len(seo["meta_description"]), 155)
            outline = (out.run_dir / "outline.md").read_text()
            self.assertIn("Goa's Best Waterfalls", outline)
            # planner may reorder but never invent names
            self.assertEqual(sorted(json.loads((out.run_dir / "records.json").read_text())[i]["name"] for i in range(4)),
                             sorted(res.names()))


if __name__ == "__main__":
    unittest.main()
