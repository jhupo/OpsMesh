from datetime import datetime
from uuid import UUID

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
    artifact_type: str
    filename: str
    content_type: str
    size_bytes: int
    checksum_sha256: str
    storage_key: str
    artifact_metadata: dict[str, object]
    created_at: datetime

