#!/usr/bin/env python3
# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
"""Write the published OpenAPI contract to Docs/api/openapi.json."""
from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
TARGET = BACKEND.parent / "Docs" / "api" / "openapi.json"


def render() -> str:
    from orchestrator.config import Settings
    from orchestrator.main import create_app
    from dataclasses import replace

    settings = replace(Settings(), environment="test", auth_mode="disabled", llm_provider="mock")
    return json.dumps(create_app(settings=settings).openapi(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(render(), encoding="utf-8")
    print(f"Wrote {TARGET}")
