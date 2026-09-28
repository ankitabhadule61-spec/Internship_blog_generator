"""Editor / quality agent.

Runs a battery of deterministic checks on the draft so problems are caught
*before* export - and gives the writer concrete feedback to fix them.

Checks: structure, word count, every dataset item present, invented sections,
formatting slips, AI clichés, repeated openers, readability, FAQ size,
duplicate paragraphs and - most importantly - **fact grounding** (prices,
distances and years must appear in the dataset).
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import asdict, dataclass, field

import config
from agents.retrieval import SearchResult
from utils.prompts import content_type_config
from utils.text import (
    extract_faq, flesch_reading_ease, match_item, parse_blocks, split_h2_sections,
    split_sentences, word_count,
)

CLICHES = [
    "hidden gem", "nestled", "tapestry", "bustling", "paradise on earth", "look no further", "delve",
    "breathtaking", "unforgettable experience", "whether you're a", "in conclusion", "vibrant tapestry",
    "a must-visit", "treasure trove", "testament to",
]


@dataclass
class Issue:
    severity: str            # error | warning | info
    code: str
    message: str


@dataclass
class QualityReport:
    score: int = 100
    issues: list[Issue] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def passed(self) -> bool:
        return not self.errors

    def feedback(self, include_warnings: bool = True) -> list[str]:
        keep = {"error", "warning"} if include_warnings else {"error"}
        return [i.message for i in self.issues if i.severity in keep]

    def to_dict(self) -> dict:
        return {"score": self.score, "passed": self.passed, "metrics": self.metrics,
                "issues": [asdict(i) for i in self.issues]}


class EditorAgent:
    PENALTY = {"error": 12, "warning": 4, "info": 1}

    def check(self, blog: str, result: SearchResult, settings: config.Settings,
              primary_keyword: str | None = None, draft_mode: bool = False) -> QualityReport:
        rep = QualityReport()
        add = lambda sev, code, msg: rep.issues.append(Issue(sev, code, msg))  # noqa: E731
        cfg = content_type_config(result.content_type, settings.region)
        blocks = parse_blocks(blog)
        names = result.names()
        lo, hi = settings.word_range(len(names))

        # ---- structure
        h1 = [t for k, t in blocks if k == "h1"]
        h2 = [t for k, t in blocks if k == "h2"]
        if len(h1) != 1:
            add("error", "h1", f"The article must have exactly one # title (found {len(h1)}).")
        if len(h2) < 4:
            add("warning", "h2_count", f"Only {len(h2)} sections (## headings); expected at least 4.")

        # ---- length
        wc = word_count(blog)
        rep.metrics["word_count"] = wc
        if wc < lo * 0.85 or wc > hi * 1.15:
            sev = "warning" if draft_mode else "error"
            add(sev, "length", f"Word count is {wc}; target is {lo}-{hi} words.")
        elif wc < lo or wc > hi:
            add("warning", "length", f"Word count {wc} is slightly outside the {lo}-{hi} target.")

        # ---- every item covered, no invented sections
        matched = {match_item(h, names) for h in h2} - {None}
        missing = [n for n in names if n not in matched]
        rep.metrics["items_covered"] = f"{len(names) - len(missing)}/{len(names)}"
        if missing:
            add("error", "missing_items", "These dataset items have no dedicated ## section: " + ", ".join(missing))
        generic = {"introduction"} | {h.lower() for h, _ in cfg["extra_sections"]}
        odd = [h for h in h2 if match_item(h, names) is None and h.lower() not in generic
               and not any(g.split()[0] in h.lower() for g in generic if g)]
        if odd:
            add("warning", "unknown_sections", "Sections not in the outline or dataset (possible invention): " + ", ".join(odd))

        # ---- formatting
        if re.search(r"^\s*(\*\*\s*#|#+\s*\*\*)", blog, re.MULTILINE):
            add("error", "bold_heading", "Headings must not be wrapped in ** bold **.")
        if re.search(r"^\s*>", blog, re.MULTILINE):
            add("warning", "blockquote", "Remove blockquotes (>).")

        # ---- style
        low = blog.lower()
        found = [c for c in CLICHES if low.count(c) >= 1]
        if len([c for c in found]) >= 2 or any(low.count(c) > 2 for c in found):
            add("warning", "cliches", "Replace clichéd phrases: " + ", ".join(found))
        rep.metrics["cliches"] = len(found)

        sentences = split_sentences("\n".join(t for k, t in blocks if k in {"para", "bullet"}))
        openers = Counter(" ".join(s.lower().split()[:2]) for s in sentences)
        if sentences:
            opener, n = openers.most_common(1)[0]
            if n > max(5, 0.08 * len(sentences)):
                add("warning", "repetition", f"The sentence opening '{opener}' is used {n} times; vary the openings.")
        rep.metrics["flesch"] = flesch_reading_ease(blog)
        if rep.metrics["flesch"] < 30:
            add("warning", "readability", f"Reading ease is low ({rep.metrics['flesch']}); shorten sentences.")

        # ---- FAQ
        faq = extract_faq(blog)
        rep.metrics["faq_count"] = len(faq)
        if any("frequently" in h.lower() for h in h2) and not 5 <= len(faq) <= 7:
            add("warning", "faq", f"FAQ has {len(faq)} questions (needs 5-7, each as a ### question heading).")

        # ---- duplicate paragraphs
        paras = [re.sub(r"\W+", " ", t.lower()).strip() for k, t in blocks if k == "para" and len(t.split()) > 12]
        dup = [p for p, c in Counter(paras).items() if c > 1]
        if dup:
            add("warning", "duplicate_paragraphs", f"{len(dup)} paragraph(s) are repeated verbatim.")

        # ---- primary keyword
        if primary_keyword:
            intro_words = " ".join(blog.split()[:140]).lower()
            if primary_keyword.lower() not in intro_words:
                add("info", "keyword_intro", f"Primary keyword '{primary_keyword}' not found in the opening paragraph.")

        # ---- fact grounding
        unsupported = self.unsupported_facts(blog, result)
        rep.metrics["unsupported_facts"] = len(unsupported)
        if unsupported:
            sev = "error" if len(unsupported) >= 3 and not draft_mode else "warning"
            add(sev, "grounding", "These figures do not appear in the dataset - remove or correct them: "
                + ", ".join(unsupported[:10]))

        rep.score = max(0, 100 - sum(self.PENALTY[i.severity] for i in rep.issues))
        return rep

    # ------------------------------------------------------------------
    @staticmethod
    def unsupported_facts(blog: str, result: SearchResult) -> list[str]:
        """Money amounts, distances and years in the article that the dataset never mentions."""
        context = result.to_context().replace(",", "")
        known = set(re.findall(r"\d+(?:\.\d+)?", context))
        text = blog.replace(",", "")
        claims: list[tuple[str, str]] = []
        for m in re.finditer(r"(?:₹|Rs\.?|INR)\s?(\d+(?:\.\d+)?)", text):
            claims.append((m.group(0), m.group(1)))
        for m in re.finditer(r"(\d+(?:\.\d+)?)\s?(?:km|kilomet(?:re|er)s?|metres|meters)\b", text):
            claims.append((m.group(0), m.group(1)))
        for m in re.finditer(r"\b(1[5-9]\d\d|20[0-2]\d)\b", text):
            claims.append((m.group(0), m.group(1)))
        seen, bad = set(), []
        for label, number in claims:
            if number not in known and label not in seen:
                seen.add(label)
                bad.append(label)
        return bad
