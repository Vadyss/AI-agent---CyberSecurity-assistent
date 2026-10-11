# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
"""Parse RFC 3339 OpenSSH authentication logs into structured events."""
from datetime import datetime
import re

HEADER = re.compile(r"^(\S+)\s+(\S+)\s+sshd\[(\d+)\]:\s+(.*)$")
AUTH = re.compile(r"^(Accepted|Failed) (\S+) for (invalid user )?(\S+) from (\S+) port (\d+)(?:\s.*)?$")
INVALID = re.compile(r"^Invalid user (\S+) from (\S+)(?: port (\d+))?$")
CLOSED = re.compile(r"^Connection closed by (authenticating|invalid) user (\S+) (\S+) port (\d+)(?:\s.*)?$")
DISCONNECTED = re.compile(r"^Disconnected from user (\S+) (\S+) port (\d+)(?:\s.*)?$")


def run(inputs, params):
    results = []
    for index, line in enumerate(inputs):
        if not isinstance(line, str):
            continue
        header = HEADER.match(line)
        if not header:
            continue
        try:
            stamp = datetime.fromisoformat(header.group(1))
            if stamp.tzinfo is None:
                continue
            ts = stamp.timestamp()
        except (ValueError, OverflowError):
            continue
        message = header.group(4)
        match = AUTH.match(message)
        if match:
            event = "accepted" if match.group(1) == "Accepted" else "failed"
            outcome = "success" if event == "accepted" else "failure"
            method, invalid, user, ip, port = match.group(2), bool(match.group(3)), match.group(4), match.group(5), int(match.group(6))
        else:
            match = INVALID.match(message)
            if match:
                event, outcome, method, invalid = "invalid_user", None, None, True
                user, ip = match.group(1), match.group(2)
                port = int(match.group(3)) if match.group(3) else None
            else:
                match = CLOSED.match(message)
                if match:
                    event, outcome, method = "closed", None, None
                    invalid = match.group(1) == "invalid"
                    user, ip, port = match.group(2), match.group(3), int(match.group(4))
                else:
                    match = DISCONNECTED.match(message)
                    if not match:
                        continue
                    event, outcome, method, invalid = "disconnected", None, None, False
                    user, ip, port = match.group(1), match.group(2), int(match.group(3))
        results.append({"_line": index, "ts": ts, "host": header.group(2), "program": "sshd",
                        "pid": int(header.group(3)), "event": event, "outcome": outcome,
                        "user": user, "invalid_user": invalid, "src_ip": ip, "src_port": port, "method": method})
    return results
