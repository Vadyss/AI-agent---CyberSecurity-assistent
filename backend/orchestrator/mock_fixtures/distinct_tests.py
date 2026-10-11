# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
from skill import run

PARAMS = {"group_by": "src_ip", "distinct_field": "user", "window_s": 10}


def test_empty():
    assert run([], PARAMS) == []


def test_window_boundary():
    rows = run([{"_line": 0, "ts": 0, "src_ip": "a", "user": "u"}, {"_line": 1, "ts": 10, "src_ip": "a", "user": "v"}], PARAMS)
    assert rows[-1]["distinct_count"] == 2


def test_duplicates():
    rows = run([{"_line": 0, "ts": 1, "src_ip": "a", "user": "u"}, {"_line": 1, "ts": 2, "src_ip": "a", "user": "u"}], PARAMS)
    assert rows[-1]["distinct_count"] == 1


def test_groups():
    rows = run([{"_line": 0, "ts": 1, "src_ip": "a", "user": "u"}, {"_line": 1, "ts": 2, "src_ip": "b", "user": "v"}], PARAMS)
    assert len(rows) == 2 and all(row["distinct_count"] == 1 for row in rows)


def test_missing_group():
    assert run([{"_line": 0, "ts": 1, "user": "u"}], PARAMS) == []
