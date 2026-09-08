from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, runtime_checkable
from uuid import UUID

from backend.app.agents.models import AgentProfile

AgentRuntimeSessionItem = dict[str, object]


class AgentRuntimeCapability(StrEnum):
    HANDOFFS = "handoffs"
    AGENTS_AS_TOOLS = "agents_as_tools"
    STRUCTURED_OUTPUT = "structured_output"
    STREAMING = "streaming"
    RESUMABLE_STATE = "resumable_state"
    GUARDRAILS = "guardrails"
    SESSIONS = "sessions"
    CANCELLATION = "cancellation"


@dataclass(frozen=True)
class AgentRuntimeCapabilities:
    """Capabilities exposed by one concrete runtime adapter.

    The set is intentionally product-owned. Provider SDK feature objects never cross this
    boundary, and unsupported features can be rejected before a run is queued.
    """

    provider: str
    adapter: str
    supported: frozenset[AgentRuntimeCapability] = frozenset()
    limits: dict[str, object] = field(default_factory=dict)
    unsupported_reasons: dict[str, str] = field(default_factory=dict)

    def supports(self, capability: AgentRuntimeCapability | str) -> bool:
        value = (
            capability.value
            if isinstance(capability, AgentRuntimeCapability)
            else str(capability)
        )
        return any(item.value == value for item in self.supported)

    def require(self, *capabilities: AgentRuntimeCapability | str) -> None:
        missing = [
            str(item.value if isinstance(item, AgentRuntimeCapability) else item)
            for item in capabilities
            if not self.supports(item)
        ]
        if missing:
            raise ValueError(
                f"Runtime adapter {self.adapter} does not support: {', '.join(missing)}"
            )


@runtime_checkable
class AgentRuntimeSession(Protocol):
    """Product-owned session protocol implemented by persistence adapters."""

    session_id: str

    async def get_items(self, limit: int | None = None) -> list[AgentRuntimeSessionItem]: ...

    async def add_items(self, items: list[AgentRuntimeSessionItem]) -> None: ...

    async def pop_item(self) -> AgentRuntimeSessionItem | None: ...

    async def clear_session(self) -> None: ...


@dataclass(frozen=True)
class AgentRuntimeAgentRef:
    name: str
    profile_id: UUID | None = None
    role: str | None = None


@dataclass(frozen=True)
class AgentRuntimeAgentDefinition:
    """Authorized, immutable definition used when an agent is a handoff target."""

    ref: AgentRuntimeAgentRef
    workspace_id: UUID
    instructions: str
    model: str | None = None
    model_settings: dict[str, object] = field(default_factory=dict)
    handoff_description: str | None = None


@dataclass(frozen=True)
class AgentRuntimeHandoff:
    target: AgentRuntimeAgentRef
    reason: str = ""
    input_filter: tuple[str, ...] = ()
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentRuntimeHandoffResult:
    source: AgentRuntimeAgentRef
    target: AgentRuntimeAgentRef
    status: str
    reason: str = ""
    filtered_context_keys: tuple[str, ...] = ()
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentRuntimeOutputSchema:
    name: str
    schema: dict[str, object]
    strict: bool = True
    version: str | None = None


@dataclass(frozen=True)
class AgentRuntimeGuardrail:
    name: str
    kind: str
    config: dict[str, object] = field(default_factory=dict)
    blocking: bool = True


@dataclass(frozen=True)
class AgentRuntimeGuardrails:
    input: tuple[AgentRuntimeGuardrail, ...] = ()
    output: tuple[AgentRuntimeGuardrail, ...] = ()


class AgentRuntimeStreamEventKind(StrEnum):
    RUN_STARTED = "run.started"
    AGENT_UPDATED = "agent.updated"
    TEXT_DELTA = "output.text.delta"
    TOOL_CALL = "tool.call"
    TOOL_RESULT = "tool.result"
    HANDOFF = "agent.handoff"
    USAGE = "model.usage"
    RUN_COMPLETED = "run.completed"
    RUN_FAILED = "run.failed"


@dataclass(frozen=True)
class AgentRuntimeStreamEvent:
    sequence: int
    event_type: str
    payload: dict[str, object] = field(default_factory=dict)
    delta: str | None = None
    is_terminal: bool = False


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
class AgentRuntimeAgentTool:
    """One authorized nested agent exposed as a provider SDK tool."""

    target: AgentRuntimeAgentDefinition
    tool_name: str
    description: str
    context: AgentRuntimeContext
    max_turns: int
    depth: int
    max_depth: int
    model: str
    provider: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    model_api: str | None = None
    model_provider_credential_id: UUID | None = None
    nested_tools: tuple[AgentRuntimeAgentTool, ...] = ()


@dataclass(frozen=True)
class AgentRuntimeAgentToolResult:
    source: AgentRuntimeAgentRef
    target: AgentRuntimeAgentRef
    tool_name: str
    tool_call_id: str
    status: str
    depth: int
    max_turns: int
    usage: dict[str, object] = field(default_factory=dict)
    error: dict[str, object] | None = None


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
class AgentRuntimeApprovalDecision:
    tool_call_id: str
    tool_name: str
    status: str
    reason: str | None = None


@dataclass(frozen=True)
class AgentRuntimeInterruption:
    tool_call_id: str
    tool_name: str
    tool_kind: str
    arguments: dict[str, object]
    policy_decision: dict[str, object] = field(default_factory=dict)
    kind: str = "tool_approval"
    message: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)


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
    session: AgentRuntimeSession | None = None
    previous_response_id: str | None = None
    conversation_id: str | None = None
    tracing: AgentRunTracing | None = None
    resume_state: AgentRuntimeResumeState | None = None
    approval_decisions: tuple[AgentRuntimeApprovalDecision, ...] = ()
    handoffs: tuple[AgentRuntimeHandoff, ...] = ()
    handoff_agents: tuple[AgentRuntimeAgentDefinition, ...] = ()
    agent_tools: tuple[AgentRuntimeAgentTool, ...] = ()
    output_schema: AgentRuntimeOutputSchema | None = None
    guardrails: AgentRuntimeGuardrails | None = None
    stream: bool = False


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
    interruptions: tuple[AgentRuntimeInterruption, ...] = ()
    structured_output: AgentRuntimeStructuredOutput | None = None
    stream_events: tuple[AgentRuntimeStreamEvent, ...] = ()
    handoffs: tuple[AgentRuntimeHandoffResult, ...] = ()
    agent_tool_calls: tuple[AgentRuntimeAgentToolResult, ...] = ()
    capabilities: AgentRuntimeCapabilities | None = None


@dataclass(frozen=True)
class AgentRuntimeStructuredOutput:
    value: object
    schema_name: str | None = None
    schema_version: str | None = None
    validated: bool = False


class AgentRuntimeAdapter(Protocol):
    @property
    def capabilities(self) -> AgentRuntimeCapabilities: ...

    async def run(self, request: AgentRunRequest) -> AgentRunResult: ...


class AgentRunner(Protocol):
    async def run(self, request: AgentRunRequest) -> AgentRunResult: ...
