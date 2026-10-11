# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
"""Plain-text cleanup for untrusted display values."""
from __future__ import annotations

import re
import unicodedata


def strip_control(text: str) -> str:
    return "".join(c for c in text if c in "\n\t" or not unicodedata.category(c).startswith("C"))


def clip(text: str, limit: int) -> str:
    text = strip_control(text)
    return text if len(text) <= limit else text[:max(0, limit - 1)] + "…"


def safe_name(value: str) -> str:
    return clip(re.sub(r"[^a-z0-9_]", "_", str(value)), 50)
