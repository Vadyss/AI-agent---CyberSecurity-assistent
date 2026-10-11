# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
from skill import run

PREFIX = "2026-10-05T08:01:12.123456+02:00 bastion sshd[5120]: "


def test_empty():
    assert run([], {}) == []


def test_accepted():
    row = run([PREFIX + "Accepted publickey for alice from 10.0.0.1 port 51122 ssh2: ED25519 SHA256:key"], {})[0]
    assert row["event"] == "accepted" and row["outcome"] == "success" and row["method"] == "publickey"


def test_failed():
    row = run([PREFIX + "Failed password for bob from 10.0.0.1 port 51122 ssh2"], {})[0]
    assert row["outcome"] == "failure" and row["invalid_user"] is False


def test_failed_invalid():
    row = run([PREFIX + "Failed password for invalid user injection_text from 203.0.113.7 port 40311 ssh2"], {})[0]
    assert row["user"] == "injection_text" and row["invalid_user"] is True


def test_invalid_user():
    row = run([PREFIX + "Invalid user oracle from 203.0.113.7"], {})[0]
    assert row["event"] == "invalid_user" and row["outcome"] is None and row["src_port"] is None


def test_closed():
    row = run([PREFIX + "Connection closed by invalid user oracle 203.0.113.7 port 40311 [preauth]"], {})[0]
    assert row["event"] == "closed" and row["outcome"] is None


def test_disconnected():
    row = run([PREFIX + "Disconnected from user alice 10.0.0.1 port 51122"], {})[0]
    assert row["event"] == "disconnected" and row["outcome"] is None


def test_nonsense():
    assert run(["nonsense", PREFIX + "Unknown message"], {}) == []


def test_timezone_and_line():
    original = PREFIX + "Failed password for bob from 10.0.0.1 port 51122 ssh2"
    equivalent = original.replace("08:01:12.123456+02:00", "06:01:12.123456+00:00")
    rows = run(["nonsense", original, equivalent], {})
    assert rows[0]["_line"] == 1 and rows[0]["ts"] == rows[1]["ts"]
