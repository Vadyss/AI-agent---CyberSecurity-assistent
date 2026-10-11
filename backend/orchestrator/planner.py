# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
"""Planning role and composition of the four bounded proposal roles."""
from __future__ import annotations

from dataclasses import dataclass

from .llm import LlmClient, json_chat, json_value, make_client, prompt_for, untrusted


class Planner:
    def __init__(self, client: LlmClient):
        self.client = client
        self.system = prompt_for("planner")

    async def propose(self, request, catalog, samples, lessons, feedback=None, *, attempt=1):
        user = untrusted("catalog", catalog)
        user += "\n" + "\n".join(untrusted(name, value) for name, value in
                                  (("request", request), ("samples", samples), ("lessons", lessons), ("feedback", feedback)))
        return await json_chat(self.client, "planner", self.system, user,
                               {"request": request, "catalog": json_value(catalog), "attempt": attempt, "feedback": json_value(feedback)})


@dataclass
class Roles:
    planner: Planner
    forge: object
    rule_author: object
    summarizer: object
    client: LlmClient

    async def close(self):
        await self.client.close()


def make_roles(settings, client: LlmClient | None = None) -> Roles:
    from .forge import Forge
    from .rule_author import RuleAuthor
    from .summarizer import Summarizer
    client = client or make_client(settings)
    return Roles(Planner(client), Forge(client), RuleAuthor(client), Summarizer(client), client)
