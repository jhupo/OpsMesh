from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, runtime_checkable
from uuid import UUID


class AgentExecutionFeature(StrEnum):
    TOOLS = "tools"
    HANDOFFS = "handoffs"
    AGENTS_AS_TOOLS = "agents_as_tools"
    STRUCTURED_OUTPUT = "structured_output"
    STREAMING = "streaming"
    RESUMABLE_STATE = "resumable_state"
    GUARDRAILS = "guardrails"
    SESSIONS = "sessions"
    CANCELLATION = "cancellation"
    LIFECYCLE_EVENTS = "lifecycle_events"
    USAGE = "usage"


@dataclass(frozen=True)
class AgentExecutionCapabilities:
    """Capabilities exposed by one concrete runtime adapter.

    The set is intentionally product-owned. Provider SDK feature objects never cross this
    boundary, and unsupported features can be rejected before a run is queued.
    """

    provider: str
    adapter: str
    supported: frozenset[AgentExecutionFeature] = frozenset()
    limits: dict[str, object] = field(default_factory=dict)
    unsupported_reasons: dict[str, str] = field(default_factory=dict)

    def supports(self, capability: AgentExecutionFeature | str) -> bool:
        value = (
            capability.value if isinstance(capability, AgentExecutionFeature) else str(capability)
        )
        return any(item.value == value for item in self.supported)


@dataclass(frozen=True)
class AgentSessionScope:
    session_id: str
    workspace_id: UUID


@dataclass(frozen=True)
class AgentReference:
    name: str
    profile_id: UUID | None = None
    role: str | None = None


@dataclass(frozen=True)
class AgentProfileSnapshot:
    """Immutable root-agent definition accepted by provider SDK adapters."""

    id: UUID | None
    workspace_id: UUID
    version: int
    name: str
    role: str
    instructions: str
    model: str
    model_settings: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentTargetProfile:
    """Authorized, immutable definition used when an agent is a handoff target."""

    ref: AgentReference
    workspace_id: UUID
    instructions: str
    model: str | None = None
    model_settings: dict[str, object] = field(default_factory=dict)
    handoff_description: str | None = None
    version: int = 1


@dataclass(frozen=True)
class AgentHandoffResult:
    source: AgentReference
    target: AgentReference
    status: str
    reason: str = ""
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentOutputSchemaSpec:
    name: str
    schema: dict[str, object]
    strict: bool = True
    version: str | None = None


@dataclass(frozen=True)
class AgentGuardrailSpec:
    name: str
    kind: str
    config: dict[str, object] = field(default_factory=dict)
    blocking: bool = True


@dataclass(frozen=True)
class AgentGuardrailConfig:
    input: tuple[AgentGuardrailSpec, ...] = ()
    output: tuple[AgentGuardrailSpec, ...] = ()


@dataclass(frozen=True)
class AgentGuardrailResult:
    stage: str
    name: str
    kind: str
    status: str
    blocking: bool
    evidence: dict[str, object] = field(default_factory=dict)


class AgentStreamEventKind(StrEnum):
    RUN_STARTED = "run.started"
    AGENT_UPDATED = "agent.updated"
    TEXT_DELTA = "output.text.delta"
    TOOL_CALL = "tool.call"
    TOOL_RESULT = "tool.result"
    HANDOFF = "agent.handoff"
    USAGE = "model.usage"
    RUN_PAUSED = "run.paused"
    RUN_COMPLETED = "run.completed"
    RUN_FAILED = "run.failed"


@runtime_checkable
class AgentCancellation(Protocol):
    """Cooperative cancellation owned by the OpsMesh execution layer."""

    async def is_cancelled(self) -> bool: ...

    async def wait_cancelled(self) -> None: ...


@dataclass(frozen=True)
class AgentStreamEvent:
    sequence: int
    event_type: str
    payload: dict[str, object] = field(default_factory=dict)
    delta: str | None = None
    is_terminal: bool = False


@dataclass(frozen=True)
class AgentToolSpec:
    name: str
    source: str
    description: str
    input_schema: dict[str, object]
    title: str = ""
    output_schema: dict[str, object] = field(default_factory=dict)
    parameters: dict[str, object] = field(default_factory=dict)
    locked_parameters: tuple[str, ...] = ()
    requires_approval: bool = False
    risk_level: str = "low"
    required_resource_type: str | None = None
    required_access_modes: tuple[str, ...] = ()
    mcp_server_id: UUID | None = None
    mcp_tool_allowlist_id: UUID | None = None


@dataclass(frozen=True)
class AgentResourceGrant:
    resource_id: UUID
    resource_type: str
    access_mode: str
    locator: dict[str, object]
    parameters: dict[str, object]
    version: int


@dataclass(frozen=True)
class AgentEnvironmentBinding:
    mode: str
    workspace_runtime_id: UUID | None
    runtime_space_id: UUID | None
    capability_resource_ids: tuple[UUID, ...] = ()
    allowed_file_ids: tuple[UUID, ...] = ()


@dataclass(frozen=True)
class AgentExecutionContext:
    workspace_id: UUID
    task_id: UUID | None
    run_id: UUID
    user_id: UUID | None = None
    allowed_tools: tuple[str, ...] = ()
    tool_definitions: tuple[AgentToolSpec, ...] = ()
    resource_grants: tuple[AgentResourceGrant, ...] = ()
    file_scope_ids: tuple[UUID, ...] = ()
    runtime_binding: AgentEnvironmentBinding | None = None
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentInvocationConfig:
    """Frozen execution scope for a native SDK handoff or agent tool."""

    target: AgentTargetProfile
    tool_name: str
    description: str
    context: AgentExecutionContext
    max_turns: int
    depth: int
    max_depth: int
    model: str
    provider: str | None = None
    base_url: str | None = None
    api_key: str | None = field(default=None, repr=False)
    model_api: str | None = None
    model_provider_credential_id: UUID | None = None
    nested_tools: tuple[AgentInvocationConfig, ...] = ()
    nested_handoffs: tuple[AgentInvocationConfig, ...] = ()
    output_schema: AgentOutputSchemaSpec | None = None
    guardrails: AgentGuardrailConfig | None = None
    require_handoff: bool = False


@dataclass(frozen=True)
class AgentToolCallResult:
    source: AgentReference
    target: AgentReference
    tool_name: str
    tool_call_id: str
    status: str
    depth: int
    max_turns: int
    usage: dict[str, object] = field(default_factory=dict)
    error: dict[str, object] | None = None


@dataclass(frozen=True)
class ToolExecutionResult:
    status: str
    output: dict[str, object] | None = None
    error: dict[str, object] | None = None
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentTracingConfig:
    workflow_name: str
    trace_id: str | None = None
    group_id: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)
    disabled: bool = False
    include_sensitive_data: bool = False


@dataclass(frozen=True)
class AgentResumeState:
    provider: str
    serialized_state: str
    schema_version: str | None = None
    sdk_version: str | None = None


@dataclass(frozen=True)
class AgentToolApprovalDecision:
    tool_call_id: str
    tool_name: str
    status: str
    reason: str | None = None


@dataclass(frozen=True)
class AgentToolInterruption:
    tool_call_id: str
    tool_name: str
    tool_kind: str
    arguments: dict[str, object]
    agent: AgentReference | None = None
    policy_decision: dict[str, object] = field(default_factory=dict)
    kind: str = "tool_approval"
    message: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)


class AgentToolExecutor(Protocol):
    async def authorize_agent(self, *, context: AgentExecutionContext) -> None: ...

    async def cancel_active_tools(self, *, context: AgentExecutionContext) -> None: ...

    async def review_tool_call(
        self,
        *,
        context: AgentExecutionContext,
        tool_name: str,
        arguments: dict[str, object],
    ) -> dict[str, object]: ...

    async def execute_tool(
        self,
        *,
        context: AgentExecutionContext,
        tool_name: str,
        arguments: dict[str, object],
        tool_call_id: str | None = None,
        approval_granted: bool = False,
    ) -> ToolExecutionResult: ...


@dataclass(frozen=True)
class AgentInputAttachment:
    file_id: UUID
    kind: str
    filename: str
    content_type: str
    content: bytes = field(repr=False)
    transcript: str = field(default="", repr=False)


@dataclass(frozen=True)
class AgentRunRequest:
    agent_profile: AgentProfileSnapshot
    input_text: str
    context: AgentExecutionContext
    max_turns: int = 10
    model: str | None = None
    provider: str | None = None
    base_url: str | None = None
    api_key: str | None = field(default=None, repr=False)
    model_api: str | None = None
    model_provider_credential_id: UUID | None = None
    tool_executor: AgentToolExecutor | None = None
    session: AgentSessionScope | None = None
    tracing: AgentTracingConfig | None = None
    resume_state: AgentResumeState | None = None
    approval_decisions: tuple[AgentToolApprovalDecision, ...] = ()
    handoffs: tuple[AgentInvocationConfig, ...] = ()
    agent_tools: tuple[AgentInvocationConfig, ...] = ()
    require_handoff: bool = False
    output_schema: AgentOutputSchemaSpec | None = None
    guardrails: AgentGuardrailConfig | None = None
    stream: bool = False
    event_sink: Callable[[AgentStreamEvent], None] | None = None
    lifecycle_sink: Callable[[AgentLifecycleEvent], Awaitable[None]] | None = None
    cancellation: AgentCancellation | None = None
    sdk_sandbox_client: object | None = field(default=None, repr=False)
    sdk_sandbox_state: dict[str, object] | None = field(default=None, repr=False)
    sdk_sandbox_tasks: dict[asyncio.Task[object], None] | None = field(default=None, repr=False)
    attachments: tuple[AgentInputAttachment, ...] = ()


@dataclass(frozen=True)
class AgentLifecycleEvent:
    event_type: str
    message: str = ""
    payload: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentUsageTotals:
    request_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0
    total_cost_usd: float | None = None
    raw_usage: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentModelUsageRecord:
    request_id: str
    agent: AgentReference
    model: str
    provider: str
    model_api: str | None
    usage: AgentUsageTotals


@dataclass(frozen=True)
class AgentRunResult:
    final_output: str
    final_agent: AgentReference | None = None
    raw_output: object | None = None
    events: tuple[AgentLifecycleEvent, ...] = ()
    resume_state: AgentResumeState | None = None
    interruptions: tuple[AgentToolInterruption, ...] = ()
    structured_output: AgentStructuredOutput | None = None
    stream_events: tuple[AgentStreamEvent, ...] = ()
    handoffs: tuple[AgentHandoffResult, ...] = ()
    agent_tool_calls: tuple[AgentToolCallResult, ...] = ()
    guardrail_results: tuple[AgentGuardrailResult, ...] = ()
    usage: AgentUsageTotals | None = None
    model_usages: tuple[AgentModelUsageRecord, ...] = ()
    capabilities: AgentExecutionCapabilities | None = None


@dataclass(frozen=True)
class AgentStructuredOutput:
    value: object
    schema_name: str | None = None
    schema_version: str | None = None
    validated: bool = False
