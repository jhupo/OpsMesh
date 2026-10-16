"""MCP template contracts: remote connections, local packages and tool lists.

The declaration is static: a remote endpoint or a local launch command,
the parameters and secret slots it expects and the tools it advertises.
Validation never connects, starts a server or discovers tools — running
an MCP server belongs to execution.runtime with its own lifecycle.
"""

from __future__ import annotations

import re
from typing import Literal
from urllib.parse import urlparse

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from opsmesh.capabilities.validation import (
    validate_parameter_schema,
    validate_secret_slots,
)
from opsmesh.shared.errors import InvalidArgumentError

MAX_ENDPOINT_LENGTH = 2000
MAX_COMMAND_LENGTH = 500
MAX_TOOLS = 256
MAX_ENVIRONMENT_ENTRIES = 64
SECRET_REFERENCE_PATTERN = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]{0,99})\}$")


class McpToolDeclaration(BaseModel):
    """One advertised tool with its parameter schema; discovery is runtime."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=2000)
    parameters_schema: dict[str, object] = Field(default_factory=dict)

    @field_validator("parameters_schema")
    @classmethod
    def _parameters_schema(cls, value: dict[str, object]) -> dict[str, object]:
        return validate_parameter_schema(value)


class McpConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transport: Literal["remote", "stdio"]
    endpoint: str | None = Field(default=None, max_length=MAX_ENDPOINT_LENGTH)
    command: list[str] | None = Field(default=None, max_length=64)
    arguments: list[str] = Field(default_factory=list, max_length=256)
    environment: dict[str, str] = Field(default_factory=dict)
    tools: list[McpToolDeclaration] = Field(default_factory=list, max_length=MAX_TOOLS)
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

    @model_validator(mode="after")
    def _transport_declarations(self) -> McpConfiguration:
        if self.transport == "remote":
            if self.endpoint is None:
                raise ValueError("a remote MCP template must declare its endpoint")
            parsed = urlparse(self.endpoint)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError("a remote MCP endpoint must be an http(s) URL")
            if self.command is not None:
                raise ValueError("a remote MCP template must not declare a launch command")
        else:
            if not self.command:
                raise ValueError("a local MCP template must declare its launch command")
            if any(not part.strip() for part in self.command):
                raise ValueError("a launch command part must not be empty")
            if any(len(part) > MAX_COMMAND_LENGTH for part in self.command):
                raise ValueError("a launch command part is too long")
            if self.endpoint is not None:
                raise ValueError("a local MCP template must not declare a remote endpoint")
        names = [tool.name for tool in self.tools]
        if len(set(names)) != len(names):
            raise ValueError("advertised MCP tool names must be unique")
        if len(self.environment) > MAX_ENVIRONMENT_ENTRIES:
            raise ValueError("an MCP template declares too many environment entries")
        for value in self.environment.values():
            match = SECRET_REFERENCE_PATTERN.match(value)
            if match is not None and match.group(1) not in self.secret_slots:
                raise ValueError(
                    "an environment value references an undeclared secret slot"
                )
        return self


def validated_configuration(configuration: object) -> dict[str, object]:
    """Validate one MCP configuration, mapping failures to the domain error."""
    try:
        model = McpConfiguration.model_validate(configuration)
    except ValidationError as exc:
        raise InvalidArgumentError(
            "The MCP configuration violates its contract",
            details={"reason": str(exc)},
        ) from exc
    return model.model_dump(mode="json")


class McpCreateRequest(BaseModel):
    """Create one MCP definition with its first draft version."""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2000)
    visibility: Literal["private", "public"] = "private"
    configuration: McpConfiguration


class McpDraftRequest(BaseModel):
    """Replace the current draft configuration of one MCP definition."""

    model_config = ConfigDict(extra="forbid")

    configuration: McpConfiguration
