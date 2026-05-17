from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LogFormat = Literal["json", "text"]
AgentRunnerBackend = Literal["fake", "openai"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="CHAINCLOUD_",
        extra="ignore",
    )

    environment: str = Field(default="local")
    service_name: str = Field(default="chaincloud-backend")
    api_prefix: str = Field(default="/api/v1")
    log_level: str = Field(default="INFO")
    log_format: LogFormat = Field(default="json")
    enable_api_docs: bool = Field(default=True)
    cors_origins: list[str] = Field(default_factory=list)
    database_url: str = Field(
        default="postgresql+psycopg://chaincloud:chaincloud@localhost:5432/chaincloud"
    )
    redis_url: str = Field(default="redis://localhost:6379/0")
    redis_key_prefix: str = Field(default="chaincloud")
    worker_queue_name: str = Field(default="agent_runs")
    internal_api_token: str = Field(default="change-me-in-production")
    token_hash_pepper: str = Field(default="change-me-token-pepper")
    storage_root: str = Field(default=".chaincloud-storage")
    max_upload_bytes: int = Field(default=10 * 1024 * 1024)
    agent_runner_backend: AgentRunnerBackend = Field(default="fake")
    api_rate_limit_enabled: bool = Field(default=False)
    api_rate_limit_requests: int = Field(default=600, ge=1)
    api_rate_limit_window_seconds: int = Field(default=60, ge=1)

    @model_validator(mode="after")
    def validate_production_secrets(self) -> "Settings":
        if self.environment.lower() in {"production", "prod"}:
            if self.internal_api_token == "change-me-in-production":
                raise ValueError("CHAINCLOUD_INTERNAL_API_TOKEN must be set in production")
            if self.token_hash_pepper == "change-me-token-pepper":
                raise ValueError("CHAINCLOUD_TOKEN_HASH_PEPPER must be set in production")
            if self.enable_api_docs:
                raise ValueError("CHAINCLOUD_ENABLE_API_DOCS must be false in production")
        return self

    @property
    def internal_api_tokens(self) -> tuple[str, ...]:
        return tuple(
            token.strip()
            for token in self.internal_api_token.split(",")
            if token.strip()
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
