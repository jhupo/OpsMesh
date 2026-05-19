from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from backend.app.api.schemas.common import ORMModel, TimestampedModel
from backend.app.api.schemas.operations import (
    QueueMetricsResponse,
    WorkerLeaseResponse,
    WorkerNodeResponse,
)
from backend.app.api.schemas.runtime_spaces import RuntimeSpaceResponse
from backend.app.api.schemas.runtimes import WorkspaceRuntimeResponse
from backend.app.api.schemas.workspaces import WorkspaceResponse
from backend.app.workers.jobs import JobPayload


class AdminOverviewResponse(BaseModel):
    workspaces_total: int
    workspaces_active: int
    workers_total: int
    workers_online: int
    workers_draining: int
    active_worker_leases: int
    runtime_spaces_total: int
    runtime_spaces_quarantined: int
    runtimes_running: int
    runtimes_offline: int
    critical_security_events: int


class AdminRuntimeSpaceResponse(RuntimeSpaceResponse):
    pass


class AdminWorkerNodeResponse(WorkerNodeResponse):
    pass


class AdminWorkerLeaseResponse(WorkerLeaseResponse):
    pass


class AdminQueueMetricsResponse(QueueMetricsResponse):
    pass


class AdminOperationsSummaryResponse(BaseModel):
    queue: dict[str, object]
    workers: dict[str, object]
    runtime_spaces: dict[str, object]
    approvals: dict[str, object]
    failures: dict[str, object]


class AdminDeadLetterJobsResponse(BaseModel):
    items: list[JobPayload]
    total: int


class AdminRequeueDeadLetterResponse(BaseModel):
    requeued: bool
    job: JobPayload | None = None


class AdminWorkspaceRuntimeResponse(WorkspaceRuntimeResponse):
    pass


class AdminWorkspaceResponse(WorkspaceResponse):
    pass


class AdminSecurityEventResponse(ORMModel):
    id: UUID
    workspace_id: UUID | None
    user_id: UUID | None
    action: str
    outcome: str
    severity: str
    source_ip: str | None
    user_agent: str | None
    request_id: str | None
    path: str
    method: str
    reason: str
    event_metadata: dict[str, object]
    created_at: datetime


class AdminQuarantineRuntimeSpaceRequest(BaseModel):
    reason: str = "Quarantined by platform admin"


class AdminQuarantineRuntimeSpaceResponse(TimestampedModel):
    workspace_id: UUID
    name: str
    scope: str
    status: str
    reason: str


class AdminForceStopRuntimeRequest(BaseModel):
    reason: str = "Force stopped by platform admin"


class AdminWorkerUpdateRequest(BaseModel):
    status: str | None = None
    worker_type: str | None = None
    queue_name: str | None = None
    worker_version: str | None = None
    hostname: str | None = None
    capacity: dict[str, object] | None = None
    details: dict[str, object] | None = None
    reason: str = "Updated by platform admin"
    updated_by: str | None = "platform_admin"


class AdminPlatformPolicyResponse(TimestampedModel):
    policy_key: str
    status: str
    value: dict[str, object]
    description: str
    updated_by: str | None


class AdminRiskyExecutionPolicyUpdateRequest(BaseModel):
    value: dict[str, object]
    description: str | None = None
    updated_by: str | None = "platform_admin"
