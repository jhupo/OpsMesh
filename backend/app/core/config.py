from functools import lru_cache
from typing import Literal

from pydantic import Field
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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
