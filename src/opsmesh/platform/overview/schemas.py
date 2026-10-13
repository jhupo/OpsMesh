from pydantic import (
    BaseModel,
)


class AdminOverviewResponse(BaseModel):
    workspaces_total: int
    workspaces_active: int
    runtime_spaces_total: int
    runtime_spaces_quarantined: int
    runtimes_active: int
    runtimes_disabled: int
    temporal_dispatch_pending: int
    critical_security_events: int
