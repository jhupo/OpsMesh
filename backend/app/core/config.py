from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LogFormat = Literal["json", "text"]


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
    storage_root: str = Field(default=".chaincloud-storage")
    max_upload_bytes: int = Field(default=10 * 1024 * 1024)

    @model_validator(mode="after")
    def validate_production_secrets(self) -> "Settings":
        if self.environment.lower() in {"production", "prod"}:
            if self.internal_api_token == "change-me-in-production":
                raise ValueError("CHAINCLOUD_INTERNAL_API_TOKEN must be set in production")
            if self.enable_api_docs:
                raise ValueError("CHAINCLOUD_ENABLE_API_DOCS must be false in production")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
