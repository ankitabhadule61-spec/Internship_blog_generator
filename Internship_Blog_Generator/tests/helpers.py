"""Shared test helpers: a fake Gemini client so the AI code path is testable without network access."""
import json
import re

from utils.llm import LLMClient, extract_json


class FakeLLM(LLMClient):
    """Pretends to be Gemini. Behaviour is chosen from the prompt text."""

    def __init__(self, blog_text: str | None = None, fail_first_writes: int = 0):
        super().__init__(offline=False)
        self.api.blog_keys = ["fake-key"]
        self.blog_text = blog_text
        self.prompts: list[str] = []
        self.fail_first_writes = fail_first_writes

    @property
    def offline(self) -> bool:
        return False

    def generate(self, prompt, temperature=0.7, json_mode=False):
        self.prompts.append(prompt)
        self.calls += 1
        if "planning a blog post" in prompt:                       # planner
            names = re.findall(r"^- (.+)$", prompt.split("CANDIDATE ITEMS")[1].split("Decide")[0], re.M)
            return json.dumps({"title": "Goa's Best Waterfalls: A Monsoon Guide", "primary_keyword": "Goa waterfalls",
                               "angle": "Chase the monsoon", "order": list(reversed(names)),
                               "reader_persona": "Monsoon travellers"})
        if "SEO specialist" in prompt:                             # seo
            return json.dumps({"seo_title": "Goa Waterfalls: 6 Best Falls to Visit", "primary_keyword": "Goa waterfalls",
                               "secondary_keywords": ["a", "b", "c", "d", "e"], "long_tail_keywords": ["1", "2", "3", "4", "5"],
                               "meta_description": "x" * 155, "excerpt": "Short.", "tags": ["t1", "t2", "t3", "t4", "t5"]})
        if "meticulous travel-magazine editor" in prompt:          # revise
            return self.blog_text or "# Fixed\n"
        return self.blog_text or "# Draft\n"                       # writer
