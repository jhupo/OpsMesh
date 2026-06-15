from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, computed_field, field_serializer

from backend.app.api.schemas.common import TimestampedModel
from backend.app.api.schemas.redaction import redact_sensitive_payload


class WorkerHeartbeatRequest(BaseModel):
    workspace_id: UUID | None = None
    worker_id: str = Field(min_length=1, max_length=160)
    worker_type: str = Field(default="cloud", max_length=80)
    status: str = Field(default="online", max_length=32)
    queue_name: str = Field(default="agent_runs", max_length=120)
    worker_version: str | None = Field(default=None, max_length=120)
    hostname: str | None = Field(default=None, max_length=255)
    capacity: dict[str, object] = Field(default_factory=dict)
    details: dict[str, object] = Field(default_factory=dict)


class WorkerHeartbeatResponse(TimestampedModel):
    workspace_id: UUID | None
    worker_id: str
    worker_type: str
    status: str
    queue_name: str
    details: dict[str, object]
    last_seen_at: datetime

    @field_serializer("details")
    def _serialize_details(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


class WorkerNodeResponse(TimestampedModel):
    worker_id: str
    worker_type: str
    status: str
    queue_name: str
    worker_version: str | None
    hostname: str | None
    capacity: dict[str, object]
    details: dict[str, object]
    drain_requested_at: datetime | None
    last_seen_at: datetime

    @field_serializer("capacity", "details")
    def _serialize_worker_metadata(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


WorkerControlStatus = Literal[
    "online",
    "offline",
    "maintenance",
    "disabled",
    "quarantined",
    "draining",
]


class WorkerStatusUpdateRequest(BaseModel):
    status: WorkerControlStatus
    reason: str | None = Field(default=None, max_length=240)


class WorkerLeaseResponse(TimestampedModel):
    workspace_id: UUID
    worker_id: str
    queue_name: str
    job_id: UUID
    job_type: str
    resource_id: UUID
    status: str
    attempt: int
    lease_metadata: dict[str, object]
    started_at: datetime
    last_heartbeat_at: datetime | None
    finished_at: datetime | None

    @field_serializer("lease_metadata")
    def _serialize_lease_metadata(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


class RuntimeLeaseResponse(TimestampedModel):
    workspace_id: UUID
    workspace_runtime_id: UUID
    runtime_space_id: UUID | None
    docker_container_id: str | None = Field(exclude=True, repr=False)
    status: str
    lease_metadata: dict[str, object]
    acquired_at: datetime
    released_at: datetime | None

    @computed_field
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
