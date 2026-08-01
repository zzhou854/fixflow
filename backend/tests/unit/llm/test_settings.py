from pathlib import Path

import pytest
from app.config import Settings
from pydantic import ValidationError

BASE = {"database_url": "postgresql+asyncpg://u:p@localhost/db"}


@pytest.fixture(autouse=True)
def _isolate_dotenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)


def test_scripted_provider_does_not_require_key() -> None:
    settings = Settings.model_validate({**BASE, "llm_provider": "scripted"})
    assert settings.llm_experimental_enabled is False
    assert settings.glm_api_key is None
    assert settings.deepseek_api_key is None
    assert settings.llm_online_enabled is False
    assert settings.llm_online_qualification_status == "NOT_ACTIVATED"
    assert settings.llm_model_budget_seconds == 25


def test_controlled_shadow_requires_switch_key_and_explicit_qualification() -> None:
    with pytest.raises(ValidationError, match="LLM_ONLINE_ENABLED"):
        Settings.model_validate({**BASE, "llm_shadow_enabled": True})
    settings = Settings.model_validate(
        {
            **BASE,
            "llm_online_enabled": True,
            "llm_shadow_enabled": True,
            "llm_online_qualification_status": "CONTRACT_PASSED",
            "deepseek_api_key": "synthetic-shadow-secret",
        }
    )
    assert settings.llm_provider == "scripted"
    assert settings.llm_online_runtime_mode == "demo_safe"


def test_development_canary_requires_trusted_uuid_allowlist_and_keeps_default_scripted() -> None:
    user_id = "233b64aa-0c53-5388-85e5-87b4ede2c8eb"
    settings = Settings.model_validate(
        {
            **BASE,
            "environment": "development",
            "llm_online_enabled": True,
            "llm_online_runtime_mode": "development",
            "online_canary_enabled": True,
            "online_canary_user_ids": user_id,
            "online_structured_understanding_enabled": True,
            "online_grounded_response_enabled": True,
            "deepseek_api_key": "synthetic-canary-secret",
        }
    )
    assert settings.llm_provider == "scripted"
    assert {str(item) for item in settings.online_canary_user_id_set} == {user_id}


def test_development_canary_rejects_missing_allowlist_and_non_development() -> None:
    base = {
        **BASE,
        "llm_online_enabled": True,
        "online_canary_enabled": True,
        "deepseek_api_key": "synthetic-canary-secret",
    }
    with pytest.raises(ValidationError, match="ONLINE_CANARY_USER_IDS"):
        Settings.model_validate(base)
    with pytest.raises(ValidationError, match="restricted to development"):
        Settings.model_validate(
            {
                **base,
                "environment": "test",
                "online_canary_user_ids": "233b64aa-0c53-5388-85e5-87b4ede2c8eb",
            }
        )


def test_model_budget_cannot_exceed_product_deadline() -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate({**BASE, "llm_model_budget_seconds": 25.01})


def test_legacy_experimental_shadow_is_disabled() -> None:
    with pytest.raises(ValidationError, match="legacy"):
        Settings.model_validate({**BASE, "llm_experimental_enabled": True})


def test_legacy_shadow_cannot_bypass_controlled_gate() -> None:
    with pytest.raises(ValidationError, match="legacy"):
        Settings.model_validate(
            {
                **BASE,
                "llm_provider": "deepseek",
                "llm_experimental_enabled": True,
                "deepseek_api_key": "synthetic-shadow-secret",
            }
        )


def test_glm_provider_requires_secret_without_leaking_it() -> None:
    with pytest.raises(ValidationError, match="GLM_API_KEY"):
        Settings.model_validate({**BASE, "llm_provider": "glm"})
    secret = "very-secret-value-that-must-not-appear"
    settings = Settings.model_validate({**BASE, "llm_provider": "glm", "glm_api_key": secret})
    assert settings.glm_api_key is not None
    assert secret not in repr(settings)


def test_deepseek_provider_requires_non_placeholder_secret() -> None:
    with pytest.raises(ValidationError, match="DEEPSEEK_API_KEY"):
        Settings.model_validate({**BASE, "llm_provider": "deepseek"})
    with pytest.raises(ValidationError, match="DEEPSEEK_API_KEY"):
        Settings.model_validate(
            {
                **BASE,
                "llm_provider": "deepseek",
                "deepseek_api_key": "replace-with-your-deepseek-api-key",
            }
        )
    secret = "synthetic-deepseek-secret-that-must-not-appear"
    settings = Settings.model_validate(
        {**BASE, "llm_provider": "deepseek", "deepseek_api_key": secret}
    )
    assert settings.deepseek_model == "deepseek-v4-flash"
    assert secret not in repr(settings)


def test_deepseek_pro_is_an_allowed_explicit_model() -> None:
    settings = Settings.model_validate(
        {
            **BASE,
            "llm_provider": "deepseek",
            "deepseek_model": "deepseek-v4-pro",
            "deepseek_api_key": "synthetic-deepseek-secret-that-must-not-appear",
        }
    )
    assert settings.deepseek_model == "deepseek-v4-pro"


@pytest.mark.parametrize(
    "overrides",
    [
        {"llm_provider": "other"},
        {"glm_thinking_mode": "maybe"},
        {"glm_request_timeout_seconds": 20, "glm_total_timeout_seconds": 10},
        {"glm_max_attempts": 6},
        {"deepseek_model": "deepseek-chat"},
        {"deepseek_thinking_mode": "enabled"},
        {
            "deepseek_request_timeout_seconds": 20,
            "deepseek_total_timeout_seconds": 10,
        },
        {"llm_max_response_bytes": 0},
    ],
)
def test_invalid_llm_settings_fail_at_startup(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate({**BASE, **overrides})
