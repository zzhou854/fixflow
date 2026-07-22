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
    outbox_poll_interval_seconds: float = Field(default=1.0, gt=0)
    outbox_batch_size: int = Field(default=50, ge=1, le=1000)
    outbox_lease_seconds: int = Field(default=30, ge=1, le=3600)
    outbox_max_attempts: int = Field(default=8, ge=1, le=100)
    outbox_retry_base_seconds: float = Field(default=1.0, gt=0)
    trace_max_payload_bytes: int = Field(default=8192, ge=128, le=1_048_576)
    trace_max_string_length: int = Field(default=1024, ge=16, le=65536)


@lru_cache
def get_settings() -> Settings:
    """Return one immutable-by-convention settings instance per process."""

    return Settings()
