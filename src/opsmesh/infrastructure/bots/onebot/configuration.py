"""Validated settings for the optional OneBot v11 compatibility adapter."""

from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, ValidationInfo, field_validator, model_validator

from opsmesh.framework.typing import OpsMeshContract


class OneBotSettings(OpsMeshContract):
    transport: Literal["http", "websocket", "reverse_websocket"] = "http"
    api_base_url: str | None = None
    websocket_url: str | None = None
    reconnect_seconds: float = Field(default=1, ge=0.05, le=60)
    file_upload: Literal["unsupported", "napcat"] = "unsupported"

    @field_validator("api_base_url", "websocket_url")
    @classmethod
    def validated_endpoint(cls, value: str | None, info: ValidationInfo) -> str | None:
        if value is None:
            return None
        schemes = {"http", "https"} if info.field_name == "api_base_url" else {"ws", "wss"}
        url = urlsplit(value)
        if (
            url.scheme not in schemes
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise ValueError("OneBot endpoint must use its transport scheme without credentials")
        return value.rstrip("/") if info.field_name == "api_base_url" else value

    @model_validator(mode="after")
    def transport_endpoint(self) -> "OneBotSettings":
        if self.transport == "http" and not self.api_base_url:
            raise ValueError("HTTP OneBot requires an API URL")
        if self.transport == "websocket" and not self.websocket_url:
            raise ValueError("Active OneBot WebSocket requires a URL")
        return self
