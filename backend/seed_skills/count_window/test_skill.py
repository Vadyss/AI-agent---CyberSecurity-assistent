# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
from skill import run

PARAMS = {"group_by": "src_ip", "window_s": 10}


def test_empty():
    assert run([], PARAMS) == []


def test_one_group():
    events = [{"_line": i, "ts": i, "src_ip": "a"} for i in range(3)]
    assert [row["count"] for row in run(events, PARAMS)] == [1, 2, 3]


def test_multiple_groups():
    rows = run([{"_line": 0, "ts": 0, "src_ip": "a"}, {"_line": 1, "ts": 0, "src_ip": "b"}], PARAMS)
    assert len(rows) == 2 and all(row["count"] == 1 for row in rows)


def test_window_boundary():
    rows = run([{"_line": 0, "ts": 0, "src_ip": "a"}, {"_line": 1, "ts": 10, "src_ip": "a"}, {"_line": 2, "ts": 10.1, "src_ip": "a"}], PARAMS)
    assert [row["count"] for row in rows] == [1, 2, 2]


def test_missing_group():
    assert run([{"_line": 0, "ts": 0}], PARAMS) == []


def test_composite_group():
    params = {"group_by": ["src_ip", "user"], "window_s": 10}
    rows = run([{"_line": 0, "ts": 0, "src_ip": "a", "user": "u"}], params)
    assert rows[0]["group"] == {"src_ip": "a", "user": "u"}


def test_same_timestamp():
    rows = run([{"_line": 1, "ts": 0, "src_ip": "a"}, {"_line": 0, "ts": 0, "src_ip": "a"}], PARAMS)
    assert all(row["count"] == 2 and row["_lines"] == [0, 1] for row in rows)


def test_large_window_preserves_count_and_bounded_sorted_lines():
    events = [{"_line": i, "ts": 1, "src_ip": "a"} for i in range(750, 0, -1)]
    rows = run(events, PARAMS)
    assert len(rows) == 750 and all(row["count"] == 750 for row in rows)
    assert all(row["_lines"] == list(range(1, 501)) for row in rows)
