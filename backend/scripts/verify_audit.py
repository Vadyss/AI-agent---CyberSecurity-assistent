#!/usr/bin/env python3
# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. All rights reserved.
"""Verify persisted audit trails without modifying them.

Pass one audit.jsonl file, or a data directory: every tenant's chain under
it (``tenants/<id>/audit.jsonl``) is verified.
"""
from __future__ import annotations
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gatekeeper.audit import verify_audit


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the SHA-256 audit chains.")
    parser.add_argument("path", type=Path, nargs="?", default=Path("/data"))
    args = parser.parse_args()
    paths = sorted(args.path.rglob("audit.jsonl")) if args.path.is_dir() else [args.path]
    paths = [path for path in paths if path.is_file() and not path.is_symlink()]
    if not paths:
        print("No audit file found.", file=sys.stderr)
        return 1
    failed = False
    for path in paths:
        ok, errors = verify_audit(path)
        print(f"{path}: {'valid' if ok else 'BROKEN'}")
        for error in errors:
            print(f"  {error}", file=sys.stderr)
        failed |= not ok
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
