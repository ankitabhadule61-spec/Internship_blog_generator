"""Planner agent: decides title, primary keyword and the order of items.

The LLM may only *re-order* names that exist in the dataset, so the outline
can never introduce a hallucinated destination.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import config
from agents.retrieval import SearchResult
from utils.llm import LLMClient, LLMError
from utils.logger import get_logger
from utils.prompts import content_type_config, load_prompt, render

log = get_logger("planner")


@dataclass
class Outline:
    title: str
    primary_keyword: str
    angle: str
    persona: str
    order: list[str]
    sections: list[tuple[str, str]] = field(default_factory=list)   # extra h2 sections
    mode: str = "llm"

    def to_markdown(self, item_structure: list[list[str]]) -> str:
        lines = [f"# {self.title}", "", "## Introduction", f"- Hook: {self.angle}", f"- Reader: {self.persona}", ""]
        for name in self.order:
            lines.append(f"## {name}")
            lines.extend(f"### {h}" for h, _ in item_structure)
            lines.append("")
        for heading, desc in self.sections:
            lines += [f"## {heading}", f"- {desc}", ""]
        return "\n".join(lines).strip() + "\n"


def _title_case(text: str) -> str:
    small = {"of", "in", "to", "the", "and", "for", "a", "an", "on"}
    words = text.strip().split()
    return " ".join(w if (i and w.lower() in small) else w[:1].upper() + w[1:] for i, w in enumerate(words))


class PlannerAgent:
    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm or LLMClient()

    def create_outline(self, topic: str, result: SearchResult, settings: config.Settings | None = None) -> Outline:
        settings = settings or config.Settings(topic=topic)
        cfg = content_type_config(result.content_type, settings.region)
        names = result.names()
        if not names:
            raise ValueError("Nothing retrieved for this topic - cannot plan an outline.")

        plan = None
        if not self.llm.offline:
            try:
                prompt = render(
                    load_prompt("planner_prompt.txt"),
                    region=settings.region, country=settings.country, topic=topic,
                    content_label=cfg["label"], names="\n".join(f"- {n}" for n in names),
                )
                plan = self.llm.generate_json(prompt)
            except (LLMError, ValueError) as exc:
                if settings.strict:
                    raise
                log.warning("Planner LLM failed (%s) -> using data-driven plan", exc)

        return self._build(topic, names, plan, cfg)

    # ------------------------------------------------------------------
    def _build(self, topic: str, names: list[str], plan: dict | None, cfg: dict) -> Outline:
        mode = "llm" if plan else "offline"
        plan = plan or {}
        order = [n for n in plan.get("order", []) if n in names]
        order += [n for n in names if n not in order]          # never lose an item
        title = re.sub(r"\s+", " ", str(plan.get("title") or _title_case(topic))).strip()
        primary = str(plan.get("primary_keyword") or _title_case(re.sub(r"^top\s*\d+\s*", "", topic, flags=re.I))).strip()
        return Outline(
            title=title,
            primary_keyword=primary,
            angle=str(plan.get("angle") or f"Show readers the most rewarding {cfg['label']} and how to enjoy them well."),
            persona=str(plan.get("reader_persona") or "Curious travellers planning a trip"),
            order=order,
            sections=[tuple(s) for s in cfg["extra_sections"]],
            mode=mode,
        )
