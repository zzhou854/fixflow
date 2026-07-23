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
    assert settings.glm_api_key is None


def test_glm_provider_requires_secret_without_leaking_it() -> None:
    with pytest.raises(ValidationError, match="GLM_API_KEY"):
        Settings.model_validate({**BASE, "llm_provider": "glm"})
    secret = "very-secret-value-that-must-not-appear"
    settings = Settings.model_validate({**BASE, "llm_provider": "glm", "glm_api_key": secret})
    assert settings.glm_api_key is not None
    assert secret not in repr(settings)


@pytest.mark.parametrize(
    "overrides",
    [
        {"llm_provider": "other"},
        {"glm_thinking_mode": "maybe"},
        {"glm_request_timeout_seconds": 20, "glm_total_timeout_seconds": 10},
        {"glm_max_attempts": 6},
        {"llm_max_response_bytes": 0},
    ],
)
def test_invalid_llm_settings_fail_at_startup(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate({**BASE, **overrides})
