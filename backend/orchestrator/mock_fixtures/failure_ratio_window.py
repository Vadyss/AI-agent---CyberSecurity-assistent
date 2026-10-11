# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
def run(inputs, params):
    fields = params["group_by"]
    fields = [fields] if isinstance(fields, str) else fields
    return [{"group": {field: event[field] for field in fields}, "window_start": event["ts"] - params["window_s"],
             "window_end": event["ts"], "failure_ratio": 0, "_lines": [event["_line"]]}
            for event in inputs if all(field in event for field in fields)]
