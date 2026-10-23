"""Borrowed MCP connections, filters and native MCP configuration."""

from typing import Literal

from pydantic import Field, JsonValue, model_validator

from .base import ConfigValue, Identifier
from .callbacks import CallbackRef


class MCPServerRef(ConfigValue):
    connection_id: Identifier
    config: dict[str, JsonValue] = Field(default_factory=dict)
    allowed_tools: list[str] | None = None
    blocked_tools: list[str] = Field(default_factory=list)
    require_approval: (
        Literal["always", "never"]
        | bool
        | dict[str, bool | Literal["always", "never"] | dict[str, list[str]]]
        | CallbackRef
        | None
    ) = None

    @model_validator(mode="after")
    def validate_filter(self) -> "MCPServerRef":
        if self.allowed_tools is not None and self.blocked_tools:
            raise ValueError("Use allowed_tools or blocked_tools, not both")
        for names in (self.allowed_tools, self.blocked_tools):
            if names is not None and (len(names) != len(set(names)) or any(not n for n in names)):
                raise ValueError("MCP tool names must be nonempty and unique")
        return self


class MCPConfig(ConfigValue):
    convert_schemas_to_strict: bool | None = None
    include_server_in_tool_names: bool | None = None
    failure_error_function: CallbackRef | None = None
