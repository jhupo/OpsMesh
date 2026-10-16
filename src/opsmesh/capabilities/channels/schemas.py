"""Channel definition contracts: vendor protocol and adapter operations.

A channel capability declares one vendor protocol and the adapter
operations that speak it — receive, send, connect and disconnect. The
declaration is static: an entrypoint is an identifier the package must
carry as a declared resource, and no vendor client is constructed, no
signature is verified and no message is exchanged at publish time.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from opsmesh.capabilities.validation import (
    validate_parameter_schema,
    validate_secret_slots,
)
from opsmesh.shared.errors import InvalidArgumentError

MAX_OPERATIONS = 4
MAX_ENTRYPOINT_LENGTH = 300
MAX_DESCRIPTION_LENGTH = 2000
MAX_PROTOCOL_LENGTH = 50
SIGNATURE_ALGORITHMS = ("hmac-sha256",)
CHANNEL_OPERATIONS = ("receive", "send", "connect", "disconnect")
REQUIRED_CHANNEL_OPERATIONS = ("receive", "send")
ENTRYPOINT_PATTERN = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*:[A-Za-z_][A-Za-z0-9_]*$"
)
PROTOCOL_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,48}[a-z0-9]$")


class ChannelOperation(BaseModel):
    """One adapter operation with its identifier and typed I/O schemas."""

    model_config = ConfigDict(extra="forbid")

    name: Literal["receive", "send", "connect", "disconnect"]
    entrypoint: str = Field(min_length=1, max_length=MAX_ENTRYPOINT_LENGTH)
    description: str = Field(default="", max_length=MAX_DESCRIPTION_LENGTH)
    input_schema: dict[str, object] = Field(default_factory=dict)
    output_schema: dict[str, object] = Field(default_factory=dict)

    @field_validator("input_schema", "output_schema")
    @classmethod
    def _schemas(cls, value: dict[str, object]) -> dict[str, object]:
        return validate_parameter_schema(value)


class ChannelConfiguration(BaseModel):
    """The vendor protocol one channel definition speaks."""

    model_config = ConfigDict(extra="forbid")

    protocol: str = Field(min_length=2, max_length=MAX_PROTOCOL_LENGTH)
    signature_algorithm: Literal["hmac-sha256"] = "hmac-sha256"
    operations: list[ChannelOperation] = Field(min_length=1, max_length=MAX_OPERATIONS)
    parameters_schema: dict[str, object] = Field(default_factory=dict)
    secret_slots: list[str] = Field(default_factory=list)

    @field_validator("parameters_schema")
    @classmethod
    def _parameters_schema(cls, value: dict[str, object]) -> dict[str, object]:
        return validate_parameter_schema(value)

    @field_validator("secret_slots")
    @classmethod
    def _secret_slots(cls, value: list[str]) -> list[str]:
        return validate_secret_slots(value)

    @field_validator("protocol")
    @classmethod
    def _protocol(cls, value: str) -> str:
        if not PROTOCOL_PATTERN.match(value):
            raise ValueError("a channel protocol is a lowercase vendor identifier")
        return value

    @field_validator("operations")
    @classmethod
    def _operations(cls, value: list[ChannelOperation]) -> list[ChannelOperation]:
        names = [operation.name for operation in value]
        if len(set(names)) != len(names):
            raise ValueError("channel operation names must be unique")
        missing = sorted(set(REQUIRED_CHANNEL_OPERATIONS) - set(names))
        if missing:
            raise ValueError(f"a channel must declare the operations: {missing}")
        for operation in value:
            if not ENTRYPOINT_PATTERN.match(operation.entrypoint):
                raise ValueError("a channel entrypoint must name module:function")
        return value


def validated_configuration(configuration: object) -> dict[str, object]:
    """Validate one channel configuration, mapping failures to the domain error."""
    try:
        model = ChannelConfiguration.model_validate(configuration)
    except ValidationError as exc:
        raise InvalidArgumentError(
            "The channel configuration violates its contract",
            details={"reason": str(exc)},
        ) from exc
    return model.model_dump(mode="json")


class ChannelCreateRequest(BaseModel):
    """Create one channel definition with its first draft version."""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2000)
    visibility: Literal["private", "public"] = "private"
    configuration: ChannelConfiguration


class ChannelDraftRequest(BaseModel):
    """Replace the current draft configuration of one channel definition."""

    model_config = ConfigDict(extra="forbid")

    configuration: ChannelConfiguration


__all__ = [
    "CHANNEL_OPERATIONS",
    "ChannelConfiguration",
    "ChannelCreateRequest",
    "ChannelDraftRequest",
    "ChannelOperation",
    "REQUIRED_CHANNEL_OPERATIONS",
    "SIGNATURE_ALGORITHMS",
    "validated_configuration",
]
