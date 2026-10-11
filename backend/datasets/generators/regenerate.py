# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
"""Development-only regeneration entry point; production only reads artifacts."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from generate_ssh import write as write_ssh
from generate_web import write as write_web


def regenerate(root: Path) -> None:
    write_ssh(root)
    write_web(root)
    catalog = {
        "log_sources": {
            "ssh": {"description": "OpenSSH auth.log with RFC 3339 timestamps", "file": "auth.log"},
            "web": {"description": "nginx access.log in combined format", "file": "access.log"}},
        "attack_types": {
            "ssh_bruteforce": {"log_source": "ssh", "description": "One IP address tries many passwords for one user."},
            "ssh_password_spraying": {"log_source": "ssh", "description": "One IP address tries a few passwords across many different users."},
            "ssh_distributed_bruteforce": {"log_source": "ssh", "description": "Many IP addresses attack one user."},
            "web_dir_bruteforce": {"log_source": "web", "description": "One IP address rapidly probes many paths on a web server."}}}
    (root / "catalog.json").write_text(json.dumps(catalog, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8", newline="\n")
    paths = [root / "catalog.json"] + sorted(root.glob("*/*/*.log")) + sorted(root.glob("*/*/labels.json"))
    manifest = "".join(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(root).as_posix()}\n" for path in sorted(paths))
    (root / "MANIFEST.sha256").write_text(manifest, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1])
    regenerate(parser.parse_args().output)
