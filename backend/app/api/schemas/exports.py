from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from backend.app.api.schemas.common import ORMModel


class WorkspaceExportRequest(BaseModel):
    include_agents: bool = True
    include_teams: bool = True
    include_tasks: bool = True
    include_runs: bool = True
    include_files: bool = True
    include_audit_events: bool = True
    max_items_per_collection: int = Field(default=500, ge=1, le=5_000)


class WorkspaceArchiveExportRequest(WorkspaceExportRequest):
    include_file_bytes: bool = True
    include_artifact_bytes: bool = True
    max_bytes_per_object: int = Field(default=25 * 1024 * 1024, ge=1, le=100 * 1024 * 1024)
    max_total_bytes: int = Field(default=100 * 1024 * 1024, ge=1, le=1024 * 1024 * 1024)


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
    task_messages: list[dict[str, object]] = Field(default_factory=list)
    runs: list[dict[str, object]] = Field(default_factory=list)
    run_events: list[dict[str, object]] = Field(default_factory=list)
    files: list[dict[str, object]] = Field(default_factory=list)
    artifacts: list[dict[str, object]] = Field(default_factory=list)
    audit_events: list[dict[str, object]] = Field(default_factory=list)


class WorkspaceArchiveExportResult(BaseModel):
    filename: str
    content_type: str = "application/zip"
    content: bytes
    skipped_objects: list[str] = Field(default_factory=list)


class WorkspaceExportJobResponse(ORMModel):
    id: UUID
    workspace_id: UUID
    created_by_user_id: UUID | None
    export_type: str
    status: str
    request: dict[str, object]
    storage_key: str | None
    filename: str | None
    content_type: str | None
    size_bytes: int | None
    checksum_sha256: str | None
    error: str | None
    started_at: datetime | None
    completed_at: datetime | None
    job_metadata: dict[str, object]
    created_at: datetime
    updated_at: datetime


class WorkspaceImportRequest(BaseModel):
    export: WorkspaceExportResponse
    dry_run: bool = True
    import_agents: bool = True
    import_teams: bool = True
    import_tasks: bool = True
    name_prefix: str = Field(default="Imported ", max_length=80)
    max_items_per_collection: int = Field(default=500, ge=1, le=5_000)


class WorkspaceArchiveImportRequest(BaseModel):
    dry_run: bool = True
    import_agents: bool = True
    import_teams: bool = True
    import_tasks: bool = True
    import_file_bytes: bool = True
    import_artifact_bytes: bool = True
    name_prefix: str = Field(default="Imported ", max_length=80)
    max_items_per_collection: int = Field(default=500, ge=1, le=5_000)
    max_bytes_per_object: int = Field(default=25 * 1024 * 1024, ge=1, le=100 * 1024 * 1024)
    max_total_bytes: int = Field(default=100 * 1024 * 1024, ge=1, le=1024 * 1024 * 1024)


class WorkspaceImportResponse(BaseModel):
    dry_run: bool
    source_workspace_id: UUID
    target_workspace_id: UUID
    created_counts: dict[str, int]
    skipped_counts: dict[str, int]
    id_map: dict[str, dict[str, str]]
    warnings: list[str] = Field(default_factory=list)
