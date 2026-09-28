"""Shared text / markdown helpers used by every agent.

Having ONE slugify() fixes an old bug where the image agent and the exporter
built filenames differently and then relied on fuzzy matching to find them.
"""
from __future__ import annotations

import re
import unicodedata

INLINE_RE = re.compile(r"(\*\*[^*]+\*\*|\*[^*\s][^*]*\*|`[^`]+`)")


def slugify(text: str, sep: str = "-", max_len: int = 80) -> str:
    text = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode()
    text = re.sub(r"\(.*?\)", " ", text)
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-zA-Z0-9]+", sep, text.lower()).strip(sep)
    return (text[:max_len].strip(sep)) or "untitled"


def clean_heading(text: str) -> str:
    return re.sub(r"[*_`#]", "", text).strip()


def strip_markdown(text: str) -> str:
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"[*_`>]", "", text)
    text = re.sub(r"\[(.*?)\]\(.*?\)", r"\1", text)
    return text


def word_count(markdown: str) -> int:
    return len(re.findall(r"\b[\w'’-]+\b", strip_markdown(markdown)))


def reading_time(words: int, wpm: int = 200) -> int:
    return max(1, round(words / wpm))


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", strip_markdown(text).strip())
    return [p.strip() for p in parts if len(p.strip().split()) >= 3]


def _syllables(word: str) -> int:
    word = word.lower()
    groups = re.findall(r"[aeiouy]+", word)
    count = len(groups)
    if word.endswith("e") and count > 1:
        count -= 1
    return max(1, count)


def flesch_reading_ease(text: str) -> float:
    sentences = split_sentences(text) or [text]
    words = re.findall(r"[A-Za-z']+", strip_markdown(text))
    if not words:
        return 0.0
    syll = sum(_syllables(w) for w in words)
    score = 206.835 - 1.015 * (len(words) / len(sentences)) - 84.6 * (syll / len(words))
    return round(max(0.0, min(100.0, score)), 1)


def parse_blocks(markdown: str) -> list[tuple[str, str]]:
    """Turn markdown into [(kind, text)] with kind in h1,h2,h3,bullet,number,para."""
    blocks: list[tuple[str, str]] = []
    para: list[str] = []

    def flush():
        if para:
            blocks.append(("para", " ".join(para).strip()))
            para.clear()

    for raw in markdown.splitlines():
        line = raw.strip()
        if not line:
            flush()
            continue
        if line in {"---", "***", "___"}:
            flush()
            continue
        m = re.match(r"^(#{1,3})\s+(.*)$", line)
        if m:
            flush()
            blocks.append((f"h{len(m.group(1))}", clean_heading(m.group(2))))
            continue
        if re.match(r"^[-*•]\s+", line):
            flush()
            blocks.append(("bullet", re.sub(r"^[-*•]\s+", "", line)))
            continue
        if re.match(r"^\d+[.)]\s+", line):
            flush()
            blocks.append(("number", re.sub(r"^\d+[.)]\s+", "", line)))
            continue
        para.append(line.lstrip("> "))
    flush()
    return blocks


def parse_inline(text: str) -> list[tuple[str, bool, bool]]:
    """Split inline markdown into runs: (text, bold, italic)."""
    runs: list[tuple[str, bool, bool]] = []
    for piece in INLINE_RE.split(text):
        if not piece:
            continue
        if piece.startswith("**") and piece.endswith("**") and len(piece) > 4:
            runs.append((piece[2:-2], True, False))
        elif piece.startswith("*") and piece.endswith("*") and len(piece) > 2:
            runs.append((piece[1:-1], False, True))
        elif piece.startswith("`") and piece.endswith("`"):
            runs.append((piece[1:-1], False, False))
        else:
            runs.append((piece, False, False))
    return runs


def split_h2_sections(markdown: str) -> dict[str, str]:
    """Return {h2 heading: body text} preserving order."""
    sections: dict[str, str] = {}
    current, buf = None, []
    for line in markdown.splitlines():
        m = re.match(r"^##\s+(.*)$", line.strip())
        if m and not line.strip().startswith("###"):
            if current is not None:
                sections[current] = "\n".join(buf).strip()
            current, buf = clean_heading(m.group(1)), []
        elif current is not None:
            buf.append(line)
    if current is not None:
        sections[current] = "\n".join(buf).strip()
    return sections


def extract_faq(markdown: str) -> list[tuple[str, str]]:
    """Parse the FAQ section into [(question, answer)]."""
    faq_body = ""
    for heading, body in split_h2_sections(markdown).items():
        if "frequently asked" in heading.lower() or heading.lower().strip() == "faq":
            faq_body = body
            break
    if not faq_body:
        return []
    pairs: list[tuple[str, str]] = []
    question, answer = None, []
    for line in faq_body.splitlines():
        s = line.strip()
        if not s:
            continue
        q = None
        if s.startswith("###"):
            q = clean_heading(s)
        elif re.match(r"^\*\*.+\?\*\*$", s):
            q = s.strip("*")
        elif re.match(r"^(\d+[.)]\s*)?\*\*.+\?\*\*", s):
            q = re.sub(r"^\d+[.)]\s*", "", s).split("**")[1]
        if q:
            if question:
                pairs.append((question, " ".join(answer).strip()))
            question, answer = q.strip(), []
        elif question:
            answer.append(strip_markdown(s))
    if question:
        pairs.append((question, " ".join(answer).strip()))
    return [(q, a) for q, a in pairs if a]


def normalize_markdown(md: str) -> str:
    """Repair the small formatting slips LLMs make so downstream parsing is stable."""
    md = md.strip()
    md = re.sub(r"^```(?:markdown|md)?\s*\n", "", md)
    md = re.sub(r"\n```\s*$", "", md)
    fixed = []
    for line in md.splitlines():
        s = line.rstrip()
        m = re.match(r"^\s*\*{0,2}\s*(#{1,4})\s*\*{0,2}\s*(.+?)\s*\*{0,2}\s*$", s)
        if m and (s.lstrip().startswith("#") or s.lstrip().startswith("*")):
            s = f"{m.group(1)} {m.group(2).strip('* ')}"
        s = re.sub(r"^\s*>\s?", "", s)
        fixed.append(s)
    md = "\n".join(fixed)
    md = re.sub(r"\n{3,}", "\n\n", md)
    return md.strip() + "\n"


def match_item(heading: str, names: list[str]) -> str | None:
    """Map a blog heading back to the dataset item it talks about (or None)."""
    import difflib

    hs = slugify(heading)
    htokens = set(hs.split("-"))
    best, best_ratio = None, 0.0
    for name in names:
        ns = slugify(name)
        if hs == ns:
            return name
        ntokens = set(ns.split("-"))
        ratio = difflib.SequenceMatcher(None, hs, ns).ratio()
        if ntokens and (ntokens <= htokens or htokens <= ntokens) and len(ntokens & htokens) >= 2:
            ratio = max(ratio, 0.9)
        if ratio > best_ratio:
            best, best_ratio = name, ratio
    return best if best_ratio >= 0.8 else None
