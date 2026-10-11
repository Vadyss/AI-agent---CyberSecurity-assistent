# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
"""Byte-reproducible combined access logs and simultaneous ground truth."""
from __future__ import annotations

import argparse
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path


def generate(dataset: str) -> tuple[list[str], dict]:
    if dataset not in {"tuning", "validation"}:
        raise ValueError("Unknown dataset")
    tuning = dataset == "tuning"
    seed = 1001 if tuning else 2002
    rng = random.Random(seed + 7000)
    base = datetime(2026, 10, 5 if tuning else 6, tzinfo=timezone(timedelta(hours=2)))
    rows = []
    instances = []

    def add(seconds, ip, path, status, attack=None):
        moment = base + timedelta(seconds=seconds)
        # Explicit English month avoids dependence on host locale.
        stamp = f"{moment.day:02d}/Oct/{moment.year}:{moment:%H:%M:%S} +0200"
        line = f'{ip} - - [{stamp}] "GET {path} HTTP/1.1" {status} 153 "-" "Mozilla/5.0"'
        rows.append((moment, len(rows), line, attack))

    for i in range(1500):
        add(i * 57 + rng.randint(0, 10), f"{'10.0' if tuning else '10.1'}.20.{10 + i % 40}",
            f"/page/{i % 20}", 404 if i % 13 == 0 else 200)
    for index in range(6):
        attack = f"web_atk_{index + 1:04d}"
        ip = f"{'203.0.113' if tuning else '192.0.2'}.{220 + index}"
        count = rng.randint(50, 200)
        start = 3600 + index * 10800
        for j in range(count):
            add(start + int(270 * j / (count - 1)), ip, f"/scan_{index}/path_{j}.php", 200 if j % 17 == 0 else 404, attack)
        instances.append({"id": attack, "attack_type": "web_dir_bruteforce", "lines": [],
                          "start": (base + timedelta(seconds=start)).isoformat(),
                          "end": (base + timedelta(seconds=start + 270)).isoformat(),
                          "src_ips": [ip], "users": [], "notes": ""})
    rows.sort(key=lambda row: (row[0], row[1]))
    by_id = {instance["id"]: instance for instance in instances}
    for index, row in enumerate(rows):
        if row[3]:
            by_id[row[3]]["lines"].append(index)
    return [row[2] for row in rows], {"dataset": dataset, "log_source": "web", "seed": seed,
                                     "generator_version": 1, "line_count": len(rows),
                                     "instances": instances, "counts": {"web_dir_bruteforce": 6}}


def write(root: Path) -> None:
    for dataset in ("tuning", "validation"):
        lines, labels = generate(dataset)
        target = root / "web" / dataset
        target.mkdir(parents=True, exist_ok=True)
        (target / "access.log").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        (target / "labels.json").write_text(json.dumps(labels, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1])
    write(parser.parse_args().output)
