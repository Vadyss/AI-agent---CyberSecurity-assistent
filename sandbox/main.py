# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
"""Compatibility entrypoint for existing ``uvicorn main:app`` commands."""
from __future__ import annotations

try:
    from .server import app
except ImportError:  # Docker runs this module from /app.
    from server import app
