"""Frozen resolved inputs and pure native SDK boundary values."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import UUID

from jsonschema import Draft202012Validator
from opsmesh_sdk.values import ReasoningEffort
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from opsmesh.shared.files import validate_relative_path


class AssetRef(BaseModel):
    """One content-addressed input asset of a run.

    The destination is a safe relative path inside the execution
    environment: the shared path validator rejects absolute paths, parent
    segments and backslashes, and the transfer resolves containment
    against the environment root before writing.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    ref: str = Field(min_length=1, max_length=240)
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    destination: str = Field(min_length=1, max_length=240)

    @field_validator("destination")
    @classmethod
    def safe_destination(cls, value: str) -> str:
        return validate_relative_path(value)


class UncertainOperationError(RuntimeError):
    """An external operation may have succeeded and must not be repeated blindly."""


@dataclass(frozen=True)
class ModelConnection:
    """Activity-local secret and platform-verified provider context bound."""

    api_key: str = field(repr=False)
    base_url: str
    context_tokens: int


class Configuration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ModelConfiguration(Configuration):
    connection_id: UUID
    name: str = Field(min_length=1, max_length=200)
    max_output_tokens: int = Field(default=4096, ge=1, le=131072)
    temperature: float | None = Field(default=None, ge=0, le=2)
    reasoning_effort: ReasoningEffort | None = None


class InstalledExport(Configuration):
    instance_id: UUID
    installation_id: UUID
    configuration_revision: int = Field(ge=0)
    artifact: str = Field(pattern="^sha256:[a-f0-9]{64}$")
    export_id: str = Field(min_length=1, max_length=100)


class HookBudget(Configuration):
    max_invocations: int = Field(default=128, ge=1, le=1024)
    timeout_seconds: int = Field(default=30, ge=1, le=120)
    model_tokens: int = Field(default=0, ge=0, le=262144)


class InstalledHook(InstalledExport):
    binding_id: str = Field(min_length=1, max_length=128)
    order: int = Field(default=0, ge=0)
    stages: list[str] = Field(min_length=1, max_length=9)
    failure_policy: Literal["block", "observe"] = "block"
    budget: HookBudget = Field(default_factory=HookBudget)


class ToolConfiguration(Configuration):
    name: str = Field(pattern="^[a-zA-Z0-9_-]{1,64}$")
    description: str = Field(default="", max_length=2000)
    input_schema: dict[str, Any]
    command: list[str] = Field(default_factory=list, max_length=100)
    extension: InstalledExport | None = None
    approval_required: bool = True

    @model_validator(mode="after")
    def one_implementation(self) -> ToolConfiguration:
        if bool(self.command) == bool(self.extension):
            raise ValueError("A tool requires exactly one command or installed extension")
        return self

    @field_validator("input_schema")
    @classmethod
    def valid_schema(cls, schema: dict[str, Any]) -> dict[str, Any]:
        validate_schema(schema)
        if schema.get("type") != "object":
            raise ValueError("Tool input must be an object")
        return schema


class MCPBinding(Configuration):
    connection_id: UUID
    tools_digest: str = Field(pattern="^[a-f0-9]{64}$")
    allowed_tools: list[str] = Field(min_length=1, max_length=100)


class AgentConfiguration(Configuration):
    mcp_connections: list[MCPBinding] = Field(default_factory=list, max_length=100)
    name: str = Field(min_length=1, max_length=120)
    instructions: str = Field(default="", max_length=100000)
    model: ModelConfiguration
    tools: list[ToolConfiguration] = Field(default_factory=list, max_length=100)
    handoffs: list[str] = Field(default_factory=list, max_length=100)
    agent_tools: list[str] = Field(default_factory=list, max_length=100)
    output_schema: dict[str, Any] | None = None

    @field_validator("output_schema")
    @classmethod
    def valid_output(cls, schema: dict[str, Any] | None) -> dict[str, Any] | None:
        if schema is not None:
            validate_schema(schema)
        return schema


class ExecutionLimits(Configuration):
    max_turns: int = Field(default=20, ge=1, le=100)
    execution_timeout_seconds: int = Field(default=3600, ge=1, le=86400)
    approval_timeout_seconds: int = Field(default=86400, ge=1, le=604800)


class RunOutput(Configuration):
    path: str = Field(min_length=1, max_length=240)
    name: str = Field(min_length=1, max_length=240)
    media_type: str | None = Field(default=None, max_length=200)

    @field_validator("path")
    @classmethod
    def safe_path(cls, value: str) -> str:
        from opsmesh.shared.files import validate_relative_path

        return validate_relative_path(value)


class ExecutionRequest(Configuration):
    configuration_source: str | None = Field(default=None, pattern="^[a-fA-F0-9-]{36}$")
    parent_invocation_id: UUID | None = None
    hooks: list[InstalledHook] = Field(default_factory=list, max_length=64)
    assets: list[AssetRef] = Field(default_factory=list, max_length=100)
    outputs: list[RunOutput] = Field(default_factory=list, max_length=100)
    session_id: UUID
    input: str | list[dict[str, Any]]
    entry_agent: str = Field(pattern="^[a-zA-Z0-9_-]{1,64}$")
    agents: dict[str, AgentConfiguration] = Field(min_length=1, max_length=100)
    environment_id: UUID | None = None
    limits: ExecutionLimits = Field(default_factory=ExecutionLimits)

    @model_validator(mode="after")
    def validate_graph(self) -> ExecutionRequest:
        if len({hook.binding_id for hook in self.hooks}) != len(self.hooks):
            raise ValueError("Hook binding identifiers must be unique")
        if (self.assets or self.outputs) and self.environment_id is None:
            raise ValueError("Run assets require an explicit environment")
        if len({asset.destination for asset in self.assets}) != len(self.assets):
            raise ValueError("Run asset destinations must be unique")
        if len({output.name for output in self.outputs}) != len(self.outputs):
            raise ValueError("Run artifact names must be unique")
        if self.entry_agent not in self.agents:
            raise ValueError("Entry agent is not defined")
        for key, definition in self.agents.items():
            related = definition.handoffs + definition.agent_tools
            if any(target not in self.agents for target in related):
                raise ValueError("Agent references an undefined agent")
            names = [tool.name for tool in definition.tools] + definition.agent_tools
            if len(names) != len(set(names)):
                raise ValueError("Agent tool names must be unique")
            if any(tool.command for tool in definition.tools) and self.environment_id is None:
                raise ValueError("Sandbox tools require an explicit environment")
            for target in definition.agent_tools:
                stack = [(target, 1)]
                seen: set[str] = set()
                while stack:
                    current, depth = stack.pop()
                    if current == key or depth > 8:
                        raise ValueError("Nested Agent tools contain a cycle or exceed depth 8")
                    if current not in seen:
                        seen.add(current)
                        agent = self.agents[current]
                        stack.extend(
                            (name, depth + 1) for name in agent.agent_tools + agent.handoffs
                        )
        return self


class RunReference(Configuration):
    execution_id: UUID
    workflow_id: str
    workflow_name: str = "ExecutionRunWorkflow"
    run_id: UUID


class PreparedRun(Configuration):
    reference: RunReference
    principal_id: UUID
    request: ExecutionRequest


class ToolInvocation(Configuration):
    run_id: UUID
    agent_key: str
    tool_name: str
    call_id: str
    arguments: dict[str, Any] = Field(default_factory=dict)


def validate_schema(schema: dict[str, Any]) -> None:
    pending: list[Any] = [schema]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            reference = value.get("$ref")
            if reference is not None and (
                not isinstance(reference, str) or not reference.startswith("#")
            ):
                raise ValueError("Remote JSON schema references are not allowed")
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    Draft202012Validator.check_schema(schema)
