"""Settings for the official QQ OpenAPI and signed HTTP callbacks."""

import ipaddress
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator

from opsmesh.framework.typing import OpsMeshContract


class QQBotSettings(OpsMeshContract):
    app_id: str = Field(pattern=r"^[0-9]+$", max_length=64)
    transport: Literal["webhook"] = "webhook"
    api_base_url: str = "https://api.bot.qq.com"
    request_timeout_seconds: float = Field(default=30, gt=0, le=120)
    signature_max_age_seconds: int = Field(default=300, ge=30, le=3600)

    @field_validator("api_base_url")
    @classmethod
    def api_endpoint(cls, value: str) -> str:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower()
        try:
            loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = host == "localhost"
        official = host in {"api.bot.qq.com", "sandbox.api.sgroup.qq.com"}
        if (
            (parsed.scheme != "https" and not (loopback and parsed.scheme == "http"))
            or not parsed.hostname
            or (not official and not loopback)
            or (not loopback and parsed.port not in {None, 443})
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("QQ API requires an official secure host or explicit loopback host")
        return value.rstrip("/")

    @property
    def token_endpoint(self) -> str:
        if urlsplit(self.api_base_url).hostname in {"api.bot.qq.com", "sandbox.api.sgroup.qq.com"}:
            return "https://api.bot.qq.com/app/getAppAccessToken"
        return f"{self.api_base_url}/app/getAppAccessToken"
