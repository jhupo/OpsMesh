from pydantic import (
    BaseModel,
)

from opsmesh.runtime.instances.schemas import WorkspaceRuntimeResponse
from opsmesh.runtime.instances.worker_schemas import RuntimeLeaseResponse


class AdminRuntimeLeaseResponse(RuntimeLeaseResponse):
    docker_container_id: str | None = None


class AdminWorkspaceRuntimeResponse(WorkspaceRuntimeResponse):
    docker_container_id: str | None = None


class AdminForceStopRuntimeRequest(BaseModel):
    reason: str = "Force stopped by platform admin"
