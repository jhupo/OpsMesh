"""Agent constructor settings with references in place of live Python objects."""

from typing import Literal

from agents.prompts import Prompt
from pydantic import Field, JsonValue, TypeAdapter, field_validator, model_validator

from .base import ConfigValue
from .callbacks import CallbackRef
from .handoffs import HandoffRef
from .mcp import MCPConfig, MCPServerRef
from .model import ModelRef, model_settings
from .output import OutputConfig
from .tools import ToolBinding


class PromptConfig(ConfigValue):
    id: str = Field(min_length=1)
    version: str | None = None
    variables: dict[str, JsonValue] | None = None

    @model_validator(mode="after")
    def validate_prompt(self) -> "PromptConfig":
        TypeAdapter(Prompt).validate_python(self.model_dump(exclude_none=True))
        return self


class StopAtTools(ConfigValue):
    stop_at_tool_names: list[str] = Field(min_length=1)

    @field_validator("stop_at_tool_names")
    @classmethod
    def validate_names(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)) or any(not name.strip() for name in value):
            raise ValueError("Stop tool names must be nonempty and unique")
        return value


class BaseAgentConfig(ConfigValue):
    name: str = Field(min_length=1, max_length=200)
    instructions: str | CallbackRef | None = None
    handoff_description: str | None = None
    model: ModelRef | None = None
    model_settings: dict[str, JsonValue] | None = None
    tools: list[ToolBinding] = Field(default_factory=list)
    mcp_servers: list[MCPServerRef] = Field(default_factory=list)
    mcp_config: MCPConfig = Field(default_factory=MCPConfig)
    handoffs: list[HandoffRef] = Field(default_factory=list)
    prompt: PromptConfig | CallbackRef | None = None
    input_guardrails: list[CallbackRef] = Field(default_factory=list)
    output_guardrails: list[CallbackRef] = Field(default_factory=list)
    output_type: OutputConfig | None = None
    hooks: CallbackRef | None = None
    tool_use_behavior: (
        Literal["run_llm_again", "stop_on_first_tool"] | StopAtTools | CallbackRef
    ) = "run_llm_again"
    reset_tool_choice: bool = True

    @field_validator("model_settings")
    @classmethod
    def validate_settings(cls, value: dict[str, JsonValue] | None) -> dict[str, JsonValue] | None:
        if value is not None:
            model_settings(value)
        return value


class AgentConfig(BaseAgentConfig):
    type: Literal["agent"] = "agent"
