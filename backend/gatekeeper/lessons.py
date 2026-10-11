# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
"""Persistent bounded lessons with an identity-free recipe shape."""
from __future__ import annotations

import json
import os
from pathlib import Path
import threading
from .audit import now_iso
from .names import clip
from .recipe import canonical_json, number


def recipe_shape(recipe: dict | None) -> dict:
    if not isinstance(recipe, dict):
        return {}
    aggregation = recipe.get("aggregation", {})
    params = aggregation.get("params", {}) if isinstance(aggregation, dict) else {}
    condition = recipe.get("condition", {})
    shape = {"parser": recipe.get("parser"),
             "filter": [{"field": f.get("field"), "op": f.get("op")} for f in recipe.get("filter", []) if isinstance(f, dict)],
             "aggregation": {"skill": aggregation.get("skill"), "param_names": sorted(params),
                             "window_s": params.get("window_s") if number(params.get("window_s")) else None},
             "condition": {"field": condition.get("field"), "op": condition.get("op"),
                           "value": condition.get("value") if number(condition.get("value")) else None}}
    return shape


class Lessons:
    def __init__(self, data_dir: Path):
        self.path = Path(data_dir) / "lessons.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink():
            raise ValueError("Lessons must not be a symbolic link.")
        self.lock = threading.Lock()

    def record(self, run_id: str, attack_type: str, kind: str, text: str, recipe: dict | None = None) -> dict:
        record = {"ts": now_iso(), "run_id": run_id, "attack_type": attack_type, "kind": kind,
                  "text": clip(text, 300), "recipe_shape": recipe_shape(recipe)}
        with self.lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(canonical_json(record) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        return record

    def read(self, limit: int = 5, attack_type: str | None = None) -> list[dict]:
        limit = max(0, min(limit, 50))
        if not limit or not self.path.exists():
            return []
        with self.lock:
            rows = []
            for line in self.path.read_text(encoding="utf-8").splitlines():
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict) and (attack_type is None or row.get("attack_type") == attack_type):
                    rows.append(row)
            return rows[-limit:]
