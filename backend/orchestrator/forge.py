# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
"""The forge proposes source text. Only gatekeeper can execute or store it."""
from __future__ import annotations


from .llm import LlmClient, json_chat, json_value, prompt_for, untrusted


class Forge:
    def __init__(self, client: LlmClient):
        self.client = client
        self.system = prompt_for("forge")

    async def build(self, spec, catalog, sample, feedback=None, *, attempt=1, request=""):
        user = untrusted("catalog", catalog)
        user += "\n" + "\n".join(untrusted(name, value) for name, value in
                                  (("specification", spec), ("sample", sample), ("feedback", feedback)))
        return await json_chat(self.client, "forge", self.system, user,
                               {"request": request, "spec": json_value(spec), "catalog": json_value(catalog),
                                "attempt": attempt, "feedback": json_value(feedback)})
