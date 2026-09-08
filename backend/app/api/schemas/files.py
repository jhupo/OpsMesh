from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, computed_field, field_serializer

from backend.app.api.schemas.common import ORMModel, TimestampedModel
from backend.app.api.schemas.redaction import redact_sensitive_payload

FileSensitivity = Literal["public", "internal", "confidential", "restricted"]
FileRuntimeAccess = Literal["allowed", "denied"]


class WorkspaceFileResponse(TimestampedModel):
    workspace_id: UUID
    uploaded_by_user_id: UUID | None
    filename: str
    content_type: str
    size_bytes: int
    checksum_sha256: str
    storage_key: str = Field(exclude=True, repr=False)
    status: str
    sensitivity: FileSensitivity
    runtime_access: FileRuntimeAccess
    file_metadata: dict[str, object]

    @computed_field
    @property
    def has_storage_object(self) -> bool:
        return bool(self.storage_key)

    @field_serializer("file_metadata")
    def _serialize_file_metadata(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


class WorkspaceFileRuntimePolicyRequest(BaseModel):
    sensitivity: FileSensitivity
    runtime_access: FileRuntimeAccess


class ArtifactResponse(ORMModel):
    id: UUID
    workspace_id: UUID
    task_id: UUID | None
    agent_run_id: UUID | None
    task_step_id: UUID | None
    workspace_project_id: UUID | None
    workspace_project_output_id: UUID | None
    agent_profile_id: UUID | None
    supersedes_artifact_id: UUID | None
    work_package_id: str | None
    project_path: str | None
    version: int
    review_status: str
    artifact_type: str
    filename: str
    content_type: str
    size_bytes: int
    checksum_sha256: str
    storage_key: str = Field(exclude=True, repr=False)
    artifact_metadata: dict[str, object]
    created_at: datetime

    @computed_field
    @property
    def has_storage_object(self) -> bool:
        return bool(self.storage_key)

    @field_serializer("artifact_metadata")
    def _serialize_artifact_metadata(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


class ArtifactHistoryResponse(BaseModel):
    task_id: UUID
    work_package_id: str
    items: list[ArtifactResponse]
    total: int
    latest_artifact_id: UUID | None
    latest_version: int | None


class FinalOutputArtifactHistoryResponse(BaseModel):
    task_id: UUID
    final_output: dict[str, object] | None
    final_work_package_ids: list[str]
    items: list[ArtifactResponse]
    total: int
    latest_artifact_id: UUID | None
    latest_version: int | None
