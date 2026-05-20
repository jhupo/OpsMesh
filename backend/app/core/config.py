from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from backend.app.core.resources import recommend_runtime_resources

LogFormat = Literal["json", "text"]
AgentRunnerBackend = Literal["fake", "openai"]
_RESOURCE_RECOMMENDATION = recommend_runtime_resources()


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
    database_pool_size: int = Field(default=_RESOURCE_RECOMMENDATION.database_pool_size, ge=1)
    database_max_overflow: int = Field(default=_RESOURCE_RECOMMENDATION.database_max_overflow, ge=0)
    database_pool_timeout_seconds: int = Field(default=30, ge=1)
    database_pool_recycle_seconds: int = Field(default=1_800, ge=30)
    database_statement_timeout_ms: int = Field(default=30_000, ge=1_000)
    redis_url: str = Field(default="redis://localhost:6379/0")
    redis_max_connections: int = Field(
        default=_RESOURCE_RECOMMENDATION.redis_max_connections,
        ge=1,
    )
    redis_socket_timeout_seconds: float = Field(default=5.0, gt=0)
    redis_socket_connect_timeout_seconds: float = Field(default=5.0, gt=0)
    redis_health_check_interval_seconds: int = Field(default=30, ge=0)
    redis_key_prefix: str = Field(default="chaincloud")
    worker_queue_name: str = Field(default="agent_runs")
    blocking_thread_pool_workers: int = Field(
        default=_RESOURCE_RECOMMENDATION.blocking_thread_pool_workers,
        ge=1,
    )
    request_slow_log_threshold_ms: int = Field(default=1_000, ge=1)
    internal_api_token: str = Field(default="change-me-in-production")
    platform_admin_token: str | None = Field(default=None)
    token_hash_pepper: str = Field(default="change-me-token-pepper")
    storage_root: str = Field(default=".chaincloud-storage")
    max_upload_bytes: int = Field(default=10 * 1024 * 1024)
    agent_runner_backend: AgentRunnerBackend = Field(default="fake")
    api_rate_limit_enabled: bool = Field(default=False)
    api_rate_limit_requests: int = Field(default=600, ge=1)
    api_rate_limit_window_seconds: int = Field(default=60, ge=1)
    credential_encryption_secret: str = Field(default="change-me-credential-encryption-secret")
    credential_encryption_key_id: str = Field(default="local")
    runtime_allowed_images: list[str] = Field(default_factory=lambda: ["python:3.12-slim"])

    @model_validator(mode="after")
    def validate_production_secrets(self) -> "Settings":
        if self.environment.lower() in {"production", "prod"}:
            if self.internal_api_token == "change-me-in-production":
                raise ValueError("CHAINCLOUD_INTERNAL_API_TOKEN must be set in production")
            if not self.platform_admin_token:
                raise ValueError("CHAINCLOUD_PLATFORM_ADMIN_TOKEN must be set in production")
            if self.token_hash_pepper == "change-me-token-pepper":
                raise ValueError("CHAINCLOUD_TOKEN_HASH_PEPPER must be set in production")
            if self.enable_api_docs:
                raise ValueError("CHAINCLOUD_ENABLE_API_DOCS must be false in production")
            if self.credential_encryption_secret == "change-me-credential-encryption-secret":
                raise ValueError(
                    "CHAINCLOUD_CREDENTIAL_ENCRYPTION_SECRET must be set in production"
                )
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
