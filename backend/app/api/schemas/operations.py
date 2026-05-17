from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from backend.app.api.schemas.audit import AuditEventResponse
from backend.app.api.schemas.common import TimestampedModel
from backend.app.api.schemas.runs import AgentRunResponse, RunEventResponse


class RuntimeEventResponse(BaseModel):
    id: UUID
    workspace_id: UUID
    workspace_runtime_id: UUID
    event_type: str
    message: str
    event_metadata: dict[str, object]
    created_at: datetime


class WorkerHeartbeatRequest(BaseModel):
    workspace_id: UUID | None = None
    worker_id: str = Field(min_length=1, max_length=160)
    worker_type: str = Field(default="cloud", max_length=80)
    status: str = Field(default="online", max_length=32)
    queue_name: str = Field(default="agent_runs", max_length=120)
    details: dict[str, object] = Field(default_factory=dict)


class WorkerHeartbeatResponse(TimestampedModel):
    workspace_id: UUID | None
    worker_id: str
    worker_type: str
    status: str
    queue_name: str
    details: dict[str, object]
    last_seen_at: datetime


class QueueMetricsResponse(BaseModel):
    queue_name: str
    queued: int
    dead_letter: int
    idempotency_keys: int


class RuntimeCleanupResponse(BaseModel):
    stale_marked_offline: int
    deleted_records: int


class FailedJobInspectionResponse(BaseModel):
    runs: list[AgentRunResponse]
    total: int


class OperationsOverviewResponse(BaseModel):
    queue: QueueMetricsResponse
    failed_runs: int
    offline_runtimes: int
    workers_online: int


class AuditEventFilterResponse(BaseModel):
    items: list[AuditEventResponse]
    total: int
    limit: int
    offset: int


class RunEventFilterResponse(BaseModel):
    items: list[RunEventResponse]
    total: int
    limit: int
    offset: int
