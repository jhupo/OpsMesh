"""Private Session creation, metadata and history control inputs."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class SessionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instance_id: UUID


class SessionView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    instance_id: UUID | None
    generation: int
    status: str
    created_at: datetime
