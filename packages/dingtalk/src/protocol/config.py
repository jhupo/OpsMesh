"""DingTalk robot connection settings."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..attachments import AttachmentSettings


class DingTalkCardSettings(BaseModel):
    """A published AI card template with a streaming Markdown component."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    template_id: str = Field(min_length=1, max_length=200)
    content_key: str = Field(
        default="msgContent", pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,99}$"
    )
    update_interval: float = Field(default=0.8, ge=0.5, le=5)

    @field_validator("content_key")
    @classmethod
    def separate_content_from_status(cls, value: str) -> str:
        """Keep streaming text from overwriting the SDK lifecycle field."""
        if value == "flowStatus":
            raise ValueError("flowStatus is reserved for the AI card lifecycle")
        return value


class DingTalkConnectionSettings(BaseModel):
    """One robot account declared by a consumer plugin.

    The client secret is read from the named environment variable when the
    connection is prepared and is never stored in the configuration model.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = True
    client_id: str = Field(pattern=r"^[A-Za-z0-9._-]{1,128}$")
    client_secret_ref: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
    max_message_length: int = Field(default=20000, ge=1, le=100000)
    card: DingTalkCardSettings | None = None
    attachments: AttachmentSettings = Field(default_factory=AttachmentSettings)
    request_timeout: float = Field(default=10.0, gt=0)
    shutdown_timeout: float = Field(default=5.0, gt=0)
    reconnect_delay: float = Field(default=3.0, gt=0)
    max_pending_events: int = Field(default=100, ge=1)
    concurrency: int = Field(default=8, ge=1)
    dedupe_ttl: float = Field(default=300.0, gt=0)
    dedupe_capacity: int = Field(default=1024, ge=1)
