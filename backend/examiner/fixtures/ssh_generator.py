# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
from datetime import datetime, timedelta, timezone
import hashlib


def generate(seed):
    base = datetime(2026, 10, 5, 8, tzinfo=timezone.utc)
    identity = hashlib.sha256(str(seed).encode("utf-8")).hexdigest()
    lines = []
    instances = []
    for case in range(6):
        start = base + timedelta(hours=case * 2)
        ip = "2001:db8:" + identity[:4] + ":" + identity[4:8] + ":" + identity[8:12] + ":" + identity[12:16] + "::" + str(case + 1)
        user = "custom_target_" + identity[:16] + "_" + str(case)
        indices = []
        attempts = 18 + case * 2
        for index in range(attempts):
            stamp = (start + timedelta(seconds=270 * index / (attempts - 1))).isoformat(timespec="microseconds")
            indices.append(len(lines))
            lines.append(stamp + " bastion sshd[5120]: Failed password for invalid user " + user + " from " + ip + " port 40311 ssh2")
        instances.append({"lines": indices})
        # This lookalike traffic is deliberately not labelled as attack.
        benign_ip = "2001:db8:" + identity[:4] + ":" + identity[4:8] + ":" + identity[8:12] + ":" + identity[12:16] + "::" + str(case + 100)
        for index in range(3):
            stamp = (start + timedelta(minutes=10, seconds=index * 20)).isoformat(timespec="microseconds")
            lines.append(stamp + " bastion sshd[5120]: Failed password for benign_" + identity[:16] + " from " + benign_ip + " port 50000 ssh2")
        stamp = (start + timedelta(minutes=11)).isoformat(timespec="microseconds")
        lines.append(stamp + " bastion sshd[5120]: Accepted password for benign_" + identity[:16] + " from " + benign_ip + " port 50000 ssh2")
    return {"lines": lines, "instances": instances}
