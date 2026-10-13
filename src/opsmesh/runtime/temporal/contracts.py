from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


class ExecutionKind(StrEnum):
    CONVERSATION = "conversation"
    AGENT = "agent"
    ORCHESTRATION = "orchestration"
    TEAM_ORCHESTRATION = "team_orchestration"
    TASK_PLAN = "task_plan"
    MCP_CONTROL = "mcp_control"
    PLUGIN_DOWNLOAD = "plugin_download"
    SANDBOX_CONTROL = "sandbox_control"
    SANDBOX_CLEANUP = "sandbox_cleanup"
    ARCHIVE_EXPORT = "archive_export"
    ARCHIVE_VERIFY = "archive_verify"
    ARCHIVE_RESTORE_DRILL = "archive_restore_drill"
    MEMORY_INDEX = "memory_index"
    MEMORY_EMBED = "memory_embed"
    KNOWLEDGE_INGEST = "knowledge_ingest"
    WEBHOOK_DELIVERY = "webhook_delivery"
    SECRET_REENCRYPT = "secret_reencrypt"
    MODEL_PROVIDER_HEALTH = "model_provider_health"
    AUDIT_INTEGRITY = "audit_integrity"
    ADMIN_OPERATION = "admin_operation"
    ENTRY_EVENT = "entry_event"
    REPLY_DELIVERY = "reply_delivery"
    SCHEDULE_CONTROL = "schedule_control"
    SCHEDULE_TICK = "schedule_tick"


_PARAMETER_NAMES: dict[ExecutionKind, frozenset[str]] = {
    ExecutionKind.CONVERSATION: frozenset({"turn_id"}),
    ExecutionKind.AGENT: frozenset(),
    ExecutionKind.ORCHESTRATION: frozenset({"definition_version_id"}),
    ExecutionKind.TEAM_ORCHESTRATION: frozenset(),
    ExecutionKind.TASK_PLAN: frozenset({"revision_request_id"}),
    ExecutionKind.MCP_CONTROL: frozenset({"action", "deployment_version"}),
    ExecutionKind.PLUGIN_DOWNLOAD: frozenset(),
    ExecutionKind.SANDBOX_CONTROL: frozenset({"action"}),
    ExecutionKind.SANDBOX_CLEANUP: frozenset(),
    ExecutionKind.ARCHIVE_EXPORT: frozenset(),
    ExecutionKind.ARCHIVE_VERIFY: frozenset(),
    ExecutionKind.ARCHIVE_RESTORE_DRILL: frozenset(),
    ExecutionKind.MEMORY_INDEX: frozenset({"source_type"}),
    ExecutionKind.MEMORY_EMBED: frozenset({"embedding_generation"}),
    ExecutionKind.KNOWLEDGE_INGEST: frozenset({"source_version"}),
    ExecutionKind.WEBHOOK_DELIVERY: frozenset(),
    ExecutionKind.SECRET_REENCRYPT: frozenset({"scope"}),
    ExecutionKind.MODEL_PROVIDER_HEALTH: frozenset({"probes", "timeout_seconds"}),
    ExecutionKind.AUDIT_INTEGRITY: frozenset(),
    ExecutionKind.ADMIN_OPERATION: frozenset(),
    ExecutionKind.ENTRY_EVENT: frozenset(),
    ExecutionKind.REPLY_DELIVERY: frozenset(),
    ExecutionKind.SCHEDULE_CONTROL: frozenset({"owner_kind"}),
    ExecutionKind.SCHEDULE_TICK: frozenset({"owner_kind"}),
}


class ExecutionRequest(BaseModel):
    """An authorized durable reference, never the model input or a credential."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    execution_id: UUID = Field(default_factory=uuid4)
    workspace_id: UUID
    resource_id: UUID
    kind: ExecutionKind
    requested_by_user_id: UUID | None = None
    requested_by_agent_run_id: UUID | None = None
    parameters: dict[str, JsonValue] = Field(default_factory=dict)
    request_id: str | None = Field(default=None, max_length=80)
    trace_id: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def validate_reference_parameters(self) -> "ExecutionRequest":
        if self.parameters.keys() - _PARAMETER_NAMES[self.kind]:
            raise ValueError("Execution parameters must match the declared operation contract")
        return self

    @property
    def workflow_id(self) -> str:
        return f"opsmesh:{self.workspace_id}:{self.kind.value}:{self.execution_id}"


class DispatchOperation(StrEnum):
    START = "start"
    SIGNAL = "signal"
    CANCEL = "cancel"


class WorkflowSignal(BaseModel):
    """A durable signal references the authorized decision persisted in PostgreSQL."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    resource_id: UUID


class DispatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    dispatch_id: UUID
    workspace_id: UUID
    workflow_id: str = Field(min_length=1, max_length=255)
    operation: DispatchOperation
    execution: ExecutionRequest | None = None
    signal_name: str | None = Field(default=None, min_length=1, max_length=100)
    signal: WorkflowSignal | None = None
    traceparent: str | None = Field(
        default=None, pattern=r"^00-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}$"
    )
    tracestate: str | None = Field(default=None, max_length=512)

    @model_validator(mode="after")
    def validate_destination(self) -> "DispatchRequest":
        if not self.workflow_id.startswith(f"opsmesh:{self.workspace_id}:"):
            raise ValueError("Workflow destination is outside the dispatch workspace")
        if self.operation == DispatchOperation.START:
            if self.execution is None or self.execution.workspace_id != self.workspace_id:
                raise ValueError("Start dispatch requires a matching execution reference")
            if self.execution.workflow_id != self.workflow_id:
                raise ValueError("Execution and dispatch workflow identifiers differ")
            if self.signal_name is not None or self.signal is not None:
                raise ValueError("Start dispatch cannot contain a signal")
        elif self.operation == DispatchOperation.SIGNAL:
            if self.signal_name is None or self.signal is None or self.execution is not None:
                raise ValueError("Signal dispatch requires only a named decision reference")
        elif self.execution is not None or self.signal_name is not None or self.signal is not None:
            raise ValueError("Cancel dispatch cannot contain execution or signal data")
        return self
