# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
from skill import run

FAILED = '203.0.113.50 - - [05/Oct/2026:10:00:01 +0200] "GET /admin.php HTTP/1.1" 404 153 "-" "Mozilla/5.0"'


def test_empty():
    assert run([], {}) == []


def test_status_and_path():
    row = run([FAILED], {})[0]
    assert row["status"] == 404 and row["path"] == "/admin.php" and row["_line"] == 0


def test_success():
    assert run([FAILED.replace("404", "200")], {})[0]["status"] == 200


def test_nonsense():
    assert run(["nonsense"], {}) == []


def test_timezone():
    equivalent = FAILED.replace("10:00:01 +0200", "08:00:01 +0000")
    assert run([FAILED], {})[0]["ts"] == run([equivalent], {})[0]["ts"]
