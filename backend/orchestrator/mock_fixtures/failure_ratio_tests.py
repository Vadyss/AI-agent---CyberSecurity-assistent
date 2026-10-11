# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
from skill import run

PARAMS = {"group_by": "src_ip", "window_s": 10}
EVENTS = [{"_line": 0, "ts": 1, "src_ip": "a", "outcome": "failure"}]


def test_empty():
    assert run([], PARAMS) == []


def test_all_failure():
    assert run(EVENTS, PARAMS)[0]["failure_ratio"] == 1


def test_duplicate_failure():
    assert run(EVENTS + EVENTS, PARAMS)[-1]["failure_ratio"] == 1


def test_missing_group():
    assert run([{"_line": 0, "ts": 1}], PARAMS) == []
