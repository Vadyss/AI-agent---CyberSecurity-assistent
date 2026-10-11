# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
"""Verified private datasets. Only the bounded tuning sample leaves gatekeeper."""
from __future__ import annotations

import copy
import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Dataset:
    lines: list[str]
    labels: dict


class DatasetStore:
    def __init__(self, root: Path, min_instances: int = 6, sample_lines: int = 20):
        self.root = Path(root).resolve()
        self.sample_lines = min(20, max(0, sample_lines))
        self.digests: dict[str, str] = {}
        self._datasets: dict[tuple[str, str], Dataset] = {}
        self._samples: dict[str, list[str]] = {}
        manifest = (self.root / "MANIFEST.sha256").read_bytes()
        self.manifest_sha256 = hashlib.sha256(manifest).hexdigest()
        for row in manifest.decode("utf-8").splitlines():
            if not row:
                continue
            match = re.fullmatch(r"([0-9a-f]{64})  ([^\n]+)", row)
            if match is None:
                raise ValueError("Invalid dataset manifest.")
            sha, relative = match.groups()
            path = (self.root / relative).resolve()
            if not path.is_relative_to(self.root) or relative in self.digests:
                raise ValueError("Invalid dataset manifest path.")
            if hashlib.sha256(path.read_bytes()).hexdigest() != sha:
                raise ValueError("Test data digest mismatch.")
            self.digests[relative] = sha
        if "catalog.json" not in self.digests:
            raise ValueError("The catalog has no verified digest.")
        self._catalog = json.loads((self.root / "catalog.json").read_text(encoding="utf-8"))
        expected = {"catalog.json"}
        for source, metadata in self._catalog["log_sources"].items():
            if re.fullmatch(r"[a-z][a-z0-9_]{0,49}", source) is None or metadata["file"] not in {"auth.log", "access.log"}:
                raise ValueError("Invalid data source.")
            attacks = {name for name, item in self._catalog["attack_types"].items() if item["log_source"] == source}
            for dataset in ("tuning", "validation"):
                log_name = f"{source}/{dataset}/{metadata['file']}"
                label_name = f"{source}/{dataset}/labels.json"
                expected.update((log_name, label_name))
                if log_name not in self.digests or label_name not in self.digests:
                    raise ValueError("The dataset has no verified digests.")
                lines = (self.root / log_name).read_text(encoding="utf-8").splitlines()
                labels = json.loads((self.root / label_name).read_text(encoding="utf-8"))
                if labels["dataset"] != dataset or labels["log_source"] != source or labels["line_count"] != len(lines):
                    raise ValueError("Invalid dataset metadata.")
                counts = Counter()
                all_indices: set[int] = set()
                identifiers: set[str] = set()
                for instance in labels["instances"]:
                    indices = instance["lines"]
                    if (instance["id"] in identifiers or instance["attack_type"] not in attacks or not indices
                            or len(set(indices)) != len(indices)
                            or any(type(i) is not int or not 0 <= i < len(lines) for i in indices)
                            or all_indices.intersection(indices)):
                        raise ValueError("Invalid dataset labels.")
                    identifiers.add(instance["id"])
                    all_indices.update(indices)
                    counts[instance["attack_type"]] += 1
                if dict(counts) != labels["counts"] or any(counts[name] < min_instances for name in attacks):
                    raise ValueError("The dataset does not have enough attack instances.")
                self._datasets[source, dataset] = Dataset(lines, labels)
        if set(self.digests) != expected:
            raise ValueError("The manifest does not match the complete dataset catalog.")

    def catalog(self) -> dict:
        return {"log_sources": {name: {"description": item["description"]} for name, item in self._catalog["log_sources"].items()},
                "attack_types": {name: {"log_source": item["log_source"], "description": item["description"]}
                                 for name, item in self._catalog["attack_types"].items()}}

    def load(self, source: str, dataset: str) -> Dataset:
        if (source, dataset) not in self._datasets:
            raise ValueError("Unknown dataset.")
        data = self._datasets[source, dataset]
        return Dataset(data.lines.copy(), copy.deepcopy(data.labels))

    def parser_lines(self, source: str, limit: int = 50) -> list[str]:
        return self.load(source, "tuning").lines[:limit]

    def sample(self, source: str) -> list[str]:
        if source not in self._samples:
            lines = self.load(source, "tuning").lines
            chosen: list[int] = []
            if source == "ssh":
                for pattern, count in ((r": Accepted ", 8), (r": Failed ", 6),
                                       (r": Invalid user ", 2), (r": (Connection closed|Disconnected) ", 2)):
                    chosen.extend([i for i, line in enumerate(lines) if re.search(pattern, line)][:count])
                longest = sorted(range(len(lines)), key=lambda i: (-len(lines[i]), i))
                for i in longest:
                    if i not in chosen:
                        chosen.append(i)
                        if len(chosen) >= 20:
                            break
            else:
                chosen = [i for i, line in enumerate(lines) if '" 404 ' not in line][:10]
                chosen += [i for i, line in enumerate(lines) if '" 404 ' in line][:10]
            self._samples[source] = [lines[i] for i in chosen[:self.sample_lines]]
        return self._samples[source].copy()


Datasets = DatasetStore
