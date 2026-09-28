"""One resilient Gemini client shared by every agent.

Replaces three copy-pasted retry blocks (planner / writer / seo) with:
  * key rotation on quota errors (429)
  * exponential back-off on 503 / timeouts
  * helpful message for a wrong model name (404)
  * robust JSON extraction + one automatic repair attempt
  * clean offline mode (no keys, --offline, or LLM_MODE=offline)
"""
from __future__ import annotations

import json
import os
import re
import time

import config
from utils.api_manager import APIManager
from utils.logger import get_logger

log = get_logger("llm")


class LLMError(RuntimeError):
    """Raised when no key / retry could produce an answer."""


def extract_json(text: str) -> dict:
    """Parse JSON even when the model wraps it in ``` fences or adds chatter."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            return json.loads(text[start : end + 1])
        raise


class LLMClient:
    def __init__(self, offline: bool | None = None, model: str | None = None,
                 api_manager: APIManager | None = None, max_retries: int = 3) -> None:
        self.api = api_manager or APIManager()
        self.model = model or config.MODEL
        self.max_retries = max_retries
        forced = offline if offline is not None else os.getenv("LLM_MODE", "").lower() == "offline"
        self._forced_offline = forced
        self.calls = 0
        self.failures = 0

    @property
    def offline(self) -> bool:
        return self._forced_offline or not self.api.has_keys

    # ------------------------------------------------------------------ core
    def generate(self, prompt: str, temperature: float = 0.7, json_mode: bool = False) -> str:
        if self.offline:
            raise LLMError("LLM is offline (no API key or --offline flag).")
        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:  # pragma: no cover
            raise LLMError("google-genai is not installed. Run: pip install -r requirements.txt") from exc

        last_error: Exception | None = None
        for index, key in enumerate(self.api.get_blog_keys(), start=1):
            client = genai.Client(api_key=key)
            for attempt in range(1, self.max_retries + 1):
                try:
                    cfg = types.GenerateContentConfig(
                        temperature=temperature,
                        response_mime_type="application/json" if json_mode else None,
                    )
                    resp = client.models.generate_content(model=self.model, contents=prompt, config=cfg)
                    self.calls += 1
                    if not (resp.text or "").strip():
                        raise LLMError("Empty response from model.")
                    return resp.text
                except Exception as exc:  # noqa: BLE001 - SDK raises many types
                    last_error = exc
                    self.failures += 1
                    msg = str(exc)
                    if "429" in msg or "RESOURCE_EXHAUSTED" in msg:
                        log.warning("Key #%d quota reached -> switching key", index)
                        break
                    if "404" in msg or "NOT_FOUND" in msg:
                        raise LLMError(
                            f"Model '{self.model}' was not found. Set GEMINI_MODEL in .env to a model "
                            f"your key can use. Original error: {msg[:160]}"
                        ) from exc
                    if any(code in msg for code in ("401", "403", "API_KEY_INVALID", "PERMISSION_DENIED")):
                        log.warning("Key #%d rejected -> switching key", index)
                        break
                    wait = min(30, 2 ** attempt)
                    log.warning("Gemini busy/error (%s). Retry %d/%d in %ds", msg[:80], attempt, self.max_retries, wait)
                    time.sleep(wait)
        raise LLMError(f"All Gemini keys/retries failed: {last_error}")

    def generate_json(self, prompt: str, temperature: float = 0.4) -> dict:
        raw = self.generate(prompt, temperature=temperature, json_mode=True)
        try:
            return extract_json(raw)
        except (json.JSONDecodeError, ValueError):
            log.warning("Model returned invalid JSON -> asking once more")
            fixed = self.generate(
                "Return ONLY the following content as strictly valid JSON, no commentary:\n\n" + raw,
                temperature=0.0,
                json_mode=True,
            )
            return extract_json(fixed)
