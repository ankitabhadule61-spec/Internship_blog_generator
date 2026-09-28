"""Prompt + content-type loaders (prompts live in /prompts so they are easy to edit)."""
from __future__ import annotations

import json
from functools import lru_cache

import config


@lru_cache(maxsize=None)
def load_prompt(name: str) -> str:
    return (config.PROMPT_DIR / name).read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def load_content_types() -> dict:
    return json.loads((config.PROMPT_DIR / "content_types.json").read_text(encoding="utf-8"))


def content_type_config(content_type: str, region: str) -> dict:
    cfg = json.loads(json.dumps(load_content_types()[content_type]))  # deep copy
    cfg["extra_sections"] = [[h.replace("{region}", region), d] for h, d in cfg["extra_sections"]]
    return cfg


def render(template: str, **values) -> str:
    """str.format that raises a clear error if a placeholder is missing."""
    try:
        return template.format(**values)
    except KeyError as exc:  # pragma: no cover
        raise KeyError(f"Prompt placeholder {exc} was not supplied") from exc
