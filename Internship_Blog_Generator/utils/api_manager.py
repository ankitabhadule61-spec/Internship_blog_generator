"""API key management (Gemini key rotation + optional other providers)."""
from __future__ import annotations

import os

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover
    pass


class APIManager:
    """Collects every Gemini key found in the environment.

    Supported variables: GEMINI_API_KEY, GEMINI_API_KEY_1 ... GEMINI_API_KEY_9
    (the original GEMINI_API_KEY_1 / _2 setup keeps working).
    """

    def __init__(self) -> None:
        keys = [os.getenv("GEMINI_API_KEY")]
        keys += [os.getenv(f"GEMINI_API_KEY_{i}") for i in range(1, 10)]
        seen, ordered = set(), []
        for key in keys:
            if key and key.strip() and key not in seen:
                seen.add(key)
                ordered.append(key.strip())
        self.blog_keys = ordered
        self.hf_key = os.getenv("HF_API_KEY")
        self.stability_key = os.getenv("STABILITY_API_KEY")

    def get_blog_keys(self) -> list[str]:
        return self.blog_keys

    def get_hf_key(self):
        return self.hf_key

    def get_stability_key(self):
        return self.stability_key

    @property
    def has_keys(self) -> bool:
        return bool(self.blog_keys)
