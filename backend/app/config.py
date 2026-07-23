"""Application configuration loaded from environment variables."""

from functools import lru_cache

from pydantic import Field, SecretStr, model_validator
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
    reconciliation_poll_interval_seconds: float = Field(default=2.0, gt=0)
    reconciliation_batch_size: int = Field(default=20, ge=1, le=1000)
    reconciliation_lease_seconds: int = Field(default=30, ge=1, le=3600)
    reconciliation_max_attempts: int = Field(default=5, ge=1, le=100)
    reconciliation_retry_base_seconds: float = Field(default=2.0, gt=0)
    trace_max_payload_bytes: int = Field(default=8192, ge=128, le=1_048_576)
    trace_max_string_length: int = Field(default=1024, ge=16, le=65536)
    runtime_revision: str = Field(default="development", min_length=1, max_length=128)
    replay_bundle_schema_version: int = Field(default=1, ge=1, le=100)
    graph_schema_version: int = Field(default=1, ge=1, le=100)
    replay_max_steps: int = Field(default=500, ge=10, le=5000)
    replay_max_payload_bytes: int = Field(default=262_144, ge=4096, le=4_194_304)
    replay_execution_timeout_seconds: float = Field(default=15.0, gt=0, le=300)
    llm_provider: str = "scripted"
    glm_model: str = "glm-5.1"
    glm_base_url: str = "https://open.bigmodel.cn/api/paas/v4/"
    glm_api_key: SecretStr | None = None
    glm_thinking_mode: str = "disabled"
    glm_temperature: float = Field(default=0.1, gt=0, le=1)
    glm_top_p: float = Field(default=0.8, gt=0, le=1)
    glm_max_tokens: int = Field(default=1600, gt=0, le=8000)
    glm_request_timeout_seconds: float = Field(default=20.0, gt=0, le=120)
    glm_total_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    glm_max_attempts: int = Field(default=3, ge=1, le=5)
    glm_retry_initial_delay_seconds: float = Field(default=0.5, ge=0, le=30)
    glm_retry_max_delay_seconds: float = Field(default=4.0, ge=0, le=60)
    glm_max_concurrency: int = Field(default=8, ge=1, le=100)
    llm_max_input_characters: int = Field(default=6000, gt=0, le=100_000)
    llm_max_context_messages: int = Field(default=6, gt=0, le=20)
    llm_max_message_characters: int = Field(default=2000, gt=0, le=20_000)
    llm_max_response_bytes: int = Field(default=65_536, gt=0, le=1_048_576)

    @model_validator(mode="after")
    def validate_llm_settings(self) -> "Settings":
        if self.llm_provider not in {"scripted", "glm"}:
            raise ValueError("LLM_PROVIDER must be one of: scripted, glm")
        if self.glm_thinking_mode not in {"disabled", "enabled"}:
            raise ValueError("GLM_THINKING_MODE must be disabled or enabled")
        if self.glm_total_timeout_seconds < self.glm_request_timeout_seconds:
            raise ValueError("GLM_TOTAL_TIMEOUT_SECONDS must be at least request timeout")
        if self.glm_retry_max_delay_seconds < self.glm_retry_initial_delay_seconds:
            raise ValueError("GLM_RETRY_MAX_DELAY_SECONDS must be at least initial delay")
        if self.llm_provider == "glm" and (
            self.glm_api_key is None or not self.glm_api_key.get_secret_value().strip()
        ):
            raise ValueError("GLM_API_KEY is required when LLM_PROVIDER=glm")
        return self


@lru_cache
def get_settings() -> Settings:
    """Return one immutable-by-convention settings instance per process."""

    return Settings()
