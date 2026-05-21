from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from backend.app.api.schemas.common import ORMModel, TimestampedModel


class WorkspaceFileResponse(TimestampedModel):
    workspace_id: UUID
    uploaded_by_user_id: UUID | None
    filename: str
    content_type: str
    size_bytes: int
    checksum_sha256: str
    storage_key: str
    status: str
    file_metadata: dict[str, object]


class ArtifactResponse(ORMModel):
    id: UUID
    workspace_id: UUID
    task_id: UUID | None
    agent_run_id: UUID | None
    task_step_id: UUID | None
    agent_profile_id: UUID | None
    supersedes_artifact_id: UUID | None
    work_package_id: str | None
    version: int
    review_status: str
    artifact_type: str
    filename: str
    content_type: str
    size_bytes: int
    checksum_sha256: str
    storage_key: str
    artifact_metadata: dict[str, object]
    created_at: datetime


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
