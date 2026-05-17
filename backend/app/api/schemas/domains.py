from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from backend.app.api.schemas.common import TimestampedModel
from backend.app.api.schemas.tasks import TaskResponse


class DomainProjectCreateRequest(BaseModel):
    agent_team_id: UUID | None = None
    domain_type: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    state: dict[str, object] = Field(default_factory=dict)


class DomainProjectResponse(TimestampedModel):
    workspace_id: UUID
    agent_team_id: UUID | None
    domain_type: str
    name: str
    description: str
    status: str
    state: dict[str, object]


class DomainItemCreateRequest(BaseModel):
    domain_project_id: UUID | None = None
    task_id: UUID | None = None
    parent_item_id: UUID | None = None
    item_type: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=240)
    status: str = Field(default="draft", max_length=32)
    order_index: int = 0
    content: dict[str, object] = Field(default_factory=dict)
    state: dict[str, object] = Field(default_factory=dict)


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


class ReviewCommentCreateRequest(BaseModel):
    domain_item_id: UUID | None = None
    body: str = Field(min_length=1)
    metadata: dict[str, object] = Field(default_factory=dict)


class ReviewCommentResponse(TimestampedModel):
    workspace_id: UUID
    task_id: UUID
    domain_item_id: UUID | None
    author_user_id: UUID | None
    author_agent_run_id: UUID | None
    body: str
    status: str
    metadata: dict[str, object] = Field(validation_alias="metadata_")


class RevisionRequestCreateRequest(BaseModel):
    domain_item_id: UUID | None = None
    assigned_agent_profile_id: UUID | None = None
    instruction: str = Field(min_length=1)
    payload: dict[str, object] = Field(default_factory=dict)


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


class TaskViewResponse(BaseModel):
    task: TaskResponse
    domain_project: DomainProjectResponse | None
    domain_items: list[DomainItemResponse]
    review_comments: list[ReviewCommentResponse]
    revision_requests: list[RevisionRequestResponse]
