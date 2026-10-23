"""HTTP authoring, publication and collaboration contracts."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from opsmesh.workflows.contracts import Permission, Visibility, WorkflowSpec


class WorkflowCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    spec: WorkflowSpec


class WorkflowUpdate(WorkflowCreate):
    expected_revision: int = Field(ge=1)


class RevisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)


class PublishRequest(RevisionRequest):
    visibility: Visibility = "private"


class WorkflowView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    owner_user_id: UUID
    name: str
    description: str
    visibility: Visibility
    status: Literal["active", "archived"]
    revision: int
    published_number: int
    created_at: datetime
    updated_at: datetime


class DraftView(BaseModel):
    revision: int
    spec: WorkflowSpec


class RevisionView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    workflow_id: UUID
    number: int
    visibility: Visibility
    digest: str
    spec: WorkflowSpec
    published_by_user_id: UUID
    created_at: datetime


class GrantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    permissions: list[Permission] = Field(min_length=1, max_length=3)


class GrantView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    workflow_id: UUID
    user_id: UUID
    permissions: list[Permission]


class InvitationCreate(GrantRequest):
    recipient_user_id: UUID
    expires_in_hours: int = Field(default=168, ge=1, le=720)


class InvitationDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["accept", "decline"]


class InvitationView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    workflow_id: UUID
    invited_by_user_id: UUID
    recipient_user_id: UUID
    permissions: list[Permission]
    status: Literal["pending", "accepted", "declined", "revoked"]
    expires_at: datetime
    decided_at: datetime | None
    created_at: datetime
