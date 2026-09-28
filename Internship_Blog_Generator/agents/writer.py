"""Writer agent: turns the outline + verified dataset into the article.

* Online: Gemini writes the article from a compact, grounded context.
* Offline / fallback: a deterministic, data-driven draft is assembled straight
  from the dataset (so the whole pipeline still works without an API key and
  can be unit-tested).
* `revise()` re-writes the article using the editor's feedback.
"""
from __future__ import annotations

import re

import config
from agents.planner import Outline
from agents.retrieval import SearchResult, clean_value, is_blank
from utils.llm import LLMClient, LLMError
from utils.logger import get_logger
from utils.prompts import content_type_config, load_prompt, render
from utils.text import normalize_markdown

log = get_logger("writer")

# offline section templates: heading -> (template, required columns)
OFFLINE_SECTIONS: dict[str, dict[str, tuple[str, list[str]]]] = {
    "places": {
        "Why Visit": ("Recommended for {recommended_for}. {activities}", ["recommended_for"]),
        "What Makes It Special": ("{historical_info}", ["historical_info"]),
        "Best Time to Visit": ("The best time to visit is {best_time_to_visit}.", ["best_time_to_visit"]),
        "Nearby Attractions": ("Nearby you can also explore {nearest_place_to_visit}.", ["nearest_place_to_visit"]),
        "Travel Tip": ("{dos}", ["dos"]),
    },
    "activities": {
        "What You Will Do": ("{highlights}", ["highlights"]),
        "Who It Is For": ("Suitable for {suitable_for}; difficulty level: {difficulty_level}.", ["suitable_for", "difficulty_level"]),
        "Duration and Cost": ("Plan for {duration}; the average cost is {average_cost} {currency}.", ["duration", "average_cost"]),
        "Best Season": ("The best season is {best_season}.", ["best_season"]),
        "Travel Tip": ("Carry {what_to_carry}.", ["what_to_carry"]),
    },
    "events": {
        "What Is It": ("A {category} event held {recurrence}.", ["category", "recurrence"]),
        "When and Where": ("Held in {start_date} at {venue}, {village}.", ["start_date", "venue"]),
        "What to Expect": ("Expected crowd: {expected_crowd}. Tickets required: {ticket_required}.", ["expected_crowd"]),
        "Nearby Attractions": ("Nearby: {nearby_attractions}.", ["nearby_attractions"]),
        "Travel Tip": ("{safety_tips}", ["safety_tips"]),
    },
    "food": {
        "The Experience": ("{Cuisine types} cuisine at a {Restaurant type}.", ["Cuisine types"]),
        "Must-Try Dishes": ("Popular dishes include {Popular dishes}.", ["Popular dishes"]),
        "Price and Timings": ("Price for two: {Price range (for two)} {Currency}. Opens {Opening time}.", ["Price range (for two)"]),
        "Location": ("Located in {Village}, near {Nearest beach}.", ["Village"]),
        "Travel Tip": ("Food type: {Veg / Non Veg / Pure Veg}.", ["Veg / Non Veg / Pure Veg"]),
    },
    "stay": {
        "Overview": ("{category} with a {star_rating}-star rating.", ["category"]),
        "Best For": ("Best for {best_for}.", ["best_for"]),
        "Amenities": ("Amenities include {amenities}.", ["amenities"]),
        "Price and Location": ("From {price_per_night} {currency} per night in {village}, near {nearest_beach}.", ["price_per_night"]),
        "Travel Tip": ("Family friendly: {family_friendly}.", ["family_friendly"]),
    },
}


def _sentences(text: str, n: int) -> str:
    parts = re.split(r"(?<=[.!?])\s+", clean_value(text))
    return " ".join(parts[:n]).strip()


class _Safe(dict):
    def __missing__(self, key):  # unknown / blank columns become empty strings
        return ""


class WriterAgent:
    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm or LLMClient()
        self.last_mode = "offline"

    # ------------------------------------------------------------------ main
    def write_blog(self, topic: str, outline: Outline, result: SearchResult,
                   settings: config.Settings | None = None) -> str:
        settings = settings or config.Settings(topic=topic)
        cfg = content_type_config(result.content_type, settings.region)
        lo, hi = settings.word_range(len(result.records))

        if not self.llm.offline:
            try:
                prompt = render(
                    load_prompt("writer_prompt.txt"),
                    region=settings.region, country=settings.country, topic=topic,
                    primary_keyword=outline.primary_keyword, angle=outline.angle, persona=outline.persona,
                    min_words=lo, max_words=hi,
                    outline=outline.to_markdown(cfg["item_structure"]),
                    dataset=result.to_context(),
                    label_title=cfg["label"].title(), item_word=cfg["item_word"],
                    words_per_item=cfg["words_per_item"],
                    item_structure="\n".join(f"### {h}\n({d})" for h, d in cfg["item_structure"]),
                    extra_sections="\n".join(f"## {h}\n({d})" for h, d in cfg["extra_sections"]),
                )
                text = self.llm.generate(prompt, temperature=0.75)
                self.last_mode = "llm"
                return normalize_markdown(text)
            except LLMError as exc:
                if settings.strict:
                    raise
                log.warning("Writer LLM failed (%s) -> building an offline draft from the dataset", exc)

        self.last_mode = "offline"
        return normalize_markdown(self._offline_blog(topic, outline, result, cfg, settings))

    def revise(self, blog: str, issues: list[str], result: SearchResult, settings: config.Settings) -> str:
        if self.llm.offline:
            return blog
        lo, hi = settings.word_range(len(result.records))
        prompt = render(
            load_prompt("revise_prompt.txt"),
            issues="\n".join(f"- {i}" for i in issues), min_words=lo, max_words=hi,
            dataset=result.to_context(), blog=blog,
        )
        try:
            return normalize_markdown(self.llm.generate(prompt, temperature=0.4))
        except LLMError as exc:
            log.warning("Revision failed (%s) - keeping previous draft", exc)
            return blog

    # -------------------------------------------------------------- offline draft
    def _offline_blog(self, topic: str, outline: Outline, result: SearchResult, cfg: dict,
                      settings: config.Settings) -> str:
        spec = result.spec
        templates = OFFLINE_SECTIONS[result.content_type]
        rows = {n: result.row_for(n) for n in outline.order}
        out = [f"# {outline.title}", ""]

        names = ", ".join(outline.order[:-1]) + (f" and {outline.order[-1]}" if len(outline.order) > 1 else outline.order[0])
        out += [
            "## Introduction", "",
            f"Planning a trip to {settings.region}? This guide to {outline.primary_keyword.lower()} brings together "
            f"{len(outline.order)} carefully verified {cfg['label']}: {names}.",
            "",
            f"Every detail below - from timings to prices - is taken from our {settings.region} travel database, so "
            f"you can plan with confidence and spend less time second-guessing.",
            "",
        ]

        for name in outline.order:
            row = rows[name]
            out += [f"## {name}", ""]
            if row is None:
                continue
            desc = row.get("description", "")
            if not is_blank(desc):
                out += [_sentences(desc, 3), ""]
            for heading, _ in cfg["item_structure"]:
                tpl, required = templates.get(heading, ("", []))
                if not tpl or any(is_blank(row.get(c, "")) for c in required):
                    continue
                values = _Safe({c: _sentences(row[c], 2) if len(str(row[c])) > 220 else clean_value(row[c])
                                for c in row.index if not is_blank(row[c])})
                recurrence = {"annual": "every year", "monthly": "every month", "weekly": "every week", "daily": "every day"}
                if "recurrence" in values:
                    values["recurrence"] = recurrence.get(values["recurrence"].lower(), values["recurrence"].lower())
                if values.get("venue") and values.get("venue") == values.get("village"):
                    values["village"] = ""
                text = re.sub(r"\s+", " ", tpl.format_map(values)).replace(" .", ".").replace("..", ".").strip()
                text = re.sub(r"\(\s*\)|\bnear\s*\.$|,\s*\.", ".", text)
                if text:
                    out += [f"### {heading}", "", text, ""]

        # ---- extra sections built from data
        for heading, _ in cfg["extra_sections"]:
            low = heading.lower()
            out += [f"## {heading}", ""]
            if "reach" in low or "getting around" in low:
                routes = [(n, result.route_for(n)) for n in outline.order if result.route_for(n)]
                if routes:
                    out += [f"- **{n}:** {r}" for n, r in routes[:8]]
                else:
                    out += ["Most destinations are easiest to reach by taxi or self-drive from the nearest town; check local timings before you set out."]
            elif "frequently" in low:
                out += self._offline_faq(outline, result, settings)
            elif "conclusion" in low:
                out += [f"From {outline.order[0]} to {outline.order[-1]}, {settings.region} rewards curious travellers. "
                        f"Pick a few favourites, travel respectfully and enjoy the trip."]
            elif "budget" in low:
                fees = []
                for n in outline.order:
                    for label, value in result.facts_for(n):
                        if label in {"Entry fee", "Average cost", "Ticket price", "Price for two", "Price per night"}:
                            fees.append(f"- **{n}:** {value}")
                out += fees[:8] or ["Book directly, travel in a group and avoid peak weekends to keep costs down."]
            elif "best time" in low:
                times = [f"- **{n}:** {v}" for n in outline.order for l, v in result.facts_for(n)
                         if l in {"Best time", "Best season", "When"}]
                out += times[:8] or [f"{settings.region} is at its most comfortable from November to February."]
            else:
                out += ["Respect local customs, carry your rubbish out with you, avoid single-use plastic and stay on marked paths."]
            out.append("")
        return "\n".join(out)

    def _offline_faq(self, outline: Outline, result: SearchResult, settings: config.Settings) -> list[str]:
        first = outline.order[0]
        faq = [(f"What is the most popular option in this guide?", f"{first} leads our list based on visitor popularity in the dataset.")]
        for n in outline.order[:5]:
            facts = dict(result.facts_for(n))
            for label in ("Best time", "Best season", "When", "Timings", "Entry fee", "Average cost"):
                if label in facts:
                    q = {"Best time": f"When is the best time to visit {n}?", "Best season": f"What is the best season for {n}?",
                         "When": f"When does {n} take place?", "Timings": f"What are the timings for {n}?",
                         "Entry fee": f"Is there an entry fee for {n}?", "Average cost": f"How much does {n} cost?"}[label]
                    faq.append((q, facts[label] + ("." if not facts[label].endswith(".") else "")))
                    break
        out = []
        for q, a in faq[:6]:
            out += [f"### {q}", "", a, ""]
        return out
