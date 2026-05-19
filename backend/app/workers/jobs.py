from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class JobType(StrEnum):
    AGENT_RUN = "agent.run"
    TASK_PLAN = "task.plan"
    RUNTIME_CLEANUP = "runtime.cleanup"
    WORKSPACE_ARCHIVE_EXPORT = "workspace.archive_export"


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
    attempt: int = 0
    max_attempts: int = 3
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def next_attempt(self) -> "JobPayload":
        return self.model_copy(update={"attempt": self.attempt + 1})

    @property
    def can_retry(self) -> bool:
        return self.attempt + 1 < self.max_attempts
