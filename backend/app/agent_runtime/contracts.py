from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID

from agents.memory import Session as AgentsSession

from backend.app.agents.models import AgentProfile


@dataclass(frozen=True)
class AgentRuntimeToolDefinition:
    name: str
    source: str
    description: str
    input_schema: dict[str, object]
    parameters: dict[str, object] = field(default_factory=dict)
    locked_parameters: tuple[str, ...] = ()
    requires_approval: bool = False
    risk_level: str = "low"
    required_resource_type: str | None = None
    required_access_modes: tuple[str, ...] = ()
    mcp_server_id: UUID | None = None
    mcp_tool_allowlist_id: UUID | None = None


@dataclass(frozen=True)
class AgentRuntimeResourceGrant:
    resource_id: UUID
    resource_type: str
    access_mode: str
    locator: dict[str, object]
    parameters: dict[str, object]
    version: int


@dataclass(frozen=True)
class AgentRuntimeExecutionBinding:
    mode: str
    workspace_runtime_id: UUID | None
    runtime_space_id: UUID | None
    capability_resource_ids: tuple[UUID, ...] = ()
    network_disabled: bool = False
    allowed_file_ids: tuple[UUID, ...] = ()


@dataclass(frozen=True)
class AgentRuntimeContext:
    workspace_id: UUID
    task_id: UUID | None
    run_id: UUID
    user_id: UUID | None = None
    allowed_tools: tuple[str, ...] = ()
    tool_definitions: tuple[AgentRuntimeToolDefinition, ...] = ()
    resource_grants: tuple[AgentRuntimeResourceGrant, ...] = ()
    file_scope_ids: tuple[UUID, ...] = ()
    runtime_binding: AgentRuntimeExecutionBinding | None = None
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentRuntimeToolResult:
    status: str
    output: dict[str, object] | None = None
    error: dict[str, object] | None = None
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentRunTracing:
    workflow_name: str
    trace_id: str | None = None
    group_id: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)
    disabled: bool = False
    include_sensitive_data: bool = False


@dataclass(frozen=True)
class AgentRuntimeResumeState:
    provider: str
    serialized_state: str
    schema_version: str | None = None
    sdk_version: str | None = None


@dataclass(frozen=True)
class AgentRuntimeToolContinuation:
    tool_name: str
    status: str
    result: dict[str, object] | None = None
    error: dict[str, object] | None = None
    metadata: dict[str, object] = field(default_factory=dict)


class AgentRuntimeToolExecutor(Protocol):
    def execute_tool(
        self,
        *,
        context: AgentRuntimeContext,
        tool_name: str,
        arguments: dict[str, object],
    ) -> AgentRuntimeToolResult: ...


@dataclass(frozen=True)
class AgentRunRequest:
    agent_profile: AgentProfile
    input_text: str
    context: AgentRuntimeContext
    max_turns: int = 10
    model: str | None = None
    provider: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    model_api: str | None = None
    model_provider_credential_id: UUID | None = None
    tool_executor: AgentRuntimeToolExecutor | None = None
    continuations: tuple[AgentRuntimeToolContinuation, ...] = ()
    session: AgentsSession | None = None
    previous_response_id: str | None = None
    conversation_id: str | None = None
    tracing: AgentRunTracing | None = None
    resume_state: AgentRuntimeResumeState | None = None


@dataclass(frozen=True)
class AgentRuntimeEvent:
    event_type: str
    message: str = ""
    payload: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentRunResult:
    final_output: str
    raw_output: object | None = None
    events: tuple[AgentRuntimeEvent, ...] = ()
    resume_state: AgentRuntimeResumeState | None = None


class AgentRunner(Protocol):
    async def run(self, request: AgentRunRequest) -> AgentRunResult: ...
