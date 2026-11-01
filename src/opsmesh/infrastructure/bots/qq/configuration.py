"""Settings for official QQ OpenAPI, signed callbacks and Gateway connections."""

import ipaddress
import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator

from opsmesh.framework.typing import JsonData, OpsMeshContract


class QQBotSettings(OpsMeshContract):
    app_id: str = Field(pattern=r"^[0-9]+$", max_length=64)
    transport: Literal["webhook", "websocket"] = "webhook"
    api_base_url: str = "https://api.bot.qq.com"
    request_timeout_seconds: float = Field(default=30, gt=0, le=120)
    signature_max_age_seconds: int = Field(default=300, ge=30, le=3600)
    intents: int = Field(default=1 << 25, ge=1, le=(1 << 32) - 1)
    shard: tuple[int, int] = (0, 1)
    reconnect_seconds: float = Field(default=2, ge=0.05, le=60)
    handshake_timeout_seconds: float = Field(default=30, gt=0, le=120)
    callback_timeout_seconds: float = Field(default=30, gt=0, le=120)
    callback_queue_size: int = Field(default=100, ge=1, le=1000)
    group_member_openids: dict[str, str] = Field(default_factory=dict, max_length=1000)

    @field_validator("group_member_openids")
    @classmethod
    def group_identities(cls, value: dict[str, str]) -> dict[str, str]:
        if any(
            re.fullmatch(r"[A-Za-z0-9_-]{1,256}", identifier) is None
            for group_id, member_id in value.items()
            for identifier in (group_id, member_id)
        ):
            raise ValueError("QQ group identity mapping requires valid group and member OpenIDs")
        return value

    def event_self_id(self, payload: JsonData, fallback: str) -> str:
        """Group member OpenIDs and the Gateway user ID are distinct identities."""
        data = payload.get("d")
        kind = payload.get("t")
        if (
            isinstance(kind, str)
            and kind in {"GROUP_MESSAGE_CREATE", "GROUP_AT_MESSAGE_CREATE"}
            and isinstance(data, dict)
        ):
            group_id = data.get("group_openid")
            if isinstance(group_id, str):
                return self.group_member_openids.get(group_id, fallback)
        return fallback

    @field_validator("shard")
    @classmethod
    def shard_assignment(cls, value: tuple[int, int]) -> tuple[int, int]:
        if not 0 <= value[0] < value[1] <= 1000:
            raise ValueError("QQ shard requires an index within a positive shard count")
        return value

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


def trusted_gateway(endpoint: str, settings: QQBotSettings) -> str:
    """Do not forward a QQ access token to a gateway outside the official service."""
    parsed = urlsplit(endpoint)
    host = (parsed.hostname or "").lower()
    api_host = urlsplit(settings.api_base_url).hostname
    try:
        local_api = ipaddress.ip_address(api_host or "").is_loopback
    except ValueError:
        local_api = api_host == "localhost"
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = host == "localhost"
    official = host in {"api.bot.qq.com", "api.sgroup.qq.com", "sandbox.api.sgroup.qq.com"}
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("QQ returned an invalid gateway port") from error
    if (
        len(endpoint) > 2048
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or not (
            (official and parsed.scheme == "wss" and port in {None, 443})
            or (local_api and loopback and parsed.scheme in {"ws", "wss"})
        )
    ):
        raise ValueError("QQ requires a trusted secure gateway or explicit loopback peer")
    return endpoint
