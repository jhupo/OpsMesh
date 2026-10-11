from pydantic import (
    BaseModel,
)


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
