"""Tool bindings; agent-as-tool settings are distinct from handoffs."""

from typing import Annotated, Literal

from pydantic import Field, JsonValue

from .base import ConfigValue, Identifier
from .callbacks import CallbackRef


class ToolRef(ConfigValue):
    type: Literal["tool"] = "tool"
    tool_id: Identifier
    config: dict[str, JsonValue] = Field(default_factory=dict)


class NativeToolConfig(ConfigValue):
    type: Literal["native"] = "native"
    tool_type: Identifier
    options: dict[str, JsonValue] = Field(default_factory=dict)


class AgentToolRef(ConfigValue):
    type: Literal["agent"] = "agent"
    agent_id: Identifier
    tool_name: str | None = None
    tool_description: str | None = None
    custom_output_extractor: CallbackRef | None = None
    is_enabled: bool | CallbackRef = True
    on_stream: CallbackRef | None = None
    max_turns: int | None = Field(default=None, gt=0)
    hooks: CallbackRef | None = None
    failure_error_function: CallbackRef | None = None
    needs_approval: bool | CallbackRef = False
    parameters: CallbackRef | None = None
    input_builder: CallbackRef | None = None
    include_input_schema: bool = False


ToolBinding = Annotated[ToolRef | NativeToolConfig | AgentToolRef, Field(discriminator="type")]
