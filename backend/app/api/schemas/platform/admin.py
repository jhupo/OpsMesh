from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    Field,
    computed_field,
    field_serializer,
    field_validator,
    model_validator,
)

from backend.app.api.schemas.operations.runtimes import WorkspaceRuntimeResponse
from backend.app.api.schemas.operations.workers import RuntimeLeaseResponse
from backend.app.core.contracts import ORMModel, TimestampedModel
from backend.app.core.security.redaction import redact_sensitive_payload
from backend.app.domains.access.resources import ResourceAction, ResourceKind
from backend.app.domains.platform.admin.capability_governance import AdminCapabilityKind
from backend.app.domains.platform.admin.catalog import AdminCatalogKind
from backend.app.runtime.workers.contracts import JobPayload


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


class AdminRuntimeLeaseResponse(RuntimeLeaseResponse):
    docker_container_id: str | None = None


class AdminOperationsSummaryResponse(BaseModel):
    queue: dict[str, object]
    workers: dict[str, object]
    runtime_spaces: dict[str, object]
    approvals: dict[str, object]
    failures: dict[str, object]


class AdminSystemConfigurationResponse(BaseModel):
    settings: dict[str, object]
    recommended_resources: dict[str, int]
    configured_resources: dict[str, int]
    resource_deltas: dict[str, int]
    blocking_executor: dict[str, int | bool]
    database_pool: dict[str, int | str | None]
    redis_pool: dict[str, int | None]


class AdminReleaseAssetResponse(BaseModel):
    name: str
    browser_download_url: str
    size: int
    content_type: str | None = None
    digest: str | None = None


class AdminReleaseVersionResponse(BaseModel):
    version: str
    tag: str
    commit: str | None = None


class AdminReleaseUpdateCheckResponse(BaseModel):
    current: AdminReleaseVersionResponse
    latest: AdminReleaseVersionResponse | None
    update_available: bool
    release_url: str | None
    assets: list[AdminReleaseAssetResponse]
    cached: bool


class AdminDeadLetterJobsResponse(BaseModel):
    items: list[JobPayload]
    total: int


class AdminRequeueDeadLetterResponse(BaseModel):
    requeued: bool
    job: JobPayload | None = None


class AdminWorkspaceRuntimeResponse(WorkspaceRuntimeResponse):
    docker_container_id: str | None = None


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

    @field_serializer("event_metadata")
    def _serialize_event_metadata(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


class AdminUserResponse(TimestampedModel):
    username: str | None
    email: str
    display_name: str
    status: str
    platform_admin: bool


class AdminUserListResponse(AdminUserResponse):
    invitation_delivery_status: str | None = None
    workspace_count: int = 0
    active_workspace_count: int = 0
    resource_usage_rate: float = Field(default=0.0, ge=0.0)


class AdminUserCreateRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    display_name: str = Field(min_length=1, max_length=120)
    username: str | None = Field(default=None, min_length=1, max_length=80)
    password: str | None = Field(default=None, min_length=8, max_length=4096)
    platform_admin: bool = False

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if "@" not in normalized:
            raise ValueError("email must contain @")
        return normalized

    @field_validator("display_name")
    @classmethod
    def _normalize_display_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("display_name is required")
        return normalized


class AdminUserUpdateRequest(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    username: str | None = Field(default=None, min_length=1, max_length=80)
    platform_admin: bool | None = None

    @model_validator(mode="after")
    def _require_update(self) -> "AdminUserUpdateRequest":
        if not self.model_fields_set:
            raise ValueError("at least one user field is required")
        return self


class AdminUserCreateResponse(AdminUserResponse):
    initial_password: str


class AdminUserTokenRevokeResponse(BaseModel):
    revoked: int


class AdminUserPasswordResetResponse(AdminUserResponse):
    temporary_password: str


class AdminWorkspaceResponse(TimestampedModel):
    owner_user_id: UUID
    name: str
    slug: str
    status: str
    settings: dict[str, object]
    member_count: int
    project_count: int

    @field_serializer("settings")
    def _serialize_settings(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


class AdminWorkspaceMemberResponse(ORMModel):
    id: UUID
    workspace_id: UUID
    user_id: UUID
    email: str
    display_name: str
    role: str
    status: str
    created_at: datetime
    updated_at: datetime


class AdminUserDetailResponse(AdminUserResponse):
    workspace_memberships: list[AdminWorkspaceMemberResponse]


class AdminWorkspaceMemberUpdateRequest(BaseModel):
    role: Literal["owner", "admin", "operator", "viewer"] | None = None
    status: Literal["active", "disabled"] | None = None

    @model_validator(mode="after")
    def _require_update(self) -> "AdminWorkspaceMemberUpdateRequest":
        if self.role is None and self.status is None:
            raise ValueError("role or status is required")
        return self


class AdminWorkspaceMemberCreateRequest(BaseModel):
    user_id: UUID
    role: Literal["owner", "admin", "operator", "viewer"] = "viewer"


class AdminWorkspaceStatusUpdateRequest(BaseModel):
    status: Literal["active", "paused", "disabled", "archived"]
    reason: str = "Updated by platform admin"


class AdminProjectResponse(ORMModel):
    id: UUID
    workspace_id: UUID
    created_by_user_id: UUID | None
    name: str
    slug: str
    description: str
    input_path: str
    work_path: str
    output_path: str
    configuration: dict[str, object]
    configuration_version: int
    status: str
    created_at: datetime
    updated_at: datetime

    @field_serializer("configuration")
    def _serialize_configuration(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


class AdminProjectStatusUpdateRequest(BaseModel):
    status: Literal["active", "archived", "disabled"]
    reason: str = "Updated by platform admin"


class AdminProjectQuotaUpsertItem(BaseModel):
    quota_key: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_.-]+$")
    limit_value: int = Field(ge=0)
    unit: str = Field(default="count", min_length=1, max_length=32)


class AdminProjectQuotaUpsertRequest(BaseModel):
    quotas: list[AdminProjectQuotaUpsertItem] = Field(min_length=1, max_length=64)


class AdminProjectQuotaResponse(TimestampedModel):
    workspace_id: UUID
    project_id: UUID
    quota_key: str
    limit_value: int
    reserved_value: int
    unit: str
    status: str

    @computed_field  # type: ignore[prop-decorator]
    @property
    def available_value(self) -> int:
        return max(self.limit_value - self.reserved_value, 0)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def utilization(self) -> float:
        if self.limit_value <= 0:
            return 0.0
        return round(self.reserved_value / self.limit_value, 4)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def saturated(self) -> bool:
        return self.limit_value > 0 and self.reserved_value >= self.limit_value

    @computed_field  # type: ignore[prop-decorator]
    @property
    def over_reserved(self) -> bool:
        return self.reserved_value > self.limit_value


class AdminResourceOwnerUpdateRequest(BaseModel):
    user_id: UUID


class AdminResourceGrantUpdateRequest(BaseModel):
    user_id: UUID
    actions: frozenset[ResourceAction] = Field(default_factory=frozenset)


class AdminResourceGrantResponse(BaseModel):
    user_id: UUID
    actions: list[ResourceAction]


class AdminResourceAuthorizationResponse(BaseModel):
    workspace_id: UUID
    resource_kind: ResourceKind
    resource_id: UUID
    owner_user_id: UUID | None
    grants: list[AdminResourceGrantResponse]


class AdminAnnouncementCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=240)
    body: str = Field(default="", max_length=100_000)
    severity: Literal["info", "warning", "critical"] = "info"
    workspace_ids: list[UUID] = Field(default_factory=list, max_length=500)
    roles: list[str] = Field(default_factory=list, max_length=8)
    user_ids: list[UUID] = Field(default_factory=list, max_length=2_000)

    @field_validator("roles")
    @classmethod
    def _validate_roles(cls, value: list[str]) -> list[str]:
        allowed = {"owner", "admin", "operator", "viewer"}
        normalized = sorted({item.strip().lower() for item in value if item.strip()})
        invalid = [item for item in normalized if item not in allowed]
        if invalid:
            raise ValueError(f"unsupported workspace roles: {', '.join(invalid)}")
        return normalized

    @model_validator(mode="after")
    def _require_audience(self) -> "AdminAnnouncementCreateRequest":
        if not self.workspace_ids and not self.roles and not self.user_ids:
            raise ValueError("announcement audience is required")
        return self


class AdminAnnouncementResponse(TimestampedModel):
    created_by_user_id: UUID | None
    title: str
    body: str
    severity: str
    status: str
    audience: dict[str, object]
    published_at: datetime | None
    retracted_at: datetime | None
    recipient_count: int
    read_count: int
    workspace_ids: list[UUID]


class AdminCatalogResourceResponse(BaseModel):
    resource_kind: AdminCatalogKind
    resource_id: UUID
    workspace_id: UUID | None
    name: str | None
    status: str | None
    owner_user_id: UUID | None
    created_at: datetime | None
    updated_at: datetime | None
    metadata: dict[str, object]

    @field_serializer("metadata")
    def _serialize_metadata(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


class AdminPluginGovernanceRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=1_000)


class AdminPluginGovernanceResponse(BaseModel):
    id: UUID
    workspace_id: UUID
    plugin_key: str
    status: str
    platform_blocked: bool
    generation: int
    current_version: str


class AdminPluginPublisherTrustResponse(BaseModel):
    id: UUID
    workspace_id: UUID
    key_id: str
    plugin_key: str
    status: str


class AdminCapabilityGovernanceRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=512)


class AdminCapabilityGovernanceResponse(BaseModel):
    kind: AdminCapabilityKind
    resource_id: UUID
    workspace_id: UUID | None
    status: str
    platform_blocked: bool
    platform_previous_status: str | None


class AdminSystemLogResponse(ORMModel):
    id: UUID
    workspace_id: UUID
    actor_type: str
    actor_id: str
    user_id: UUID | None
    agent_run_id: UUID | None
    action: str
    target_type: str
    target_id: str
    request_id: str | None
    trace_id: str | None
    span_id: str | None
    worker_id: str | None
    runtime_id: str | None
    audit_metadata: dict[str, object]
    previous_hash: str | None
    current_hash: str | None
    created_at: datetime

    @field_serializer("audit_metadata")
    def _serialize_audit_metadata(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


class AdminUserStatusUpdateRequest(BaseModel):
    status: Literal["active", "disabled"]


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


class AdminPlatformPolicyEventResponse(ORMModel):
    id: UUID
    platform_policy_id: UUID
    event_type: str
    message: str
    event_metadata: dict[str, object]
    created_at: datetime

    @field_serializer("event_metadata")
    def _serialize_event_metadata(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


class AdminRiskyExecutionPolicyUpdateRequest(BaseModel):
    value: dict[str, object]
    description: str | None = None
    updated_by: str | None = "platform_admin"


class AdminWorkerControlPolicyUpdateRequest(BaseModel):
    value: dict[str, object]
    description: str | None = None
    updated_by: str | None = "platform_admin"
