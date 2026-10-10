# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. All rights reserved.
"""Environment is read only here; secrets are excluded from repr."""
from __future__ import annotations

import os
import math
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

from gatekeeper.types import GatekeeperConfig

BACKEND_ROOT = Path(__file__).resolve().parents[1]
ENVIRONMENTS = {"production", "development", "test"}
ASYMMETRIC_ALGORITHMS = {"RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512", "EdDSA"}


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
    # Secure by default: production needs PostgreSQL and OIDC. Development and
    # test may disable authentication, which serves a single local tenant.
    environment: str = "production"
    database_url: str = field(default="", repr=False)
    auth_mode: str = "oidc"
    oidc_issuer: str = ""
    oidc_audience: str = ""
    oidc_jwks_url: str = ""
    oidc_algorithms: tuple[str, ...] = ("RS256", "ES256")
    bootstrap_admin_email: str = ""
    bootstrap_tenant_name: str = "Default"
    signup_mode: str = "invite_only"
    rate_limit_per_minute: int = 300
    run_rate_limit_per_minute: int = 10
    default_monthly_budget_usd: str = "50"
    default_max_runs_per_day: int = 200
    worker_concurrency: int = 2
    max_queued_runs_per_tenant: int = 10
    run_max_attempts: int = 2
    log_format: str = "json"
    # Prometheus metrics on a separate, unpublished port; 0 disables the endpoint.
    metrics_port: int = 9100

    @classmethod
    def from_env(cls, *, load_env_file: bool = False, llm_only: bool = False) -> Settings:
        """Read settings from the environment. ``llm_only`` skips server checks for CLI scripts."""
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
            elif isinstance(default, tuple):
                kwargs[name] = tuple(x.strip() for x in raw.split(",") if x.strip())
            else:
                kwargs[name] = raw
        model = kwargs.get("llm_model", defaults.llm_model)
        for role in ("planner", "forge", "rule", "summary"):
            kwargs.setdefault(f"llm_model_{role}", model)
        settings = cls(**kwargs)
        return settings.validate_llm() if llm_only else settings.validate()

    def validate_llm(self) -> Settings:
        if self.llm_provider not in {"apify", "openai_compatible", "mock"}:
            raise ValueError("Invalid LLM provider.")
        if self.llm_reasoning_effort not in {"", "minimal", "low", "medium", "high", "xhigh", "max"}:
            raise ValueError("Invalid LLM reasoning effort.")
        if self.mock_scenario and self.mock_scenario not in {"A", "B", "C", "D", "E", "F"}:
            raise ValueError("Invalid mock scenario.")
        if not 0 <= self.llm_max_retries <= 2 or not 1 <= self.llm_max_calls_per_run <= 25:
            raise ValueError("Invalid LLM limits.")
        if any(not math.isfinite(v) or v <= 0 for v in (self.llm_timeout_s, self.sandbox_timeout_s, self.run_timeout_s)):
            raise ValueError("Timeouts must be positive.")
        if not 1 <= self.llm_max_tokens <= self.llm_max_tokens_cap or self.mock_delay_ms < 0:
            raise ValueError("Invalid LLM limits.")
        return self

    def validate(self) -> Settings:
        """Refuse unsafe or inconsistent configuration before the server starts."""
        self.validate_llm()
        if self.environment not in ENVIRONMENTS:
            raise ValueError("ENVIRONMENT must be production, development or test.")
        if self.auth_mode not in {"oidc", "disabled"}:
            raise ValueError("AUTH_MODE must be oidc or disabled.")
        if self.environment == "production":
            if self.auth_mode != "oidc":
                raise ValueError("Authentication cannot be disabled in production.")
            if not self.database_url.startswith("postgresql+psycopg://"):
                raise ValueError("Production requires a PostgreSQL DATABASE_URL.")
            if self.llm_provider == "mock":
                raise ValueError("The mock language model is not allowed in production.")
            if self.oidc_issuer and not self.oidc_issuer.startswith("https://"):
                raise ValueError("The OIDC issuer must use HTTPS in production.")
        if self.environment == "development" and not self.database_url:
            raise ValueError("DATABASE_URL is required outside tests.")
        if self.auth_mode == "oidc" and not (self.oidc_issuer and self.oidc_audience and self.oidc_jwks_url):
            raise ValueError("OIDC_ISSUER, OIDC_AUDIENCE and OIDC_JWKS_URL are required.")
        if not self.oidc_algorithms or not set(self.oidc_algorithms) <= ASYMMETRIC_ALGORITHMS:
            raise ValueError("OIDC_ALGORITHMS must list asymmetric JWT algorithms only.")
        if self.log_format not in {"json", "text"}:
            raise ValueError("LOG_FORMAT must be json or text.")
        if not 0 <= self.metrics_port <= 65535:
            raise ValueError("METRICS_PORT must be a TCP port or 0.")
        if self.signup_mode not in {"invite_only", "self_service"}:
            raise ValueError("SIGNUP_MODE must be invite_only or self_service.")
        try:
            budget = Decimal(self.default_monthly_budget_usd)
        except InvalidOperation:
            raise ValueError("DEFAULT_MONTHLY_BUDGET_USD must be a decimal number.") from None
        if not budget.is_finite() or budget < 0:
            raise ValueError("DEFAULT_MONTHLY_BUDGET_USD must not be negative.")
        if min(self.rate_limit_per_minute, self.run_rate_limit_per_minute, self.default_max_runs_per_day,
               self.worker_concurrency, self.max_queued_runs_per_tenant, self.run_max_attempts) < 1:
            raise ValueError("Rate limits, quotas, workers and attempts must be positive.")
        return self

    def gatekeeper_config(self) -> GatekeeperConfig:
        return GatekeeperConfig(self.data_dir, self.datasets_dir, self.policy_path,
                                self.seed_skills_dir, self.examiner_enabled)
