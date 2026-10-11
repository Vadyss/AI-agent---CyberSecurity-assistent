# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
from collections import Counter, defaultdict


def run(inputs, params):
    fields = params["group_by"]
    fields = [fields] if isinstance(fields, str) else fields
    distinct = params["distinct_field"]
    window = params["window_s"]
    ts_field = params.get("ts_field", "ts")
    groups = defaultdict(list)
    for event in inputs:
        if all(field in event for field in fields) and distinct in event and ts_field in event and "_line" in event:
            groups[tuple(event[field] for field in fields)].append(event)
    results = []
    for key, events in groups.items():
        events.sort(key=lambda event: (event[ts_field], event["_line"]))
        values = Counter()
        left = 0
        right = 0
        for event in events:
            end = event[ts_field]
            while right < len(events) and events[right][ts_field] <= end:
                values[events[right][distinct]] += 1
                right += 1
            while left < right and events[left][ts_field] < end - window:
                value = events[left][distinct]
                values[value] -= 1
                if values[value] == 0:
                    values.pop(value)
                left += 1
            results.append({"group": dict(zip(fields, key)), "window_start": float(end - window),
                            "window_end": float(end), "distinct_count": len(values),
                            "_lines": [item["_line"] for item in events[left:min(right, left + 500)]]})
    return results
