from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class MaintenanceOperation(StrEnum):
    HISTORY = "history"
    AUDIT = "audit"
    LIFECYCLE = "lifecycle"
    MCP_DIAGNOSTICS = "mcp_diagnostics"


class PlatformMaintenanceRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    operation: MaintenanceOperation
    after_workspace_id: UUID | None = None
    failed_workspaces: int = Field(default=0, ge=0)


class WorkspaceMaintenanceRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    operation: MaintenanceOperation
    workspace_id: UUID


class MaintenanceWorkspacePage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    workspace_ids: list[UUID]
    next_cursor: UUID | None
