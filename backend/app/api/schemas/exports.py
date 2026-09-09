from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, computed_field, field_serializer

from backend.app.api.schemas.common import ORMModel
from backend.app.api.schemas.redaction import redact_sensitive_payload


class WorkspaceExportRequest(BaseModel):
    include_agents: bool = True
    include_teams: bool = True
    include_tasks: bool = True
    include_runs: bool = True
    include_files: bool = True
    include_runtime_spaces: bool = True
    include_skill_installs: bool = True
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
    runtime_spaces: list[dict[str, object]] = Field(default_factory=list)
    runtime_space_quotas: list[dict[str, object]] = Field(default_factory=list)
    skill_installs: list[dict[str, object]] = Field(default_factory=list)
    audit_events: list[dict[str, object]] = Field(default_factory=list)


class WorkspaceArchiveExportResult(BaseModel):
    filename: str
    content_type: str = "application/zip"
    content: bytes
    skipped_objects: list[str] = Field(default_factory=list)
    manifest_counts: dict[str, int] = Field(default_factory=dict)


class WorkspaceExportJobResponse(ORMModel):
    id: UUID
    workspace_id: UUID
    created_by_user_id: UUID | None
    export_type: str
    status: str
    request: dict[str, object]
    storage_key: str | None = Field(exclude=True, repr=False)
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

    @computed_field  # type: ignore[prop-decorator]  # Pydantic documented mypy limitation.
    @property
    def has_storage_object(self) -> bool:
        return self.storage_key is not None

    @field_serializer("job_metadata")
    def _serialize_job_metadata(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


class WorkspaceArchiveIntegrityResponse(BaseModel):
    workspace_id: UUID
    job_id: UUID
    verified: bool
    checked_at: datetime
    checks: dict[str, bool]
    failed_checks: list[str] = Field(default_factory=list)
    metadata: dict[str, object] = Field(default_factory=dict)

    @field_serializer("metadata")
    def _serialize_metadata(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


class WorkspaceImportRequest(BaseModel):
    export: WorkspaceExportResponse
    dry_run: bool = True
    preview_token: str | None = Field(default=None, max_length=128)
    import_agents: bool = True
    import_teams: bool = True
    import_tasks: bool = True
    import_runtime_spaces: bool = True
    import_skill_installs: bool = True
    name_prefix: str = Field(default="Imported ", max_length=80)
    max_items_per_collection: int = Field(default=500, ge=1, le=5_000)
    resolutions: dict[str, dict[str, object]] = Field(default_factory=dict)


class WorkspaceArchiveImportRequest(BaseModel):
    dry_run: bool = True
    import_agents: bool = True
    import_teams: bool = True
    import_tasks: bool = True
    import_runtime_spaces: bool = True
    import_skill_installs: bool = True
    import_file_bytes: bool = True
    import_artifact_bytes: bool = True
    name_prefix: str = Field(default="Imported ", max_length=80)
    max_items_per_collection: int = Field(default=500, ge=1, le=5_000)
    max_bytes_per_object: int = Field(default=25 * 1024 * 1024, ge=1, le=100 * 1024 * 1024)
    max_total_bytes: int = Field(default=100 * 1024 * 1024, ge=1, le=1024 * 1024 * 1024)
    resolutions: dict[str, dict[str, object]] = Field(default_factory=dict)


class WorkspaceArchiveRestoreDrillRequest(BaseModel):
    import_agents: bool = True
    import_teams: bool = True
    import_tasks: bool = True
    import_runtime_spaces: bool = True
    import_skill_installs: bool = True
    import_file_bytes: bool = True
    import_artifact_bytes: bool = True
    name_prefix: str = Field(default="Restore Drill ", max_length=80)
    max_items_per_collection: int = Field(default=500, ge=1, le=5_000)
    max_bytes_per_object: int = Field(default=25 * 1024 * 1024, ge=1, le=100 * 1024 * 1024)
    max_total_bytes: int = Field(default=100 * 1024 * 1024, ge=1, le=1024 * 1024 * 1024)


class WorkspaceImportConflict(BaseModel):
    collection: str
    source_id: str
    field: str | None = None
    source_value: str | None = None
    target_value: str | None = None
    strategy: str
    severity: str = "warning"
    message: str


class WorkspaceImportResourcePreview(BaseModel):
    collection: str
    source_count: int
    create_count: int
    skip_count: int
    conflict_count: int
    action: str
    required_resolution_count: int = 0


class WorkspaceImportRequiredResolution(BaseModel):
    collection: str
    source_id: str
    field: str | None = None
    reason: str
    allowed_actions: list[str]
    message: str


class WorkspaceImportSuggestedResolution(WorkspaceImportRequiredResolution):
    resolution_key: str
    recommended_action: str
    resolution_template: dict[str, object] = Field(default_factory=dict)


class WorkspaceImportResponse(BaseModel):
    dry_run: bool
    source_workspace_id: UUID
    target_workspace_id: UUID
    preview_token: str | None = None
    created_counts: dict[str, int]
    skipped_counts: dict[str, int]
    id_map: dict[str, dict[str, str]]
    warnings: list[str] = Field(default_factory=list)
    conflict_plan: list[WorkspaceImportConflict] = Field(default_factory=list)
    resources: list[WorkspaceImportResourcePreview] = Field(default_factory=list)
    estimated_counts: dict[str, int] = Field(default_factory=dict)
    required_resolutions: list[WorkspaceImportRequiredResolution] = Field(default_factory=list)
    suggested_resolutions: list[WorkspaceImportSuggestedResolution] = Field(default_factory=list)


class WorkspaceRestoreDrillResponse(BaseModel):
    workspace_id: UUID
    job_id: UUID
    drilled_at: datetime
    passed: bool
    required_resolution_count: int = 0
    suggested_resolution_count: int = 0
    conflict_counts: dict[str, int] = Field(default_factory=dict)
    import_preview: WorkspaceImportResponse
    metadata: dict[str, object] = Field(default_factory=dict)

    @field_serializer("metadata")
    def _serialize_metadata(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


class WorkspaceDataLifecycleResponse(BaseModel):
    workspace_id: UUID
    generated_at: datetime
    export_import: dict[str, object]
    backup_policy: dict[str, object]
    retention_policy: dict[str, object]
    storage: dict[str, object]
    file_access_audit: dict[str, object]
    automation: dict[str, object]
    readiness: dict[str, object]

    @field_serializer(
        "export_import",
        "backup_policy",
        "retention_policy",
        "storage",
        "file_access_audit",
        "automation",
        "readiness",
    )
    def _serialize_metadata(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


class WorkspaceRecoveryReadinessResponse(BaseModel):
    workspace_id: UUID
    generated_at: datetime
    latest_successful_archive_export: dict[str, object] | None
    latest_archive_import: dict[str, object] | None
    latest_restore_drill: dict[str, object] | None
    latest_failed_export_job: dict[str, object] | None
    export_jobs: dict[str, object]
    retention_safety: dict[str, object]
    archive_integrity: dict[str, object]
    restore_readiness: dict[str, object]

    @field_serializer(
        "latest_successful_archive_export",
        "latest_archive_import",
        "latest_restore_drill",
        "latest_failed_export_job",
        "export_jobs",
        "retention_safety",
        "archive_integrity",
        "restore_readiness",
    )
    def _serialize_metadata(self, value: dict[str, object] | None) -> dict[str, object] | None:
        return redact_sensitive_payload(value) if value is not None else None


class WorkspaceRecoveryReadinessActionRequest(BaseModel):
    dry_run: bool = True
    actions: list[str] = Field(default_factory=list, max_length=5)
    reason: str | None = Field(default=None, max_length=1_000)
    metadata: dict[str, object] = Field(default_factory=dict)


class WorkspaceRecoveryReadinessActionResponse(BaseModel):
    workspace_id: UUID
    generated_at: datetime
    dry_run: bool
    status: str
    requested_actions: list[str]
    eligible_action_count: int
    applied_count: int
    skipped_count: int
    summary: dict[str, object]
    results: list[dict[str, object]]
    skipped: list[dict[str, object]]

    @field_serializer("summary")
    def _serialize_summary(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)

    @field_serializer("results", "skipped")
    def _serialize_items(
        self,
        value: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        return [redact_sensitive_payload(item) for item in value]


class WorkspaceRetentionRequest(BaseModel):
    include_files: bool = True
    include_export_jobs: bool = True
    include_artifacts: bool = True
    max_items: int = Field(default=100, ge=1, le=1_000)
    require_successful_backup: bool = True


class WorkspaceRetentionCandidate(BaseModel):
    resource_type: str
    resource_id: UUID
    created_at: datetime
    age_days: int
    retention_days: int
    status: str
    action: str
    filename: str | None = None
    size_bytes: int | None = None
    reason: str | None = None


class WorkspaceRetentionResponse(BaseModel):
    workspace_id: UUID
    generated_at: datetime
    dry_run: bool
    applied: bool
    policy: dict[str, object]
    blocked_reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    recommended_actions: list[str] = Field(default_factory=list)
    counts: dict[str, int]
    applied_counts: dict[str, int]
    candidates: list[WorkspaceRetentionCandidate] = Field(default_factory=list)

    @field_serializer("policy")
    def _serialize_policy(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)
