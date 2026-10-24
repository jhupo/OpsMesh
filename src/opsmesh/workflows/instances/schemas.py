"""Explicit instance creation binds existing execution resources."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from opsmesh.workflows.contracts import WorkflowBindings


class InstanceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision_id: UUID
    name: str = Field(min_length=1, max_length=200)
    request_key: str = Field(min_length=1, max_length=200)
    bindings: WorkflowBindings = Field(default_factory=WorkflowBindings)


class InstanceView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    owner_user_id: UUID
    revision_id: UUID
    name: str
    status: str
    created_at: datetime
    updated_at: datetime
