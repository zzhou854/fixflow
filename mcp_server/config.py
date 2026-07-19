"""Environment-only configuration for the independent MCP process."""

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class MCPSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    host: str = Field(default="127.0.0.1", validation_alias="MCP_HOST")
    port: int = Field(default=8765, ge=1, le=65535, validation_alias="MCP_PORT")
    database_url: SecretStr = Field(
        validation_alias=AliasChoices("DATABASE_URL", "FIXFLOW_DATABASE_URL")
    )
