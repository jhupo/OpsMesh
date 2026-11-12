"""DingTalk robot channel settings."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from opsmesh_channels._validation import path_prefix


class DingTalkConnectionSettings(BaseModel):
    """One robot account supplied by an entry-owning plugin.

    The client secret is read from the named environment variable at startup
    and is never stored in the configuration model.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    client_id: str = Field(pattern=r"^[A-Za-z0-9._-]{1,128}$")
    client_secret_ref: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
    max_message_length: int = Field(default=20000, ge=1, le=100000)


class DingTalkSettings(BaseModel):
    """Callback limits shared by DingTalk connections."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    path_prefix: str = "/channels/dingtalk"
    request_timeout: float = Field(default=10.0, gt=0)
    shutdown_timeout: float = Field(default=5.0, gt=0)
    max_clock_skew: float = Field(default=300.0, gt=0)
    max_pending_events: int = Field(default=100, ge=1)
    concurrency: int = Field(default=8, ge=1)
    dedupe_ttl: float = Field(default=300.0, gt=0)
    dedupe_capacity: int = Field(default=1024, ge=1)

    @field_validator("path_prefix")
    @classmethod
    def _validate_path_prefix(cls, value: str) -> str:
        return path_prefix(value)


def get_settings() -> DingTalkSettings:
    """Validate this protocol only when a consumer declares a connection."""
    from ..config import protocol_settings

    values = protocol_settings("dingtalk")
    return DingTalkSettings.model_validate({} if values is None else values)
