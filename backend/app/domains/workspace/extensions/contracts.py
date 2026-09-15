from uuid import UUID

from pydantic import BaseModel, Field


class DomainProjectCreateRequest(BaseModel):
    agent_team_id: UUID | None = None
    domain_type: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    state: dict[str, object] = Field(default_factory=dict)


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


class ReviewCommentCreateRequest(BaseModel):
    domain_item_id: UUID | None = None
    body: str = Field(min_length=1)
    metadata: dict[str, object] = Field(default_factory=dict)


class RevisionRequestCreateRequest(BaseModel):
    domain_item_id: UUID | None = None
    assigned_agent_profile_id: UUID | None = None
    instruction: str = Field(min_length=1)
    payload: dict[str, object] = Field(default_factory=dict)
