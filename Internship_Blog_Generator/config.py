"""Central configuration for the Internship Blog Generator.

Everything that used to be hard-coded across agents (model name, folders,
word targets, image size ...) lives here and can be overridden with
environment variables (or a .env file).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:  # python-dotenv is optional at import time
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover
    pass

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
PROMPT_DIR = BASE_DIR / "prompts"
OUTPUT_DIR = Path(os.getenv("BLOG_OUTPUT_DIR", str(BASE_DIR / "output")))

REGION = os.getenv("BLOG_REGION", "Goa")
COUNTRY = os.getenv("BLOG_COUNTRY", "India")
MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")


@dataclass
class Settings:
    """Runtime options for one pipeline run."""

    topic: str = "Best Waterfalls of Goa"
    content_type: str | None = None      # places|activities|events|food|stay (auto if None)
    subject: str | None = None           # waterfall|beach|fort ... (auto if None)
    limit: int = 8                       # number of items to feature
    offline: bool = False                # never call Gemini / Pollinations
    strict: bool = False                 # fail instead of falling back to offline drafts
    images: bool = True
    formats: tuple = ("docx", "html", "md")
    min_words: int | None = None         # auto-scaled from item count if None
    max_words: int | None = None
    max_revisions: int = 2
    resume: bool = False                 # reuse artefacts from a previous run
    output_dir: Path = OUTPUT_DIR
    region: str = REGION
    country: str = COUNTRY
    image_width: int = 1280
    image_height: int = 720
    image_workers: int = 2

    def word_range(self, n_items: int) -> tuple[int, int]:
        lo = self.min_words or max(900, n_items * 150 + 600)
        hi = self.max_words or lo + 700
        return lo, hi
