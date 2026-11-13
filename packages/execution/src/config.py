"""Execution plugin settings owned by the plugin, not the host."""

from __future__ import annotations

from typing import Literal
from urllib.parse import urlparse

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


class LangfuseSettings(BaseModel):
    """Optional Langfuse bridge configuration."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    base_url: str = Field(min_length=1, max_length=2048)
    public_key_ref: str = Field(min_length=1, max_length=200)
    secret_key_ref: str = Field(min_length=1, max_length=200)
    environment: str | None = Field(default=None, max_length=200)
    sample_rate: float = Field(default=1.0, ge=0.0, le=1.0)
    export_timeout_seconds: float = Field(default=5.0, gt=0.0, le=300.0)
    shutdown_timeout_seconds: float = Field(default=10.0, gt=0.0, le=300.0)

    @field_validator("base_url")
    @classmethod
    def _validate_base_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("base_url must be an absolute http(s) URL")
        return value

    @field_validator("public_key_ref", "secret_key_ref")
    @classmethod
    def _validate_secret_ref(cls, value: str) -> str:
        if not value.replace("_", "").isalnum():
            raise ValueError(
                "secret references must be environment variable names"
            )
        return value


class TracingSettings(BaseModel):
    """Single tracing destination selected for this host process."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    destination: Literal["disabled", "local", "langfuse"] = "disabled"
    include_sensitive_data: bool = False
    langfuse: LangfuseSettings | None = None

    @model_validator(mode="after")
    def _require_langfuse_settings(self) -> TracingSettings:
        if self.destination == "langfuse" and self.langfuse is None:
            raise ValueError(
                "langfuse tracing requires tracing.langfuse settings"
            )
        return self


class Settings(BaseModel):
    """Lifecycle limits for execution observation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tracing: TracingSettings = Field(default_factory=TracingSettings)
    cleanup_timeout_seconds: float = Field(default=10.0, gt=0.0, le=300.0)
