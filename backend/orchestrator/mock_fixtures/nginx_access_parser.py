# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
from datetime import datetime
import re

LINE = re.compile(r'^(\S+) \S+ \S+ \[([^\]]+)\] "(\S+) (\S+) (\S+)" (\d{3}) (\d+|-) "([^"]*)" "([^"]*)"$')


def run(inputs, params):
    results = []
    for index, line in enumerate(inputs):
        if not isinstance(line, str):
            continue
        match = LINE.match(line)
        if not match:
            continue
        try:
            ts = datetime.strptime(match.group(2), "%d/%b/%Y:%H:%M:%S %z").timestamp()
        except (ValueError, OverflowError):
            continue
        results.append({"_line": index, "ts": ts, "src_ip": match.group(1), "method": match.group(3),
                        "path": match.group(4), "protocol": match.group(5), "status": int(match.group(6)),
                        "bytes": int(match.group(7)) if match.group(7) != "-" else 0,
                        "referer": match.group(8), "user_agent": match.group(9)})
    return results
