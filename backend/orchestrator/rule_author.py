# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
"""Recipe proposal role; receives only authorized, bounded tuning feedback."""
from __future__ import annotations


from .llm import LlmClient, json_chat, json_value, prompt_for, untrusted


class RuleAuthor:
    def __init__(self, client: LlmClient):
        self.client = client
        self.system = prompt_for("rule_author")

    async def draft(self, request, plan, catalog, sample, prior, lessons, feedback=None, *, attempt=1):
        user = untrusted("catalog", catalog)
        user += "\n" + "\n".join(untrusted(name, value) for name, value in
                                  (("request", request), ("plan", plan), ("sample", sample), ("approved_recipe", prior),
                                   ("lessons", lessons), ("feedback", feedback)))
        return await json_chat(self.client, "rule_author", self.system, user,
                               {"request": request, "plan": json_value(plan), "catalog": json_value(catalog),
                                "prior": json_value(prior), "attempt": attempt, "feedback": json_value(feedback)})
