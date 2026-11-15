"""QQ official bot channel settings."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from opsmesh.plugins.channels._validation import http_url

from ..attachments import AttachmentSettings


class QQConnectionSettings(BaseModel):
    """One bot account supplied by an entry-owning plugin.

    The secret is read from the named environment variable at startup; it is
    never stored in the configuration model.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    app_id: str = Field(pattern=r"^[0-9]{1,32}$")
    secret_ref: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
    sandbox: bool = False
    intents: int = Field(default=1 << 25, ge=0, le=(1 << 64) - 1)
    max_message_length: int = Field(default=2000, ge=1, le=10000)


class QQSettings(BaseModel):
    """Protocol endpoints and limits shared by QQ connections."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = True
    attachments: AttachmentSettings = Field(default_factory=AttachmentSettings)
    api_base: str = "https://api.bot.qq.com"
    sandbox_api_base: str = "https://api.bot.qq.com"
    token_url: str = "https://api.bot.qq.com/app/getAppAccessToken"
    request_timeout: float = Field(default=10.0, gt=0)
    shutdown_timeout: float = Field(default=5.0, gt=0)
    reconnect_delay: float = Field(default=3.0, gt=0)
    max_pending_events: int = Field(default=100, ge=1)
    concurrency: int = Field(default=8, ge=1)
    dedupe_ttl: float = Field(default=300.0, gt=0)
    dedupe_capacity: int = Field(default=1024, ge=1)

    @field_validator("api_base", "sandbox_api_base", "token_url")
    @classmethod
    def _validate_url(cls, value: str) -> str:
        return http_url(value)


def get_settings() -> QQSettings:
    """Validate this protocol only when a consumer declares a connection."""
    from ..config import protocol_settings

    values = protocol_settings("qq")
    return QQSettings.model_validate({} if values is None else values)
