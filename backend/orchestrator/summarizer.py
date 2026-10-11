# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
"""A failed optional summary never invalidates an already verified rule."""
from __future__ import annotations

from .llm import LlmClient, LlmError, json_chat, json_value, prompt_for, untrusted


def fallback_summary(plan, recipe, metrics_tuning, metrics_validation, stats) -> str:
    counters = json_value(stats)
    metrics = json_value(metrics_validation)
    built = counters.get("skills_built", 0)
    reused = counters.get("skills_reused", 0)
    return (f"Rule {recipe.get('name', 'detection')} passed tuning and independent validation. "
            f"New skills: {built}, reused: {reused}. "
            f"Validation: detected {metrics.get('true_positives', 0)} attacks, "
            f"false positives {metrics.get('false_positives', 0)}. Awaiting your approval.")[:1000]


class Summarizer:
    def __init__(self, client: LlmClient):
        self.client = client
        self.system = prompt_for("summary")

    async def summarize(self, request, plan, recipe, metrics_tuning, metrics_validation, stats) -> str:
        context = {"request": request, "plan": json_value(plan), "recipe": json_value(recipe),
                   "metrics_tuning": json_value(metrics_tuning), "metrics_validation": json_value(metrics_validation),
                   "stats": json_value(stats)}
        user = "\n".join(untrusted(name, value) for name, value in context.items())
        try:
            result = await json_chat(self.client, "summary", self.system, user, context)
            if isinstance(result, dict) and isinstance(result.get("text"), str) and result["text"].strip():
                text = "".join(c for c in result["text"] if c in "\n\t" or (ord(c) >= 32 and ord(c) != 127))
                return text if len(text) <= 1000 else text[:999] + "…"
        except LlmError:
            pass
        return fallback_summary(plan, recipe, metrics_tuning, metrics_validation, stats)
