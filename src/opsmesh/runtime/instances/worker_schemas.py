from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, computed_field, field_serializer

from opsmesh.shared.contracts import TimestampedModel
from opsmesh.shared.security.redaction import redact_sensitive_payload


class RuntimeLeaseResponse(TimestampedModel):
    workspace_id: UUID
    workspace_runtime_id: UUID
    runtime_space_id: UUID | None
    docker_container_id: str | None = Field(exclude=True, repr=False)
    status: str
    lease_metadata: dict[str, object]
    acquired_at: datetime
    released_at: datetime | None

    @computed_field  # type: ignore[prop-decorator]  # Pydantic documented mypy limitation.
    @property
    def has_docker_container(self) -> bool:
        return self.docker_container_id is not None

    @field_serializer("lease_metadata")
    def _serialize_lease_metadata(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


class RuntimeCleanupResponse(BaseModel):
    stale_marked_offline: int
    deleted_records: int
    expired_worker_leases: int = 0
