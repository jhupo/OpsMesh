
from pydantic import (
    BaseModel,
)


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
