# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
"""Count events by group in an inclusive sliding time window."""
from collections import defaultdict


def run(inputs, params):
    fields = params["group_by"]
    fields = [fields] if isinstance(fields, str) else fields
    window = params["window_s"]
    ts_field = params.get("ts_field", "ts")
    groups = defaultdict(list)
    for event in inputs:
        if all(field in event for field in fields) and ts_field in event and "_line" in event:
            groups[tuple(event[field] for field in fields)].append(event)
    results = []
    for key, events in groups.items():
        events.sort(key=lambda event: (event[ts_field], event["_line"]))
        left = 0
        right = 0
        for index, event in enumerate(events):
            end = event[ts_field]
            while right < len(events) and events[right][ts_field] <= end:
                right += 1
            while left < right and events[left][ts_field] < end - window:
                left += 1
            results.append({"group": dict(zip(fields, key)), "window_start": float(end - window),
                            "window_end": float(end), "count": right - left,
                            "_lines": [item["_line"] for item in events[left:min(right, left + 500)]]})
    return results
