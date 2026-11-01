"""Public runtime contracts; implementation and SDK objects remain private."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime
from typing import Any, Literal, Protocol

from pydantic import Field

from opsmesh.framework.agents import (
    AgentExecutionContext,
    AgentRunCompleted,
    AgentSpecification,
    ModelUsageObservation,
    OpsMeshAgentBackend,
    PendingToolApproval,
)
from opsmesh.framework.agents import ModelSettingsOverrides as ModelSettingsOverrides
from opsmesh.framework.errors import ErrorInfo
from opsmesh.framework.typing import OpsMeshContract

from .approval_contracts import ApprovalDecisionRecord as ApprovalDecisionRecord
from .approval_contracts import RunApprovalItem as RunApprovalItem
from .approval_contracts import RunApprovalPolicy as RunApprovalPolicy
from .extension_contracts import (
    AgentInputBuilder as AgentInputBuilder,
)
from .extension_contracts import (
    AgentToolInput as AgentToolInput,
)
from .extension_contracts import (
    ApprovalPolicyDecision as ApprovalPolicyDecision,
)
from .extension_contracts import (
    ComputerFactory as ComputerFactory,
)
from .extension_contracts import (
    ComputerSafetyPolicy as ComputerSafetyPolicy,
)
from .extension_contracts import (
    HostedMCPApprovalPolicy as HostedMCPApprovalPolicy,
)
from .extension_contracts import (
    HostedMCPApprovalRequest as HostedMCPApprovalRequest,
)
from .extension_contracts import MCPFailureFormatter as MCPFailureFormatter
from .extension_contracts import MCPToolFailure as MCPToolFailure
from .extension_contracts import (
    PatchEditor as PatchEditor,
)
from .extension_contracts import (
    ShellExecutor as ShellExecutor,
)
from .extension_contracts import (
    ToolCompletion as ToolCompletion,
)
from .extension_contracts import (
    ToolCompletionDecision as ToolCompletionDecision,
)
from .extension_contracts import (
    ToolExecutionResult as ToolExecutionResult,
)
from .overrides import RunOverrides as RunOverrides
from .overrides import SandboxRunOverrides as SandboxRunOverrides
from .overrides import resolve_run_agent as resolve_run_agent
from .prompts import PromptTemplateDefinition as PromptTemplateDefinition

RunStatus = Literal[
    "queued",
    "preparing",
    "running",
    "finalizing",
    "completed",
    "waiting_approval",
    "resuming",
    "cancelled",
    "blocked",
    "failed",
]
TERMINAL_STATES = frozenset({"completed", "cancelled", "blocked", "failed"})
MetricObserver = Callable[[str, float], None]


class SubmitRunRequest(OpsMeshContract):
    principal_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    agent: AgentSpecification
    input: str | list[dict[str, Any]]
    idempotency_key: str = Field(min_length=1, max_length=200)
    metadata: dict[str, Any] = Field(default_factory=dict)
    overrides: RunOverrides = Field(default_factory=RunOverrides)


class AgentRunView(OpsMeshContract):
    id: str
    revision: int
    principal_id: str
    session_id: str
    agent: AgentSpecification
    published_agent_ref: str = ""
    published_agent_fingerprint: str = ""
    effective_agent_fingerprint: str = ""
    overrides: RunOverrides = Field(default_factory=RunOverrides)
    status: RunStatus
    attempt_id: str | None = None
    attempt: int = 0
    output: Any = None
    usage: ModelUsageObservation = Field(default_factory=ModelUsageObservation)
    approvals: tuple[PendingToolApproval, ...] = ()
    approval_policy: RunApprovalPolicy = Field(default_factory=RunApprovalPolicy)
    approval_requested_at: datetime | None = None
    approval_reviews: dict[str, ApprovalDecisionRecord] = Field(default_factory=dict)
    has_checkpoint: bool = False
    has_final_outcome: bool = False
    cancel_requested: bool = False
    error: ErrorInfo | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class RunStreamEvent(OpsMeshContract):
    run_id: str
    attempt_id: str | None
    seq: int
    kind: str
    payload: dict[str, Any] = Field(default_factory=dict)


class RunContext(Protocol):
    run_id: str
    session_id: str
    principal_id: str


class SessionClaims(Protocol):
    async def claim(self, session_id: str, run_id: str) -> None: ...
    async def release(self, session_id: str, run_id: str) -> None: ...
    async def active_claims(self) -> dict[str, str]: ...


class GuardrailDecision(OpsMeshContract):
    allowed: bool
    info: dict[str, Any] = Field(default_factory=dict)


Guardrail = Callable[[AgentExecutionContext, Any], Awaitable[GuardrailDecision] | GuardrailDecision]
Hook = Callable[[AgentExecutionContext, str, dict[str, Any]], Awaitable[None] | None]
Instruction = Callable[[AgentExecutionContext, AgentSpecification], Awaitable[str] | str]
PromptFactory = Callable[
    [AgentExecutionContext, AgentSpecification], Awaitable[dict[str, Any]] | dict[str, Any]
]
Visibility = Callable[[AgentExecutionContext, dict[str, Any]], Awaitable[bool] | bool]


class HandoffHistory(OpsMeshContract):
    input_history: str | tuple[dict[str, Any], ...]
    prior_items: tuple[dict[str, Any], ...] = ()
    new_items: tuple[dict[str, Any], ...] = ()


class HandoffHistorySelection(OpsMeshContract):
    input_history: str | tuple[dict[str, Any], ...] | None = None
    prior_indices: tuple[int, ...] | None = None
    new_indices: tuple[int, ...] | None = None
    preserve_history: bool = True


HandoffFilter = Callable[
    [AgentExecutionContext, HandoffHistory],
    Awaitable[HandoffHistorySelection] | HandoffHistorySelection,
]
OutputExtractor = Callable[[AgentExecutionContext, AgentRunCompleted], Awaitable[str] | str]
BackendFactory = Callable[[], OpsMeshAgentBackend]
ContextFactory = Callable[[AgentRunView], Awaitable[AgentExecutionContext]]
Finalizer = Callable[[AgentRunView, AgentRunCompleted], Awaitable[dict[str, Any]]]


class RuntimeExtensions(Protocol):
    def register_mcp_failure_formatter(self, ref: str, callback: MCPFailureFormatter) -> None: ...
    def mcp_failure_formatter(self, ref: str) -> MCPFailureFormatter: ...
    def register_prompt_template(self, template: PromptTemplateDefinition) -> None: ...
    def prompt_template(self, ref: str) -> PromptTemplateDefinition: ...
    def register_output_type(self, ref: str, callback: Any) -> None: ...
    def output_type(self, ref: str) -> Any: ...
    def register_tool_completion(self, ref: str, callback: ToolCompletion) -> None: ...
    def tool_completion(self, ref: str) -> ToolCompletion: ...
    def register_agent_input(self, ref: str, callback: AgentInputBuilder) -> None: ...
    def agent_input(self, ref: str) -> AgentInputBuilder: ...
    def register_mcp_approval(self, ref: str, callback: HostedMCPApprovalPolicy) -> None: ...
    def mcp_approval(self, ref: str) -> HostedMCPApprovalPolicy: ...
    def register_shell(self, ref: str, callback: ShellExecutor) -> None: ...
    def shell(self, ref: str) -> ShellExecutor: ...
    def register_patch(self, ref: str, callback: PatchEditor) -> None: ...
    def patch(self, ref: str) -> PatchEditor: ...
    def register_computer(self, ref: str, callback: ComputerFactory) -> None: ...
    def computer(self, ref: str) -> ComputerFactory: ...
    def register_computer_safety(self, ref: str, callback: ComputerSafetyPolicy) -> None: ...
    def computer_safety(self, ref: str) -> ComputerSafetyPolicy: ...
    def register_backend(self, name: str, factory: BackendFactory) -> None: ...
    def register_guardrail(self, ref: str, callback: Guardrail) -> None: ...
    def register_hook(self, ref: str, callback: Hook) -> None: ...
    def register_instruction(self, ref: str, callback: Instruction) -> None: ...
    def register_prompt(self, ref: str, callback: PromptFactory) -> None: ...
    def register_visibility(self, ref: str, callback: Visibility) -> None: ...
    def register_handoff_filter(self, ref: str, callback: HandoffFilter) -> None: ...
    def register_output_extractor(self, ref: str, callback: OutputExtractor) -> None: ...
    def backend(self, name: str) -> OpsMeshAgentBackend: ...
    def guardrail(self, ref: str) -> Guardrail: ...
    def hook(self, ref: str) -> Hook: ...
    def instruction(self, ref: str) -> Instruction: ...
    def prompt(self, ref: str) -> PromptFactory: ...
    def visibility(self, ref: str) -> Visibility: ...
    def handoff_filter(self, ref: str) -> HandoffFilter: ...
    def output_extractor(self, ref: str) -> OutputExtractor: ...
    def freeze(self) -> None: ...


class AgentRuntime(Protocol):
    async def submit(self, request: SubmitRunRequest) -> AgentRunView: ...
    async def execute(self, run_id: str, *, launch_token: str | None = None) -> AgentRunView: ...
    async def get_run(self, run_id: str) -> AgentRunView: ...
    async def find_submission(
        self, principal_id: str, idempotency_key: str
    ) -> AgentRunView | None: ...
    async def get_run_input(self, run_id: str) -> str | list[dict[str, Any]]: ...
    async def list_runs(
        self,
        principal_id: str | None = None,
        *,
        statuses: tuple[str, ...] = (),
        session_id: str | None = None,
        limit: int = 1000,
        offset: int = 0,
        after_id: str | None = None,
    ) -> list[AgentRunView]: ...
    async def count_runs(self, statuses: tuple[str, ...]) -> int: ...
    def start(
        self, run_id: str, *, launch_token: str | None = None
    ) -> asyncio.Task[AgentRunView]: ...
    async def close(self) -> None: ...
    async def cancel(self, run_id: str, *, launch_token: str | None = None) -> AgentRunView: ...
    async def list_approvals(self, run_id: str) -> tuple[PendingToolApproval, ...]: ...
    async def decide(
        self,
        run_id: str,
        approval_id: str,
        approved: bool,
        expected_revision: int,
        *,
        actor_id: str | None = None,
        reason: str = "",
    ) -> AgentRunView: ...
    async def set_approval_policy(
        self,
        run_id: str,
        policy: RunApprovalPolicy,
        expected_revision: int,
        *,
        actor_id: str,
    ) -> AgentRunView: ...
    async def expire_approvals(self, run_id: str, expected_revision: int) -> AgentRunView: ...
    async def resume(self, run_id: str, expected_revision: int) -> AgentRunView: ...
    async def recover(self, run_id: str, expected_revision: int) -> AgentRunView: ...
    async def recover_stale(self) -> list[AgentRunView]: ...
    async def reconcile_session(self, run_id: str) -> bool: ...
    async def claim_worker_launch(
        self, run_id: str, token: str, lease_seconds: float = 30
    ) -> bool: ...
    async def renew_worker_launch(
        self, run_id: str, token: str, lease_seconds: float = 30
    ) -> bool: ...
    async def worker_exited(
        self, run_id: str, exit_code: int, *, launch_token: str | None = None
    ) -> AgentRunView: ...
    def subscribe(self, run_id: str, after_seq: int = 0) -> AsyncIterator[RunStreamEvent]: ...
