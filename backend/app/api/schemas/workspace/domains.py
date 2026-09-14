from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_serializer

from backend.app.api.schemas.common import TimestampedModel
from backend.app.api.schemas.orchestration.tasks.overview import TaskResponse
from backend.app.core.security.redaction import redact_sensitive_payload
from backend.app.domains.workspace.domains.contracts import (
    DomainItemCreateRequest,
    DomainProjectCreateRequest,
    ReviewCommentCreateRequest,
    RevisionRequestCreateRequest,
)

__all__ = [
    "DomainProjectCreateRequest",
    "DomainProjectResponse",
    "DomainItemCreateRequest",
    "DomainItemResponse",
    "ReviewCommentCreateRequest",
    "ReviewCommentResponse",
    "RevisionRequestCreateRequest",
    "RevisionRequestResponse",
    "TaskViewResponse",
]


class DomainProjectResponse(TimestampedModel):
    workspace_id: UUID
    agent_team_id: UUID | None
    domain_type: str
    name: str
    description: str
    status: str
    state: dict[str, object]

    @field_serializer("state")
    def _serialize_state(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


class DomainItemResponse(TimestampedModel):
    workspace_id: UUID
    domain_project_id: UUID | None
    task_id: UUID | None
    parent_item_id: UUID | None
    item_type: str
    title: str
    status: str
    order_index: int
    content: dict[str, object]
    state: dict[str, object]

    @field_serializer("content", "state")
    def _serialize_metadata(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


class ReviewCommentResponse(TimestampedModel):
    workspace_id: UUID
    task_id: UUID
    domain_item_id: UUID | None
    author_user_id: UUID | None
    author_agent_run_id: UUID | None
    body: str
    status: str
    metadata: dict[str, object] = Field(validation_alias="metadata_")

    @field_serializer("metadata")
    def _serialize_metadata(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


class RevisionRequestResponse(TimestampedModel):
    workspace_id: UUID
    task_id: UUID
    domain_item_id: UUID | None
    requested_by_user_id: UUID | None
    assigned_agent_profile_id: UUID | None
    instruction: str
    status: str
    payload: dict[str, object]
    resolved_at: datetime | None

    @field_serializer("payload")
    def _serialize_payload(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


class TaskViewResponse(BaseModel):
    task: TaskResponse
    domain_project: DomainProjectResponse | None
    domain_items: list[DomainItemResponse]
    review_comments: list[ReviewCommentResponse]
    revision_requests: list[RevisionRequestResponse]
