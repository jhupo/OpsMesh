from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

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
            if self.agent_runner_backend == "fake":
                raise ValueError("CHAINCLOUD_AGENT_RUNNER_BACKEND must not be fake in production")
            if "localhost" in self.database_url or "chaincloud:chaincloud" in self.database_url:
                raise ValueError("CHAINCLOUD_DATABASE_URL must not use local default credentials")
            if self.redis_url == "redis://localhost:6379/0":
                raise ValueError("CHAINCLOUD_REDIS_URL must not use the local default")
            if not self.cors_origins:
                raise ValueError("CHAINCLOUD_CORS_ORIGINS must be set in production")
            if self.storage_root == ".chaincloud-storage":
                raise ValueError("CHAINCLOUD_STORAGE_ROOT must be explicit in production")
        return self

    @property
    def internal_api_tokens(self) -> tuple[str, ...]:
        return tuple(
            token.strip()
            for token in self.internal_api_token.split(",")
            if token.strip()
        )

    def redacted_summary(self) -> dict[str, object]:
        return {
            "environment": self.environment,
            "service_name": self.service_name,
            "api_prefix": self.api_prefix,
            "log_level": self.log_level,
            "log_format": self.log_format,
            "enable_api_docs": self.enable_api_docs,
            "database_url": _redact_url(self.database_url),
            "database_pool_size": self.database_pool_size,
            "database_max_overflow": self.database_max_overflow,
            "redis_url": _redact_url(self.redis_url),
            "redis_max_connections": self.redis_max_connections,
            "worker_queue_name": self.worker_queue_name,
            "blocking_thread_pool_workers": self.blocking_thread_pool_workers,
            "agent_runner_backend": self.agent_runner_backend,
            "api_rate_limit_enabled": self.api_rate_limit_enabled,
            "storage_root": self.storage_root,
            "cors_origins_count": len(self.cors_origins),
        }


def _redact_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return "<invalid-url>"
    if not parsed.netloc:
        return value
    hostname = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port is not None else ""
    username = parsed.username
    redacted_auth = "***:***@" if username is not None else ""
    netloc = f"{redacted_auth}{hostname}{port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
