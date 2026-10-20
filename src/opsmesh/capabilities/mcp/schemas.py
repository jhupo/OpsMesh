"""MCP template contracts: remote connections, local packages and tool lists.

The declaration is static: a remote endpoint or a local launch command,
the ordinary parameters it expects and the tools it advertises.
Validation never connects, starts a server or discovers tools — running
an MCP server belongs to orchestration.runtime with its own lifecycle.
"""

from __future__ import annotations

from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from opsmesh.capabilities.validation import validate_parameter_schema
from opsmesh.shared.errors import InvalidArgumentError

MAX_ENDPOINT_LENGTH = 2000
MAX_COMMAND_LENGTH = 500
MAX_TOOLS = 256
MAX_ENVIRONMENT_ENTRIES = 64


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

    @field_validator("parameters_schema")
    @classmethod
    def _parameters_schema(cls, value: dict[str, object]) -> dict[str, object]:
        return validate_parameter_schema(value)

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
