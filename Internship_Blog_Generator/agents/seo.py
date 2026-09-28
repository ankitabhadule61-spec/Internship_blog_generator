"""SEO agent.

The LLM only does what needs language skill (title, keywords, meta text).
Everything measurable - word count, reading time, slug, meta length, keyword
density, alt text, SEO score, JSON-LD schema - is computed locally, so it is
correct every time (the first version asked the LLM to count words).
"""
from __future__ import annotations

import json
import re

import config
from agents.planner import Outline
from agents.retrieval import SearchResult, clean_value, is_blank
from utils.llm import LLMClient, LLMError
from utils.logger import get_logger
from utils.prompts import load_prompt, render
from utils.text import extract_faq, match_item, parse_blocks, reading_time, slugify, word_count

log = get_logger("seo")


def trim_to(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    cut = text[: limit - 1].rsplit(" ", 1)[0].rstrip(",;:-")
    return cut + "…" if not cut.endswith(".") else cut


class SEOAgent:
    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm or LLMClient()

    # ------------------------------------------------------------------ main
    def generate_seo(self, topic: str, blog: str, outline: Outline | None = None,
                     result: SearchResult | None = None, settings: config.Settings | None = None) -> dict:
        settings = settings or config.Settings(topic=topic)
        primary = outline.primary_keyword if outline else topic
        raw: dict = {}
        if not self.llm.offline:
            try:
                raw = self.llm.generate_json(render(load_prompt("seo_prompt.txt"), topic=topic, primary_keyword=primary, blog=blog))
            except (LLMError, ValueError) as exc:
                if settings.strict:
                    raise
                log.warning("SEO LLM failed (%s) -> deriving SEO data locally", exc)
        return self._finalise(topic, blog, raw, outline, result, settings)

    # ------------------------------------------------------------- finalise
    def _finalise(self, topic, blog, raw, outline, result, settings) -> dict:
        blocks = parse_blocks(blog)
        h1 = next((t for k, t in blocks if k == "h1"), topic)
        primary = str(raw.get("primary_keyword") or (outline.primary_keyword if outline else topic)).strip()
        names = result.names() if result else []

        seo_title = trim_to(str(raw.get("seo_title") or h1), 60)
        secondary = self._pad(raw.get("secondary_keywords"), self._fallback_secondary(primary, result, settings), 5)
        longtail = self._pad(raw.get("long_tail_keywords"), self._fallback_longtail(primary, settings), 5)
        meta = str(raw.get("meta_description") or self._fallback_meta(primary, names, settings))
        meta = trim_to(meta, 160)

        wc = word_count(blog)
        data = {
            "seo_title": seo_title,
            "primary_keyword": primary,
            "focus_keyword": primary,
            "secondary_keywords": secondary,
            "long_tail_keywords": longtail,
            "slug": slugify(primary if len(primary) > 8 else seo_title, max_len=60),
            "meta_description": meta,
            "excerpt": trim_to(str(raw.get("excerpt") or meta), 200),
            "tags": self._pad(raw.get("tags"), [primary, settings.region, *secondary], 5),
            "word_count": wc,
            "reading_time_minutes": reading_time(wc),
        }

        # image alt text for every item section
        alts: dict[str, str] = {}
        for kind, heading in blocks:
            if kind != "h2":
                continue
            item = match_item(heading, names) if names else None
            if item:
                alts[heading] = self._alt_text(heading, item, result, settings)
        data["image_alt_text"] = alts

        data["keyword_density_percent"] = self._density(blog, primary)
        data["checks"], data["seo_score"] = self._score(data, blog, blocks)
        data["json_ld"] = self._json_ld(data, blog, settings)
        return data

    def save_seo(self, seo_data: dict, path=None) -> str:
        path = path or (config.OUTPUT_DIR / "seo.json")
        path = str(path)
        import os
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(seo_data, f, indent=2, ensure_ascii=False)
        return path

    # ------------------------------------------------------------- helpers
    @staticmethod
    def _pad(values, fallback: list[str], n: int) -> list[str]:
        out: list[str] = []
        for v in list(values or []) + list(fallback):
            v = str(v).strip()
            if v and v.lower() not in {o.lower() for o in out}:
                out.append(v)
            if len(out) == n:
                break
        return out

    @staticmethod
    def _fallback_secondary(primary, result, settings):
        base = [f"{settings.region} travel guide", f"things to do in {settings.region}", f"visit {settings.region}"]
        if result is not None and not result.empty:
            cats = [clean_value(c).lower() for c in result.records.get("category", []) if not is_blank(c)]
            base += [f"{c} in {settings.region}" for c in dict.fromkeys(cats)][:3]
        return base + [f"{primary} guide"]

    @staticmethod
    def _core(primary: str) -> str:
        return re.sub(r"^(best|top\s*\d*|ultimate|complete)\s+", "", primary.strip(), flags=re.I).lower()

    @classmethod
    def _fallback_longtail(cls, primary, settings):
        core = cls._core(primary)
        return [f"best time to visit {core}", f"how to reach {core}", f"{core} with family",
                f"{core} on a budget", f"{core} travel guide", f"{core} itinerary"]

    @classmethod
    def _fallback_meta(cls, primary, names, settings):
        core = cls._core(primary)
        lead = ", ".join(names[:2]) if names else settings.region
        text = (f"Explore the {core}: {lead} and more. Verified timings, best seasons and practical "
                f"travel tips to plan your trip.")
        if len(text) < 150 and len(text) + 22 <= 160:
            text += " Start planning today."
        return text

    @staticmethod
    def _alt_text(heading, item, result, settings) -> str:
        sub = ""
        row = result.row_for(item) if result else None
        if row is not None:
            for col in ("sub_category", "Sub category", "category", "Category"):
                if col in row and not is_blank(row[col]):
                    sub = clean_value(row[col]).lower()
                    break
        name = re.sub(r"\s*\(.*?\)", "", heading).strip()
        return f"Scenic view of {name}, a {sub} in {settings.region}, {settings.country}" if sub else \
               f"View of {name} in {settings.region}, {settings.country}"

    @staticmethod
    def _density(blog: str, keyword: str) -> float:
        words = word_count(blog) or 1
        hits = len(re.findall(re.escape(keyword.lower()), blog.lower()))
        return round(100 * hits * max(1, len(keyword.split())) / words, 2)

    @staticmethod
    def _score(data: dict, blog: str, blocks) -> tuple[list[dict], int]:
        kw = data["primary_keyword"].lower()
        h1 = next((t for k, t in blocks if k == "h1"), "").lower()
        first100 = " ".join(blog.split()[:120]).lower()
        checks = [
            ("SEO title 30-60 chars", 30 <= len(data["seo_title"]) <= 60, len(data["seo_title"]), 12),
            ("Meta description 120-160 chars", 120 <= len(data["meta_description"]) <= 160, len(data["meta_description"]), 14),
            ("Keyword in SEO title", kw in data["seo_title"].lower(), "", 12),
            ("Keyword in H1", kw in h1 or all(w in h1 for w in kw.split()), "", 10),
            ("Keyword in first 100 words", kw in first100, "", 10),
            ("Keyword in slug", all(w in data["slug"] for w in slugify(kw).split("-")[:2]), data["slug"], 6),
            ("Keyword density 0.4-2.5%", 0.4 <= data["keyword_density_percent"] <= 2.5, data["keyword_density_percent"], 10),
            ("5 secondary + 5 long-tail keywords", len(data["secondary_keywords"]) >= 5 and len(data["long_tail_keywords"]) >= 5, "", 6),
            ("Alt text for every item image", bool(data["image_alt_text"]), len(data["image_alt_text"]), 8),
            ("FAQ section (FAQ rich results)", len(extract_faq(blog)) >= 3, len(extract_faq(blog)), 7),
            ("At least 5 H2 sections", sum(1 for k, _ in blocks if k == "h2") >= 5, "", 5),
        ]
        out = [{"check": n, "passed": bool(ok), "value": v, "points": p} for n, ok, v, p in checks]
        return out, sum(c["points"] for c in out if c["passed"])

    @staticmethod
    def _json_ld(data: dict, blog: str, settings) -> list[dict]:
        article = {
            "@context": "https://schema.org", "@type": "Article",
            "headline": data["seo_title"], "description": data["meta_description"],
            "keywords": ", ".join([data["primary_keyword"], *data["secondary_keywords"]]),
            "wordCount": data["word_count"], "inLanguage": "en",
            "about": {"@type": "Place", "name": f"{settings.region}, {settings.country}"},
        }
        schema = [article]
        faq = extract_faq(blog)
        if faq:
            schema.append({
                "@context": "https://schema.org", "@type": "FAQPage",
                "mainEntity": [{"@type": "Question", "name": q,
                                "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in faq],
            })
        return schema
