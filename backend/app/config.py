"""Application configuration loaded from environment variables."""

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings shared by the API and migration environment."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="FIXFLOW_",
        extra="ignore",
    )

    environment: str = "development"
    database_url: SecretStr
    checkpoint_database_url: SecretStr | None = Field(
        default=None, validation_alias="CHECKPOINT_DATABASE_URL"
    )
    property_operations_mcp_url: str = "http://127.0.0.1:8765/mcp"
    jwt_secret: SecretStr | None = None
    jwt_algorithm: str = "HS256"
    jwt_access_token_minutes: int = 30
    runtime_mode: str = "demo"
    cors_origins: str = "http://127.0.0.1:5173,http://localhost:5173"


@lru_cache
def get_settings() -> Settings:
    """Return one immutable-by-convention settings instance per process."""

    return Settings()
