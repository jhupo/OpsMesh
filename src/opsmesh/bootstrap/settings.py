from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from opsmesh.shared.schemas import ReasoningEffort


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="OPSMESH_", env_file=".env", env_ignore_empty=True, extra="ignore"
    )
    database_url: SecretStr
    encryption_key: SecretStr
    data_dir: Path = Path("data")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_dir: Path | None = None
    log_console: bool = True
    log_file: bool = True
    log_max_bytes: int = Field(default=10_000_000, ge=1024)
    log_backup_count: int = Field(default=5, ge=0)
    host: str = "0.0.0.0"
    port: int = Field(default=8000, ge=1, le=65535)
    admin_username: str | None = Field(default=None, min_length=1, max_length=100)
    admin_password: SecretStr | None = Field(default=None, min_length=12)
    model_base_url: str = "https://api.openai.com/v1"
    model_api_key: SecretStr | None = None
    model_name: str | None = None
    model_api_mode: Literal["responses", "chat_completions"] = "responses"
    model_reasoning_effort: ReasoningEffort = "medium"
    session_ttl_seconds: int = Field(default=31_536_000, ge=1)
    sandbox_root: str = "/workspace"
    worker_concurrency: int | None = Field(default=None, ge=1, le=64)
    lease_seconds: int = Field(default=90, ge=30)
    package_limit_bytes: int = Field(default=20_000_000, ge=1)
    file_limit_bytes: int = Field(default=20_000_000, ge=1)
    allowed_model_hosts: list[str] = Field(default_factory=lambda: ["api.openai.com"])
