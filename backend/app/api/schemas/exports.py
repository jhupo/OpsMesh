from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class WorkspaceExportRequest(BaseModel):
    include_agents: bool = True
    include_teams: bool = True
    include_tasks: bool = True
    include_runs: bool = True
    include_files: bool = True
    include_audit_events: bool = True
    max_items_per_collection: int = Field(default=500, ge=1, le=5_000)


class WorkspaceExportManifest(BaseModel):
    workspace_id: UUID
    exported_at: datetime
    format_version: str
    included_collections: list[str]
    counts: dict[str, int]


class WorkspaceExportResponse(BaseModel):
    manifest: WorkspaceExportManifest
    workspace: dict[str, object]
    agents: list[dict[str, object]] = Field(default_factory=list)
    teams: list[dict[str, object]] = Field(default_factory=list)
    team_members: list[dict[str, object]] = Field(default_factory=list)
    tasks: list[dict[str, object]] = Field(default_factory=list)
    task_steps: list[dict[str, object]] = Field(default_factory=list)
    runs: list[dict[str, object]] = Field(default_factory=list)
    run_events: list[dict[str, object]] = Field(default_factory=list)
    files: list[dict[str, object]] = Field(default_factory=list)
    artifacts: list[dict[str, object]] = Field(default_factory=list)
    audit_events: list[dict[str, object]] = Field(default_factory=list)
