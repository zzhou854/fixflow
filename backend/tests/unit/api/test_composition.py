import pytest
from app.api.composition import _validate_security_settings
from app.config import Settings


def _settings(**updates: object) -> Settings:
    values: dict[str, object] = {
        "database_url": "postgresql+asyncpg://user:password@localhost/fixflow",
        "jwt_secret": "unit-security-secret-with-more-than-thirty-two-bytes",
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
