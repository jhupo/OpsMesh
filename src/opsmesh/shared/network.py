"""Explicit HTTP routing without mutating process-wide proxy variables."""

import re
from collections.abc import Awaitable, Callable
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

CredentialResolver = Callable[[str], Awaitable[SecretStr]]
CREDENTIAL_REFERENCE = r"(?:env:[A-Z_][A-Z0-9_]*|secret:[A-Za-z0-9_.-]{1,190})"


class NetworkConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    mode: Literal["direct", "proxy", "environment"] = "direct"
    proxy_ref: str | None = Field(default=None, pattern="^" + CREDENTIAL_REFERENCE + "$")

    @model_validator(mode="after")
    def validate_proxy(self) -> "NetworkConfig":
        if (self.mode == "proxy") != (self.proxy_ref is not None):
            raise ValueError("Only proxy mode requires a proxy credential reference")
        return self


def validate_credential_reference(reference: str) -> None:
    if not re.fullmatch(CREDENTIAL_REFERENCE, reference):
        raise ValueError("Credentials require an env:VARIABLE or secret:identifier reference")


def validate_proxy_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        valid = (
            parsed.scheme in {"http", "https"}
            and parsed.hostname
            and parsed.port is not None
            and parsed.port > 0
            and parsed.path in {"", "/"}
            and not parsed.query
            and not parsed.fragment
            and not any(character.isspace() for character in value)
        )
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("The proxy must be an HTTP(S) URL with an explicit port")
    return value


async def http_routing(
    network: NetworkConfig, credentials: CredentialResolver
) -> tuple[bool, str | None]:
    proxy = None
    if network.proxy_ref is not None:
        secret = await credentials(network.proxy_ref)
        if not isinstance(secret, SecretStr):
            raise ValueError("The proxy resolver must return a SecretStr")
        proxy = validate_proxy_url(secret.get_secret_value())
    return network.mode == "environment", proxy
