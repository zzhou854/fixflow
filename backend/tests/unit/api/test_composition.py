import pytest
from app.api.composition import _validate_security_settings
from app.config import Settings


def _settings(**updates: object) -> Settings:
    values: dict[str, object] = {
        "database_url": "postgresql+asyncpg://user:password@localhost/fixflow",
        "jwt_secret": "unit-security-secret-with-more-than-thirty-two-bytes",
        "llm_online_enabled": False,
        "online_canary_enabled": False,
        "online_structured_understanding_enabled": False,
        "online_grounded_response_enabled": False,
    }
    values.update(updates)
    return Settings.model_validate(values)


@pytest.mark.parametrize(
    "secret",
    [None, "", "short", "replace-with-a-random-secret-of-at-least-32-characters"],
)
def test_runtime_rejects_missing_short_or_placeholder_jwt_secret(secret: str | None) -> None:
    with pytest.raises(ValueError, match="JWT_SECRET"):
        _validate_security_settings(_settings(jwt_secret=secret))


def test_runtime_rejects_non_hs256_and_wildcard_cors() -> None:
    with pytest.raises(ValueError, match="HS256"):
        _validate_security_settings(_settings(jwt_algorithm="HS512"))
    with pytest.raises(ValueError, match="explicit"):
        _validate_security_settings(_settings(cors_origins="*"))


def test_runtime_accepts_explicit_demo_security_configuration() -> None:
    _validate_security_settings(_settings())


def test_product_runtime_rejects_online_provider_as_primary() -> None:
    with pytest.raises(ValueError, match="must remain scripted"):
        _validate_security_settings(
            _settings(
                llm_provider="deepseek",
                deepseek_api_key="synthetic-test-key",
            )
        )


def test_production_runtime_rejects_unsafe_defaults() -> None:
    with pytest.raises(ValueError, match="RUNTIME_MODE"):
        _settings(environment="production")
    with pytest.raises(ValueError, match="DEBUG"):
        _settings(environment="production", runtime_mode="production", debug=True)
    with pytest.raises(ValueError, match="placeholder"):
        _settings(
            environment="production",
            runtime_mode="production",
            database_url="postgresql+asyncpg://fixflow:change-me@postgres/fixflow",
        )
    with pytest.raises(ValueError, match="ALLOWED_HOSTS"):
        _settings(
            environment="production",
            runtime_mode="production",
            allowed_hosts="*",
        )


def test_production_runtime_accepts_scripted_locked_configuration() -> None:
    settings = _settings(
        environment="production",
        runtime_mode="production",
        database_url="postgresql+asyncpg://fixflow:strong-password@postgres/fixflow",
        debug=False,
        llm_provider="scripted",
        llm_experimental_enabled=False,
        cors_origins="https://fixflow.example",
        allowed_hosts="fixflow.example",
    )
    _validate_security_settings(settings)
