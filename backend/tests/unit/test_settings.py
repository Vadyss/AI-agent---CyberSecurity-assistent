# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest

from orchestrator.config import Settings


@pytest.fixture(autouse=True)
def isolated_settings_environment(monkeypatch):
    for name in Settings.__dataclass_fields__:
        monkeypatch.delenv(name.upper(), raising=False)
    # Parsing tests run in the test environment; production rules are tested explicitly.
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("AUTH_MODE", "disabled")


TEST_DEFAULTS = replace(Settings(), environment="test", auth_mode="disabled")


def test_blank_names_only_env_uses_defaults(monkeypatch):
    for name in Settings.__dataclass_fields__:
        monkeypatch.setenv(name.upper(), "")
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("AUTH_MODE", "disabled")
    assert Settings.from_env() == replace(Settings(), environment="test", auth_mode="disabled")


def test_defaults_are_production_and_refuse_to_start_without_database_and_oidc(monkeypatch):
    assert (Settings().environment, Settings().auth_mode) == ("production", "oidc")
    monkeypatch.delenv("ENVIRONMENT")
    monkeypatch.delenv("AUTH_MODE")
    with pytest.raises(ValueError, match="PostgreSQL"):
        Settings.from_env()


PRODUCTION = {"ENVIRONMENT": "production", "AUTH_MODE": "oidc", "DATABASE_URL": "postgresql+psycopg://u:p@db/t", "OIDC_ISSUER": "https://idp.example",
              "OIDC_AUDIENCE": "toolsmith", "OIDC_JWKS_URL": "http://idp:8080/jwks"}


def test_complete_production_configuration_is_accepted(monkeypatch):
    for name, value in PRODUCTION.items():
        monkeypatch.setenv(name, value)
    settings = Settings.from_env()
    assert settings.environment == "production" and settings.oidc_algorithms == ("RS256", "ES256")
    assert "postgresql" not in repr(settings)


@pytest.mark.parametrize("change,message", [
    ({"AUTH_MODE": "disabled"}, "cannot be disabled"),
    ({"DATABASE_URL": "sqlite+aiosqlite:///x.db"}, "PostgreSQL"),
    ({"LLM_PROVIDER": "mock"}, "mock"),
    ({"OIDC_ISSUER": "http://idp.example"}, "HTTPS"),
    ({"OIDC_JWKS_URL": ""}, None),
    ({"OIDC_ALGORITHMS": "HS256"}, "asymmetric"),
    ({"OIDC_ALGORITHMS": "none"}, "asymmetric"),
    ({"SIGNUP_MODE": "open"}, "SIGNUP_MODE"),
    ({"DEFAULT_MONTHLY_BUDGET_USD": "-1"}, "negative"),
    ({"DEFAULT_MONTHLY_BUDGET_USD": "lots"}, "decimal"),
    ({"WORKER_CONCURRENCY": "0"}, "positive"),
    ({"ENVIRONMENT": "staging"}, "ENVIRONMENT"),
])
def test_unsafe_production_configuration_fails_closed(monkeypatch, change, message):
    for name, value in {**PRODUCTION, **change}.items():
        monkeypatch.setenv(name, value)
    if change == {"OIDC_JWKS_URL": ""}:
        # An empty value means "unset"; the default is empty too, so OIDC is incomplete.
        message = "OIDC_ISSUER"
    with pytest.raises(ValueError, match=message):
        Settings.from_env()


def test_development_requires_a_database_but_may_disable_authentication(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("AUTH_MODE", "disabled")
    with pytest.raises(ValueError, match="DATABASE_URL"):
        Settings.from_env()
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@db/t")
    assert Settings.from_env().auth_mode == "disabled"


def test_role_models_inherit_configured_default(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "some-model")
    monkeypatch.setenv("LLM_MODEL_FORGE", "forge-model")
    settings = Settings.from_env()
    assert settings.llm_model_planner == "some-model"
    assert settings.llm_model_forge == "forge-model"
    assert settings.llm_model_summary == "some-model"


def test_relay_models_and_execution_limits_have_required_defaults():
    settings = Settings()
    assert settings.llm_base_url == "https://piquant-peacoat--llm-relay.apify.actor/v1"
    assert settings.llm_model == "anthropic/claude-sonnet-5.5"
    assert all(getattr(settings, f"llm_model_{role}") == "anthropic/claude-sonnet-5.5"
               for role in ("planner", "forge", "rule", "summary"))
    assert settings.llm_model_examiner == "deepseek/deepseek-v4.1-flash"
    assert settings.llm_model_fallback == "deepseek/deepseek-v4.1-flash"
    assert settings.llm_reasoning_effort == ""
    assert settings.llm_max_tokens == 16000 and settings.llm_max_tokens_cap == 32000
    assert settings.llm_timeout_s == 180 and settings.run_timeout_s == 1500
    assert settings.llm_max_retries == 2 and settings.llm_max_calls_per_run == 25
    assert Settings.from_env() == replace(settings, environment="test", auth_mode="disabled")


def test_examiner_and_fallback_defaults_do_not_inherit_a_main_model_override(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "custom-main-model")
    settings = Settings.from_env()
    assert settings.llm_model_planner == "custom-main-model"
    assert settings.llm_model_examiner == Settings().llm_model_examiner
    assert settings.llm_model_fallback == Settings().llm_model_fallback


def test_every_role_and_fallback_can_be_configured_independently(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "main-model")
    for role in ("planner", "forge", "rule", "summary", "examiner", "fallback"):
        monkeypatch.setenv(f"LLM_MODEL_{role.upper()}", f"custom-{role}")
    settings = Settings.from_env()
    assert settings.llm_model == "main-model"
    for role in ("planner", "forge", "rule", "summary", "examiner", "fallback"):
        assert getattr(settings, f"llm_model_{role}") == f"custom-{role}"


def test_token_cap_and_timeouts_are_environment_configurable(monkeypatch):
    monkeypatch.setenv("LLM_MAX_TOKENS", "12000")
    monkeypatch.setenv("LLM_MAX_TOKENS_CAP", "24000")
    monkeypatch.setenv("LLM_TIMEOUT_S", "210")
    monkeypatch.setenv("RUN_TIMEOUT_S", "1800")
    settings = Settings.from_env()
    assert settings.llm_max_tokens == 12000 and settings.llm_max_tokens_cap == 24000
    assert settings.llm_timeout_s == 210 and settings.run_timeout_s == 1800


@pytest.mark.parametrize("effort", ["", "minimal", "low", "medium", "high", "xhigh", "max"])
def test_reasoning_effort_accepts_only_supported_opt_in_values(monkeypatch, effort):
    monkeypatch.setenv("LLM_REASONING_EFFORT", effort)
    assert Settings.from_env().llm_reasoning_effort == effort


@pytest.mark.parametrize("effort", ["none", "false", "bogus"])
def test_reasoning_effort_rejects_disabled_or_unknown_values(monkeypatch, effort):
    monkeypatch.setenv("LLM_REASONING_EFFORT", effort)
    with pytest.raises(ValueError):
        Settings.from_env()


def test_initial_token_budget_equal_to_cap_is_valid(monkeypatch):
    monkeypatch.setenv("LLM_MAX_TOKENS", "8000")
    monkeypatch.setenv("LLM_MAX_TOKENS_CAP", "8000")
    assert Settings.from_env().llm_max_tokens_cap == 8000


def test_initial_token_budget_above_cap_is_rejected(monkeypatch):
    monkeypatch.setenv("LLM_MAX_TOKENS", "8001")
    monkeypatch.setenv("LLM_MAX_TOKENS_CAP", "8000")
    with pytest.raises(ValueError):
        Settings.from_env()


@pytest.mark.parametrize("key,value", [("MOCK_SCENARIO", "AB"), ("LLM_PROVIDER", "bad"),
                                      ("EXAMINER_ENABLED", "perhaps"), ("RUN_TIMEOUT_S", "nan"),
                                      ("RUN_TIMEOUT_S", "inf"), ("RUN_TIMEOUT_S", "0"),
                                      ("LLM_MAX_RETRIES", "3"), ("LLM_MAX_CALLS_PER_RUN", "26"),
                                      ("LLM_MAX_TOKENS", "0"), ("MOCK_DELAY_MS", "-1"),
                                      ("LLM_MAX_TOKENS_CAP", "0"), ("LLM_MAX_TOKENS_CAP", "-1"),
                                      ("LLM_MAX_TOKENS_CAP", "1.5"), ("LLM_MAX_TOKENS_CAP", "nan"),
                                      ("LLM_TIMEOUT_S", "nan"), ("LLM_TIMEOUT_S", "inf"),
                                      ("LLM_TIMEOUT_S", "0"), ("LLM_TIMEOUT_S", "-1"),
                                      ("LLM_MAX_RETRIES", "-1"), ("LLM_MAX_CALLS_PER_RUN", "0")])
def test_invalid_settings_fail_closed(monkeypatch, key, value):
    monkeypatch.setenv(key, value)
    with pytest.raises(ValueError):
        Settings.from_env()


def test_settings_are_frozen_and_secrets_are_not_repr():
    settings = Settings(apify_token="apify-secret", llm_api_key="llm-secret", elevenlabs_api_key="voice-secret")
    assert "secret" not in repr(settings)
    with pytest.raises(FrozenInstanceError):
        settings.llm_provider = "mock"
