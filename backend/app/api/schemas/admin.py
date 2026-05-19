from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from backend.app.api.schemas.common import ORMModel, TimestampedModel
from backend.app.api.schemas.operations import WorkerLeaseResponse, WorkerNodeResponse
from backend.app.api.schemas.runtime_spaces import RuntimeSpaceResponse
from backend.app.api.schemas.workspaces import WorkspaceResponse


class AdminOverviewResponse(BaseModel):
    workspaces_total: int
    workspaces_active: int
    workers_total: int
    workers_online: int
    workers_draining: int
    active_worker_leases: int
    runtime_spaces_total: int
    runtime_spaces_quarantined: int
    critical_security_events: int


class AdminRuntimeSpaceResponse(RuntimeSpaceResponse):
    pass


class AdminWorkerNodeResponse(WorkerNodeResponse):
    pass


class AdminWorkerLeaseResponse(WorkerLeaseResponse):
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
