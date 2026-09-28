"""Tiny logging helper so every agent prints consistent, timestamped output."""
from __future__ import annotations

import logging
import sys

_CONFIGURED = False


def get_logger(name: str = "blogbot") -> logging.Logger:
    global _CONFIGURED
    if not _CONFIGURED:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s", "%H:%M:%S"))
        root = logging.getLogger("blogbot")
        root.setLevel(logging.INFO)
        root.addHandler(handler)
        root.propagate = False
        _CONFIGURED = True
    return logging.getLogger(name if name.startswith("blogbot") else f"blogbot.{name}")
