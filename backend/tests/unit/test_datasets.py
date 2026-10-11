# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import pytest

from gatekeeper.datasets import DatasetStore

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "datasets"
FAILURE = re.compile(r"Failed password for (?:invalid user )?(\S+) from (\S+)")


def failures(lines, indices):
    events = []
    for index in indices:
        match = FAILURE.search(lines[index])
        if match:
            events.append((datetime.fromisoformat(lines[index].split()[0]).timestamp(), match.group(1), match.group(2)))
    return sorted(events)


def max_window(events, group_index, distinct_index=None):
    groups = defaultdict(list)
    for event in events:
        groups[event[group_index]].append(event)
    maximum = 0
    for group in groups.values():
        for event in group:
            window = [e for e in group if event[0] - 300 <= e[0] <= event[0]]
            value = len(window) if distinct_index is None else len({e[distinct_index] for e in window})
            maximum = max(maximum, value)
    return maximum


@pytest.mark.parametrize("dataset", ["tuning", "validation"])
def test_all_calibration_guarantees(dataset):
    data = DatasetStore(DATA).load("ssh", dataset)
    attack_indices = {i for instance in data.labels["instances"] for i in instance["lines"]}
    normal = failures(data.lines, set(range(len(data.lines))) - attack_indices)
    assert max_window(normal, 2) < 5
    assert max_window(normal, 2, 1) < 5
    assert max_window(normal, 1, 2) < 5
    for instance in data.labels["instances"]:
        events = failures(data.lines, instance["lines"])
        if instance["attack_type"] == "ssh_bruteforce":
            assert max_window(events, 2) >= 15
        elif instance["attack_type"] == "ssh_password_spraying":
            assert 8 <= max_window(events, 2, 1) < 25
        else:
            assert max_window(events, 1, 2) >= 8
    assert all(count >= 6 for count in data.labels["counts"].values())
    assert all(datetime.fromisoformat(data.lines[i].split()[0]) <= datetime.fromisoformat(data.lines[i + 1].split()[0]) for i in range(len(data.lines) - 1))
    instances = data.labels["instances"]
    assert all((datetime.fromisoformat(b["start"]) - datetime.fromisoformat(a["end"])).total_seconds() >= 1800 for a, b in zip(instances, instances[1:]))


def test_disjoint_identities():
    store = DatasetStore(DATA)
    tuning, validation = (store.load("ssh", name) for name in ("tuning", "validation"))
    for key in ("users", "src_ips"):
        first = {item for instance in tuning.labels["instances"] for item in instance[key]}
        second = {item for instance in validation.labels["instances"] for item in instance[key]}
        assert first.isdisjoint(second)
    pattern = re.compile(r"(?:for (?:invalid user )?|from user |by (?:invalid|authenticating) user )(\S+)")
    identities = []
    for data in (tuning, validation):
        identities.append({match.group(1) for line in data.lines if (match := pattern.search(line))})
    assert identities[0].isdisjoint(identities[1])


def test_manifest_and_generator_determinism(tmp_path):
    subprocess.run([sys.executable, str(DATA / "generators" / "regenerate.py"), "--output", str(tmp_path)], check=True)
    store = DatasetStore(DATA)
    for path, digest in store.digests.items():
        assert hashlib.sha256((tmp_path / path).read_bytes()).hexdigest() == digest
    assert (tmp_path / "MANIFEST.sha256").read_bytes() == (DATA / "MANIFEST.sha256").read_bytes()


def test_sample_is_private_and_bounded():
    store = DatasetStore(DATA)
    sample = store.sample("ssh")
    assert len(sample) == 20
    assert set(sample).issubset(set(store.load("ssh", "tuning").lines))
    assert set(sample).isdisjoint(set(store.load("ssh", "validation").lines))
    assert any("IGNORE_PREVIOUS_INSTRUCTIONS" in line for line in sample)
    assert sum(": Accepted " in line for line in sample) == 8
    assert sum(": Invalid user " in line for line in sample) == 2
    assert "file" not in json.dumps(store.catalog())
    sample.clear()
    assert len(store.sample("ssh")) == 20


@pytest.mark.parametrize("dataset", ["tuning", "validation"])
def test_web_calibration(dataset):
    data = DatasetStore(DATA).load("web", dataset)
    attack_indices = {i for instance in data.labels["instances"] for i in instance["lines"]}
    normal = defaultdict(list)
    for i, line in enumerate(data.lines):
        if i not in attack_indices and '" 404 ' in line:
            stamp = datetime.strptime(line.split("[")[1].split("]")[0], "%d/%b/%Y:%H:%M:%S %z").timestamp()
            normal[line.split()[0]].append(stamp)
    for events in normal.values():
        assert all(sum(ts - 300 <= other <= ts for other in events) < 15 for ts in events)
    assert len(data.labels["instances"]) >= 6
    for instance in data.labels["instances"]:
        assert 50 <= len(instance["lines"]) <= 200
        assert sum('" 404 ' in data.lines[i] for i in instance["lines"]) >= 20


def test_tampering_fails_closed(tmp_path):
    subprocess.run([sys.executable, str(DATA / "generators" / "regenerate.py"), "--output", str(tmp_path)], check=True)
    with (tmp_path / "ssh/tuning/auth.log").open("a") as stream:
        stream.write("tampered\n")
    with pytest.raises(ValueError, match="Test data digest mismatch"):
        DatasetStore(tmp_path)
