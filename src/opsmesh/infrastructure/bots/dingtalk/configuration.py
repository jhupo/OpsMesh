"""Typed DingTalk application settings, independent of platform account storage."""

import ipaddress
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, ValidationError, field_validator

from opsmesh.framework.adapters import BotConnectionConfiguration
from opsmesh.framework.errors import OpsMeshError
from opsmesh.framework.typing import OpsMeshContract


def trusted_endpoint(value: str, *, websocket: bool = False) -> str:
    url = urlsplit(value)
    host = (url.hostname or "").lower()
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = host == "localhost"
    secure_scheme = "wss" if websocket else "https"
    local_scheme = "ws" if websocket else "http"
    trusted_host = (
        host == "api.dingtalk.com"
        if not websocket
        else (host == "dingtalk.com" or host.endswith(".dingtalk.com"))
    )
    if (
        not host
        or url.username
        or url.password
        or url.query
        or url.fragment
        or (url.scheme != secure_scheme and not (loopback and url.scheme == local_scheme))
        or (not trusted_host and not loopback)
        or (not loopback and url.port not in {None, 443})
    ):
        raise ValueError(
            "DingTalk endpoint must use its official secure host or an explicit loopback host"
        )
    return value.rstrip("/")


class DingTalkConfiguration(OpsMeshContract):
    transport: Literal["stream"] = "stream"
    client_id: str | None = Field(default=None, min_length=1, max_length=256)
    robot_code: str | None = Field(default=None, min_length=1, max_length=256)
    api_base_url: str = "https://api.dingtalk.com"
    reconnect_seconds: float = Field(default=1, ge=0.05, le=60)

    @field_validator("api_base_url")
    @classmethod
    def validate_api_endpoint(cls, value: str) -> str:
        return trusted_endpoint(value)


def parse_configuration(configuration: BotConnectionConfiguration) -> DingTalkConfiguration:
    if configuration.adapter != "dingtalk":
        raise OpsMeshError("CONFIG_INVALID", "DingTalk received a different adapter configuration")
    if set(configuration.credentials) != {"client_secret"}:
        raise OpsMeshError(
            "CONFIG_INVALID", "DingTalk requires only the client_secret credential reference"
        )
    try:
        return DingTalkConfiguration.model_validate(configuration.settings)
    except ValidationError as error:
        raise OpsMeshError("CONFIG_INVALID", "Invalid DingTalk connection settings") from error
