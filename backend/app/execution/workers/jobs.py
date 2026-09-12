from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from backend.app.core.trace_context import (
    TraceContext,
    child_trace_context,
    trace_context_from_metadata,
)


class JobType(StrEnum):
    AGENT_RUN = "agent.run"
    MCP_TOOL_EXECUTION = "mcp.tool_execution"
    TASK_PLAN = "task.plan"
    TEAM_EXECUTION_LOOP = "team.execution_loop"
    RUNTIME_CONTROL = "runtime.control"
    RUNTIME_CLEANUP = "runtime.cleanup"
    WORKSPACE_ARCHIVE_EXPORT = "workspace.archive_export"
    MEMORY_INDEX = "memory.index"
    MEMORY_EMBED = "memory.embed"
    WEBHOOK_DELIVERY = "webhook.delivery"
    SECRET_REENCRYPT = "secret.reencrypt"
    MODEL_PROVIDER_HEALTH_CHECK = "model_provider.health_check"
    AUDIT_INTEGRITY_CHECK = "audit.integrity_check"


class JobPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: UUID = Field(default_factory=uuid4)
    workspace_id: UUID
    job_type: JobType
    resource_id: UUID
    idempotency_key: str
    requested_by_user_id: UUID | None = None
    requested_by_agent_run_id: UUID | None = None
    routing: dict[str, object] = Field(default_factory=dict)
    priority: int = 0
    attempt: int = 0
    max_attempts: int = 3
    last_error: str | None = None
    last_error_type: str | None = None
    last_failed_at: datetime | None = None
    trace_id: str | None = None
    span_id: str | None = None
    parent_span_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def next_attempt(self, error: BaseException | str | None = None) -> "JobPayload":
        update: dict[str, object] = {"attempt": self.attempt + 1}
        if error is not None:
            update |= {
                "last_error": str(error),
                "last_error_type": type(error).__name__
                if isinstance(error, BaseException)
                else "Error",
                "last_failed_at": datetime.now(UTC),
            }
        return self.model_copy(update=update)

    @property
    def can_retry(self) -> bool:
        return self.attempt + 1 < self.max_attempts

    def trace_context(self) -> TraceContext | None:
        return trace_context_from_metadata(self.model_dump())

    def with_trace_context(self, context: TraceContext | None = None) -> "JobPayload":
        context = context or child_trace_context()
        return self.model_copy(update=context.metadata())

    def trace_metadata(self) -> dict[str, object]:
        context = self.trace_context()
        return context.metadata() if context is not None else {}
