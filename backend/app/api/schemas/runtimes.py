from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, computed_field, field_serializer

from backend.app.api.schemas.common import ORMModel, TimestampedModel
from backend.app.api.schemas.redaction import redact_sensitive_payload


class RuntimeTemplateResponse(ORMModel):
    id: UUID
    name: str
    image: str
    default_limits: dict[str, object]
    default_network_policy: dict[str, object]
    status: str
    created_at: datetime

    @field_serializer("default_limits", "default_network_policy")
    def _serialize_template_policies(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


class RuntimeLimitsRequest(BaseModel):
    cpu_count: float = Field(gt=0, le=8)
    memory_mb: int = Field(ge=128, le=32_768)
    disk_mb: int = Field(ge=256, le=102_400)
    timeout_seconds: int = Field(ge=1, le=3_600)
    max_output_bytes: int = Field(default=256_000, ge=1, le=2_000_000)
    max_processes: int = Field(default=256, ge=1, le=512)


class RuntimeCreateRequest(BaseModel):
    template_id: UUID
    name: str = Field(min_length=1, max_length=160)
    runtime_space_id: UUID | None = None
    limits: RuntimeLimitsRequest | None = None
    network_disabled: bool = True


class WorkspaceRuntimeResponse(TimestampedModel):
    workspace_id: UUID
    runtime_template_id: UUID | None
    runtime_space_id: UUID | None
    runtime_provider: str
    runtime_type: str
    name: str
    status: str
    connection_status: str
    docker_container_id: str | None = Field(exclude=True, repr=False)
    limits: dict[str, object]
    network_policy: dict[str, object]
    capabilities: dict[str, object]
    last_heartbeat_at: datetime | None

    @computed_field  # type: ignore[prop-decorator]  # Pydantic documented mypy limitation.
    @property
    def has_docker_container(self) -> bool:
        return self.docker_container_id is not None

    @field_serializer("limits", "network_policy", "capabilities")
    def _serialize_runtime_policies(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


class RuntimeCommandRequest(BaseModel):
    command: list[str] = Field(min_length=1, max_length=32)


class RuntimeCommandResponse(ORMModel):
    id: UUID
    workspace_id: UUID
    workspace_runtime_id: UUID
    runtime_space_id: UUID | None
    command: list[str]
    status: str
    exit_code: int | None
    stdout: str
    stderr: str
    started_at: datetime | None
    completed_at: datetime | None


class RuntimeEventResponse(ORMModel):
    id: UUID
    workspace_id: UUID
    workspace_runtime_id: UUID
    runtime_space_id: UUID | None
    event_type: str
    message: str
    event_metadata: dict[str, object]
    created_at: datetime

    @field_serializer("event_metadata")
    def _serialize_event_metadata(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)
