"""Instance creation selects a saved definition, never a client supplied executable."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class InstanceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_id: str = Field(min_length=1, max_length=200)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    environment_id: UUID | None = None
    request_key: str = Field(min_length=1, max_length=200)


class InstanceView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    owner_user_id: UUID
    agent_id: str
    name: str
    kind: str
    status: str
    environment_id: UUID | None
    created_at: datetime
    updated_at: datetime
