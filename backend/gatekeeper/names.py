# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
"""Identifiers, bounded human text and containment checks."""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from .types import Violation

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,49}$")
FIELD_RE = re.compile(r"^[a-z_][a-z0-9_]{0,49}$")
RUN_ID_RE = re.compile(r"^run_[a-z0-9]{4,32}$")


def valid_name(value: object) -> bool:
    return isinstance(value, str) and NAME_RE.fullmatch(value) is not None


def valid_field(value: object) -> bool:
    return isinstance(value, str) and FIELD_RE.fullmatch(value) is not None


def valid_run_id(value: object) -> bool:
    return isinstance(value, str) and RUN_ID_RE.fullmatch(value) is not None


def require_name(value: str) -> str:
    if not valid_name(value):
        raise ValueError("Invalid name.")
    return value


def require_run_id(value: str) -> str:
    if not valid_run_id(value):
        raise ValueError("Invalid run identifier.")
    return value


def safe_join(base: Path, *parts: str) -> Path:
    root = Path(base).resolve()
    candidate = root.joinpath(*parts).resolve()
    if candidate == root or root not in candidate.parents:
        raise ValueError("The path leaves the allowed directory.")
    return candidate


def clip(value: str, limit: int) -> str:
    cleaned = "".join(c for c in value if c in "\n\t" or not unicodedata.category(c).startswith("C"))
    return cleaned if len(cleaned) <= limit else cleaned[:limit - 1] + "…"


def violations_unique(values: list[Violation]) -> list[Violation]:
    seen: set[tuple[str, str]] = set()
    output = []
    for item in values:
        key = (item.code, item.detail)
        if key not in seen:
            seen.add(key)
            output.append(item)
    return output[:10]
