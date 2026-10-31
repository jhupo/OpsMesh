"""Public serializable SDK-independent agent definitions."""

from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import Field, model_validator

from opsmesh.framework.typing import OpsMeshContract

from .hosted import HostedMCPToolSelection, HostedToolBinding
from .native_tools import NativeToolBinding, SessionCompactionConfiguration


class ToolBinding(OpsMeshContract):
    aliases: tuple[str, ...] = ()
    resource_ref: str | None = None
    source_name: str | None = None
    strict_schema: bool = False
    name: str = Field(min_length=1)
    description: str = ""
    input_schema: dict[str, Any] = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        }
    )
    tool_ref: str | None = None
    requires_approval: bool = False
    enabled_ref: str | None = None
    timeout_seconds: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    defer_loading: bool = False


class HandoffBinding(OpsMeshContract):
    agent_ref: str
    tool_name: str | None = None
    description: str | None = None
    callback_ref: str | None = None
    input_schema: dict[str, Any] | None = None
    input_filter_ref: str | None = None
    enabled_ref: str | None = None


class AgentToolBinding(OpsMeshContract):
    sandbox_policy: Literal["auto", "shared", "isolated"] = "auto"
    agent_ref: str
    name: str
    description: str = ""
    max_turns: int = Field(default=10, ge=1, le=1000)
    requires_approval: bool = False
    output_extractor_ref: str | None = None
    enabled_ref: str | None = None
    input_type_ref: str | None = None
    input_builder_ref: str | None = None
    include_input_schema: bool = False


class GuardrailBinding(OpsMeshContract):
    ref: str
    stage: Literal["input", "output", "tool_input", "tool_output"]
    tool_name: str | None = None
    run_in_parallel: bool = False


class HookBinding(OpsMeshContract):
    ref: str
    scope: Literal["agent", "run"] = "run"
    include_payload: bool = False


class SandboxConfiguration(OpsMeshContract):
    memory_timeout_seconds: float = Field(default=120, gt=0, le=86400, allow_inf_nan=False)
    provider: str = Field(default="docker", min_length=1)
    trusted_local: bool = False
    manifest: dict[str, Any] = Field(default_factory=dict)
    options: dict[str, Any] = Field(default_factory=dict)
    capabilities: tuple[Literal["shell", "filesystem", "compaction"], ...] = (
        "shell",
        "filesystem",
        "compaction",
    )
    capability_options: tuple[dict[str, Any], ...] = ()
    base_instructions: str | None = None
    run_as: str | None = None
    cwd: str | None = None
    session_state: dict[str, Any] | None = None
    snapshot_path: str | None = None
    snapshot_ref: str | None = None
    session_ref: str | None = None
    manifest_concurrency: int = Field(default=8, ge=1, le=128)
    file_concurrency: int = Field(default=16, ge=1, le=256)
    archive_limits: dict[str, int] = Field(
        default_factory=lambda: {
            "max_input_bytes": 100_000_000,
            "max_extracted_bytes": 500_000_000,
            "max_members": 10000,
        }
    )

    @model_validator(mode="after")
    def validate_scope(self) -> Self:
        if self.snapshot_ref and self.session_ref:
            raise ValueError(
                "Choose a stored workspace snapshot or a live sandbox session reference"
            )
        if self.provider == "local" and not self.trusted_local:
            raise ValueError("local sandbox requires explicit trusted_local=True")
        if self.cwd is not None:
            if not self.cwd or "\\" in self.cwd or self.cwd.startswith("/"):
                raise ValueError("cwd must be a POSIX workspace-relative directory")
            if ".." in self.cwd.split("/") or ":" in self.cwd:
                raise ValueError("cwd must not escape the workspace")
        if any(value <= 0 for value in self.archive_limits.values()):
            raise ValueError("archive limits must be positive")
        return self


class MCPToolConfiguration(OpsMeshContract):
    convert_schemas_to_strict: bool = False
    include_server_in_tool_names: bool = True
    failure_behavior: Literal["raise", "message"] = "raise"
    failure_formatter_ref: str | None = None
    tool_filter_ref: str | None = None

    @model_validator(mode="after")
    def validate_failure_policy(self) -> Self:
        if self.failure_formatter_ref and self.failure_behavior != "message":
            raise ValueError("MCP failure formatter requires message error behavior")
        return self


class AgentSpecification(OpsMeshContract):
    id: str = Field(min_length=1)
    revision: int = Field(default=1, ge=1)
    name: str = Field(min_length=1)
    backend: str = "openai_agents"
    code_version: str = "1"
    instructions: str = ""
    instruction_ref: str | None = None
    prompt: dict[str, Any] | None = None
    prompt_ref: str | None = None
    prompt_template_ref: str | None = None
    prompt_variables: dict[str, Any] = Field(default_factory=dict)
    model: str | None = None
    model_profile_ref: str | None = None
    model_settings: dict[str, Any] = Field(default_factory=dict)
    model_transport: Literal["http", "websocket"] = "http"
    session_compaction: SessionCompactionConfiguration | None = None
    tools: tuple[ToolBinding, ...] = ()
    hosted_tools: tuple[HostedToolBinding, ...] = ()
    native_tools: tuple[NativeToolBinding, ...] = ()
    skill_refs: tuple[str, ...] = ()
    skill_loading: Literal["eager", "lazy"] = "lazy"
    mcp_refs: tuple[str, ...] = ()
    mcp_config: MCPToolConfiguration = Field(default_factory=MCPToolConfiguration)
    knowledge_collections: tuple[str, ...] = ()
    workspace_id: str | None = None
    memory_namespace_id: str | None = None
    memory_generate: bool = False
    handoffs: tuple[HandoffBinding, ...] = ()
    agent_tools: tuple[AgentToolBinding, ...] = ()
    output_schema: dict[str, Any] | None = None
    output_type_ref: str | None = None
    guardrails: tuple[GuardrailBinding, ...] = ()
    hooks: tuple[HookBinding, ...] = ()
    sandbox: SandboxConfiguration | None = None
    max_turns: int = Field(default=10, ge=1, le=1000)
    timeout_seconds: float = Field(default=300, gt=0, le=86400, allow_inf_nan=False)
    tool_choice: str | HostedMCPToolSelection | None = None
    tool_completion: Literal["run_llm_again", "stop_on_first_tool", "stop_at_tools"] = (
        "run_llm_again"
    )
    stop_tools: tuple[str, ...] = ()
    tool_completion_ref: str | None = None
    reset_tool_choice: bool = True
    pre_approval_tool_input_guardrails: bool = False
    max_function_tool_concurrency: int | None = Field(default=None, ge=1, le=1024, strict=True)

    @model_validator(mode="after")
    def validate_tools(self) -> Self:
        if (
            any(tool.defer_loading for tool in self.tools)
            or any(tool.type == "mcp" and tool.defer_loading for tool in self.hosted_tools)
        ) and not any(tool.type == "tool_search" for tool in self.hosted_tools):
            raise ValueError("Deferred tools require server tool search")
        if self.output_schema is not None and self.output_type_ref is not None:
            raise ValueError("Choose JSON output schema or registered Python output type")
        if self.tool_completion_ref and self.tool_completion != "run_llm_again":
            raise ValueError("Custom tool completion cannot be combined with a fixed policy")
        if (
            self.memory_generate
            and self.sandbox
            and any(
                item.get("type") == "memory" and item.get("generate", {}) is not None
                for item in self.sandbox.capability_options
            )
        ):
            raise ValueError("Platform and native Memory cannot both generate memory")
        names = (
            [tool.name for tool in self.tools]
            + [tool.name for tool in self.agent_tools]
            + [
                f"mcp:{tool.server_label}" if tool.type == "mcp" else tool.type
                for tool in self.hosted_tools
            ]
            + [tool.type for tool in self.native_tools]
            + [tool.tool_name for tool in self.handoffs if tool.tool_name]
        )
        if (
            sum(
                value is not None
                for value in (self.prompt, self.prompt_ref, self.prompt_template_ref)
            )
            > 1
        ):
            raise ValueError("Choose a static prompt, dynamic prompt, or registered template")
        if self.prompt_variables and self.prompt_template_ref is None:
            raise ValueError("Template variables require a registered template reference")
        if isinstance(self.tool_choice, HostedMCPToolSelection):
            matching = [
                tool
                for tool in self.hosted_tools
                if tool.type == "mcp" and tool.server_label == self.tool_choice.server_label
            ]
            if not matching or self.tool_choice.name not in matching[0].allowed_tools:
                raise ValueError("Hosted MCP selection must reference an allowed server tool")
        if len(names) != len(set(names)):
            raise ValueError("tool names must be unique")
        if (
            isinstance(self.tool_choice, str)
            and self.tool_choice not in ("auto", "required", "none")
            and self.tool_choice not in names
            and not (self.mcp_refs or self.skill_refs or self.knowledge_collections)
        ):
            raise ValueError("forced tool must be in the agent's tools")
        if self.tool_completion == "stop_at_tools" and not self.stop_tools:
            raise ValueError("stop_at_tools requires stop_tools")
        callable_names = [tool.name for tool in self.tools] + [
            tool.name for tool in self.agent_tools
        ]
        if any(name not in callable_names for name in self.stop_tools) and not (
            self.mcp_refs or self.skill_refs or self.knowledge_collections
        ):
            raise ValueError("stop tool must be in the agent's tools")
        hosted_names = {tool.type for tool in self.hosted_tools}
        native_names = {tool.type for tool in self.native_tools}
        if any(
            guard.stage in {"tool_input", "tool_output"}
            and guard.tool_name in hosted_names | native_names
            for guard in self.guardrails
        ):
            raise ValueError("Function tool guardrails cannot intercept hosted or native tools")
        return self

    @property
    def ref(self) -> str:
        return f"{self.id}@{self.revision}"
