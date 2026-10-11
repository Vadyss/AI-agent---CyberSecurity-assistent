# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
"""Append-only audit trail whose canonical records form a SHA-256 chain."""
from __future__ import annotations

from contextvars import ContextVar
from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
import threading
import os
import re
from .recipe import canonical_json

ZERO_HASH = "0" * 64
LOGGER = logging.getLogger(__name__)
# Who caused the records written in the current context ("user:<id>",
# "api_key:<id>", "local:local"); None means the system itself.
AUDIT_ACTOR: ContextVar[str | None] = ContextVar("audit_actor", default=None)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def record_hash(record: dict) -> str:
    payload = {key: value for key, value in record.items() if key != "hash"}
    return hashlib.sha256((record["prev_hash"] + canonical_json(payload)).encode("utf-8")).hexdigest()


def verify_audit(path: Path) -> tuple[bool, list[str]]:
    errors = []
    previous = ZERO_HASH
    sequence = 0
    if not Path(path).exists():
        return True, []
    for index, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        try:
            record = json.loads(line)
            if type(record.get("n")) is not int or record["n"] != sequence + 1:
                errors.append(f"Line {index}: invalid sequence.")
            if record.get("prev_hash") != previous or record.get("hash") != record_hash(record):
                errors.append(f"Line {index}: broken digest chain.")
            sequence = record["n"]
            previous = record["hash"]
        except (ValueError, TypeError, KeyError, AttributeError):
            errors.append(f"Line {index}: invalid JSON entry.")
    return not errors, errors


def bounded_detail(value: object, depth: int = 0) -> object:
    if depth > 8:
        return "…"
    if isinstance(value, dict):
        output = {}
        for key, child in list(value.items())[:50]:
            key = str(key)
            if key == "code" and set(value) == {"code", "detail"} and isinstance(child, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{1,49}", child):
                output[key] = child
            elif key.lower() in {"code", "tests", "token", "secret", "api_key", "authorization", "apify_token", "llm_api_key", "elevenlabs_api_key"}:
                output[key] = "[redacted]"
            else:
                output[key] = bounded_detail(child, depth + 1)
        return output
    if isinstance(value, (list, tuple)):
        return [bounded_detail(v, depth + 1) for v in value[:50]]
    if isinstance(value, str):
        return value[:1000]
    if value is None or type(value) in (int, float, bool):
        return value
    if hasattr(value, "model_dump"):
        return bounded_detail(value.model_dump(mode="json"), depth + 1)
    return str(value)[:300]


class AuditLog:
    def __init__(self, data_dir: Path):
        self.path = Path(data_dir) / "audit.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink():
            raise ValueError("The audit must not be a symbolic link.")
        self.lock = threading.Lock()
        self.n = 0
        self.previous = ZERO_HASH
        ok, errors = verify_audit(self.path)
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                try:
                    record = json.loads(line)
                    if isinstance(record, dict) and type(record.get("n")) is int and isinstance(record.get("hash"), str):
                        self.n, self.previous = record["n"], record["hash"]
                except ValueError:
                    continue
        if not ok:
            LOGGER.warning("The audit chain was compromised: %s", errors[:3])
            self.append("audit_chain_broken", detail={"errors": errors[:10]})

    def append(self, kind: str, run_id: str | None = None, detail: dict | None = None) -> dict:
        with self.lock:
            record = {"n": self.n + 1, "ts": now_iso(), "run_id": run_id, "kind": kind,
                      "actor": AUDIT_ACTOR.get(), "detail": bounded_detail(detail or {}),
                      "prev_hash": self.previous}
            record["hash"] = record_hash(record)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(canonical_json(record) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            self.n, self.previous = record["n"], record["hash"]
            return record
