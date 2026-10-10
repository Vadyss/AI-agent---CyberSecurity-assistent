# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. All rights reserved.
"""Environment is read only here; secrets are excluded from repr."""
from __future__ import annotations

import os
import math
from dataclasses import dataclass, field
from pathlib import Path

from gatekeeper.types import GatekeeperConfig

BACKEND_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Settings:
    llm_provider: str = "apify"
    apify_token: str = field(default="", repr=False)
    llm_api_key: str = field(default="", repr=False)
    llm_base_url: str = "https://piquant-peacoat--llm-relay.apify.actor/v1"
    llm_model: str = "anthropic/claude-sonnet-5.5"
    llm_model_planner: str = "anthropic/claude-sonnet-5.5"
    llm_model_forge: str = "anthropic/claude-sonnet-5.5"
    llm_model_rule: str = "anthropic/claude-sonnet-5.5"
    llm_model_summary: str = "anthropic/claude-sonnet-5.5"
    llm_model_examiner: str = "deepseek/deepseek-v4.1-flash"
    llm_model_fallback: str = "deepseek/deepseek-v4.1-flash"
    llm_reasoning_effort: str = ""
    llm_timeout_s: float = 180
    llm_max_tokens: int = 16000
    llm_max_tokens_cap: int = 32000
    llm_max_retries: int = 2
    llm_max_calls_per_run: int = 25
    mock_scenario: str = ""
    mock_delay_ms: int = 400
    sandbox_url: str = "http://sandbox:8000"
    sandbox_socket: str = ""
    sandbox_timeout_s: float = 30
    data_dir: Path = Path("/data")
    datasets_dir: Path = BACKEND_ROOT / "datasets"
    policy_path: Path = BACKEND_ROOT / "policy" / "policy.yaml"
    seed_skills_dir: Path = BACKEND_ROOT / "seed_skills"
    elevenlabs_api_key: str = field(default="", repr=False)
    elevenlabs_voice_id: str = ""
    elevenlabs_model_id: str = "eleven_multilingual_v2"
    examiner_enabled: bool = False
    run_timeout_s: float = 1500
    cors_origins: tuple[str, ...] = ("http://localhost:3000", "http://127.0.0.1:3000")
    log_level: str = "INFO"

    @classmethod
    def from_env(cls, *, load_env_file: bool = False) -> Settings:
        if load_env_file:
            from dotenv import load_dotenv

            load_dotenv()
        kwargs = {}
        defaults = cls()
        for name in cls.__dataclass_fields__:
            raw = os.environ.get(name.upper())
            if raw is None:
                continue
            if raw == "" and name not in {"apify_token", "llm_api_key", "elevenlabs_api_key",
                                          "elevenlabs_voice_id", "mock_scenario"}:
                continue
            default = getattr(defaults, name)
            if isinstance(default, bool):
                if raw.lower() not in {"true", "false", "1", "0"}:
                    raise ValueError(f"Invalid configuration {name.upper()}.")
                kwargs[name] = raw.lower() in {"true", "1"}
            elif isinstance(default, Path):
                kwargs[name] = Path(raw)
            elif isinstance(default, int):
                kwargs[name] = int(raw)
            elif isinstance(default, float):
                kwargs[name] = float(raw)
            elif name == "cors_origins":
                kwargs[name] = tuple(x.strip() for x in raw.split(",") if x.strip())
            else:
                kwargs[name] = raw
        model = kwargs.get("llm_model", defaults.llm_model)
        for role in ("planner", "forge", "rule", "summary"):
            kwargs.setdefault(f"llm_model_{role}", model)
        settings = cls(**kwargs)
        if settings.llm_provider not in {"apify", "openai_compatible", "mock"}:
            raise ValueError("Invalid LLM provider.")
        if settings.llm_reasoning_effort not in {"", "minimal", "low", "medium", "high", "xhigh", "max"}:
            raise ValueError("Invalid LLM reasoning effort.")
        if settings.mock_scenario and settings.mock_scenario not in {"A", "B", "C", "D", "E", "F"}:
            raise ValueError("Invalid mock scenario.")
        if not 0 <= settings.llm_max_retries <= 2 or not 1 <= settings.llm_max_calls_per_run <= 25:
            raise ValueError("Invalid LLM limits.")
        if any(not math.isfinite(v) or v <= 0 for v in (settings.llm_timeout_s, settings.sandbox_timeout_s, settings.run_timeout_s)):
            raise ValueError("Timeouts must be positive.")
        if not 1 <= settings.llm_max_tokens <= settings.llm_max_tokens_cap or settings.mock_delay_ms < 0:
            raise ValueError("Invalid LLM limits.")
        return settings

    def gatekeeper_config(self) -> GatekeeperConfig:
        return GatekeeperConfig(self.data_dir, self.datasets_dir, self.policy_path,
                                self.seed_skills_dir, self.examiner_enabled)
