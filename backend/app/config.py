"""Application configuration loaded from environment variables."""

from functools import lru_cache
from uuid import UUID

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
    debug: bool = False
    cors_origins: str = "http://127.0.0.1:5173,http://localhost:5173"
    allowed_hosts: str = "127.0.0.1,localhost,testserver,test"
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
    llm_experimental_enabled: bool = False
    llm_experimental_provider: str = "deepseek"
    llm_online_enabled: bool = False
    llm_shadow_enabled: bool = False
    llm_grounded_response_enabled: bool = False
    online_canary_enabled: bool = False
    online_canary_user_ids: str = ""
    online_structured_understanding_enabled: bool = False
    online_grounded_response_enabled: bool = False
    enable_live_provider_tests: bool = False
    llm_online_runtime_mode: str = "demo_safe"
    llm_online_qualification_status: str = "NOT_ACTIVATED"
    llm_model_budget_seconds: float = Field(default=25.0, gt=0, le=25)
    llm_circuit_failure_threshold: int = Field(default=2, ge=1, le=20)
    llm_circuit_recovery_seconds: float = Field(default=30.0, gt=0, le=3600)
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
    deepseek_model: str = "deepseek-v4-flash"
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_api_key: SecretStr | None = None
    deepseek_thinking_mode: str = "disabled"
    deepseek_temperature: float = Field(default=0.0, ge=0, le=2)
    deepseek_top_p: float = Field(default=1.0, gt=0, le=1)
    deepseek_max_tokens: int = Field(default=1600, gt=0, le=8000)
    deepseek_request_timeout_seconds: float = Field(default=20.0, gt=0, le=120)
    deepseek_total_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    deepseek_max_attempts: int = Field(default=3, ge=1, le=5)
    deepseek_retry_initial_delay_seconds: float = Field(default=0.5, ge=0, le=30)
    deepseek_retry_max_delay_seconds: float = Field(default=4.0, ge=0, le=60)
    deepseek_max_concurrency: int = Field(default=8, ge=1, le=100)
    llm_max_input_characters: int = Field(default=6000, gt=0, le=100_000)
    llm_max_context_messages: int = Field(default=6, gt=0, le=20)
    llm_max_message_characters: int = Field(default=2000, gt=0, le=20_000)
    llm_max_response_bytes: int = Field(default=65_536, gt=0, le=1_048_576)

    @model_validator(mode="after")
    def validate_llm_settings(self) -> "Settings":
        if self.environment not in {"development", "test", "production"}:
            raise ValueError("ENVIRONMENT must be development, test, or production")
        if self.runtime_mode not in {"demo", "production"}:
            raise ValueError("RUNTIME_MODE must be demo or production")
        if self.llm_provider not in {"scripted", "glm", "deepseek"}:
            raise ValueError("LLM_PROVIDER must be one of: scripted, glm, deepseek")
        if self.llm_experimental_provider not in {"glm", "deepseek"}:
            raise ValueError("LLM_EXPERIMENTAL_PROVIDER must be glm or deepseek")
        if self.llm_experimental_enabled:
            raise ValueError(
                "legacy LLM_EXPERIMENTAL_ENABLED shadow is disabled; "
                "use the controlled online shadow gate"
            )
        if self.llm_online_runtime_mode not in {
            "development",
            "demo_safe",
            "production_candidate",
        }:
            raise ValueError(
                "LLM_ONLINE_RUNTIME_MODE must be development, demo_safe, or production_candidate"
            )
        if self.llm_online_qualification_status not in {
            "NOT_ACTIVATED",
            "CONTRACT_PASSED",
            "DEV_REGRESSION_PASSED",
            "HOLDOUT_PASSED",
            "SHADOW_PASSED",
            "CANARY_PASSED",
            "APPROVED",
        }:
            raise ValueError("invalid LLM_ONLINE_QUALIFICATION_STATUS")
        if self.llm_shadow_enabled and not self.llm_online_enabled:
            raise ValueError("LLM_SHADOW_ENABLED requires LLM_ONLINE_ENABLED=true")
        if self.llm_shadow_enabled and self.llm_experimental_enabled:
            raise ValueError("legacy and controlled shadow modes cannot both be enabled")
        if self.llm_grounded_response_enabled and not self.llm_online_enabled:
            raise ValueError("LLM_GROUNDED_RESPONSE_ENABLED requires LLM_ONLINE_ENABLED=true")
        if self.online_canary_enabled:
            if not self.llm_online_enabled:
                raise ValueError("ONLINE_CANARY_ENABLED requires LLM_ONLINE_ENABLED=true")
            if self.environment != "development":
                raise ValueError("ONLINE_CANARY_ENABLED is restricted to development")
            if self.llm_provider != "scripted":
                raise ValueError("the default LLM_PROVIDER must remain scripted")
            if not self.online_canary_user_id_set:
                raise ValueError("ONLINE_CANARY_USER_IDS must contain at least one UUID")
            if self.llm_shadow_enabled:
                raise ValueError("development Canary and Shadow cannot run together")
        if (
            self.online_structured_understanding_enabled or self.online_grounded_response_enabled
        ) and not self.online_canary_enabled:
            raise ValueError("online language capabilities require ONLINE_CANARY_ENABLED=true")
        if self.glm_thinking_mode not in {"disabled", "enabled"}:
            raise ValueError("GLM_THINKING_MODE must be disabled or enabled")
        if self.glm_total_timeout_seconds < self.glm_request_timeout_seconds:
            raise ValueError("GLM_TOTAL_TIMEOUT_SECONDS must be at least request timeout")
        if self.glm_retry_max_delay_seconds < self.glm_retry_initial_delay_seconds:
            raise ValueError("GLM_RETRY_MAX_DELAY_SECONDS must be at least initial delay")
        if self.deepseek_model not in {"deepseek-v4-flash", "deepseek-v4-pro"}:
            raise ValueError("DEEPSEEK_MODEL must be deepseek-v4-flash or deepseek-v4-pro")
        if self.deepseek_thinking_mode != "disabled":
            raise ValueError(
                "DEEPSEEK_THINKING_MODE must be disabled for structured interpretation"
            )
        if self.deepseek_total_timeout_seconds < self.deepseek_request_timeout_seconds:
            raise ValueError("DEEPSEEK_TOTAL_TIMEOUT_SECONDS must be at least request timeout")
        if self.deepseek_retry_max_delay_seconds < self.deepseek_retry_initial_delay_seconds:
            raise ValueError("DEEPSEEK_RETRY_MAX_DELAY_SECONDS must be at least initial delay")
        if self.llm_provider == "glm" and (
            self.glm_api_key is None or not self.glm_api_key.get_secret_value().strip()
        ):
            raise ValueError("GLM_API_KEY is required when LLM_PROVIDER=glm")
        if self.llm_provider == "deepseek" or (
            self.llm_online_enabled and self.llm_experimental_provider == "deepseek"
        ):
            key = (
                self.deepseek_api_key.get_secret_value().strip()
                if self.deepseek_api_key is not None
                else ""
            )
            if not key or key == "replace-with-your-deepseek-api-key":
                raise ValueError("DEEPSEEK_API_KEY is required when LLM_PROVIDER=deepseek")
        if self.environment == "production":
            if self.runtime_mode != "production":
                raise ValueError("production ENVIRONMENT requires RUNTIME_MODE=production")
            if self.debug:
                raise ValueError("DEBUG must be false in production")
            if self.llm_provider != "scripted":
                raise ValueError("production LLM_PROVIDER must remain scripted")
            if self.llm_experimental_enabled:
                raise ValueError("LLM_EXPERIMENTAL_ENABLED must be false in production")
            if self.llm_online_enabled or self.llm_shadow_enabled:
                raise ValueError("online provider remains disabled in production")
            database_url = self.database_url.get_secret_value()
            if "change-me" in database_url.casefold():
                raise ValueError("production DATABASE_URL cannot contain a placeholder")
            origins = [value.strip() for value in self.cors_origins.split(",") if value.strip()]
            hosts = [value.strip() for value in self.allowed_hosts.split(",") if value.strip()]
            if not origins or "*" in origins:
                raise ValueError("production CORS_ORIGINS must be explicit")
            if not hosts or "*" in hosts:
                raise ValueError("production ALLOWED_HOSTS must be explicit")
        return self

    @property
    def online_canary_user_id_set(self) -> frozenset[UUID]:
        """Parse the trusted development allowlist without exposing it to clients."""

        values = tuple(item.strip() for item in self.online_canary_user_ids.split(","))
        try:
            return frozenset(UUID(item) for item in values if item)
        except ValueError as exc:
            raise ValueError("ONLINE_CANARY_USER_IDS must contain comma-separated UUIDs") from exc


@lru_cache
def get_settings() -> Settings:
    """Return one immutable-by-convention settings instance per process."""

    return Settings()
