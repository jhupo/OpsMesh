from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from opsmesh.shared.contracts import FromAttributesContract, TimestampedContract


class RuntimeTemplateResponse(FromAttributesContract):
    id: UUID
    name: str
    image: str
    sandbox_backend_id: str
    sandbox_options: dict[str, object]
    status: str
    created_at: datetime


class RuntimeEnvironmentCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    template_id: UUID
    name: str = Field(min_length=1, max_length=160)
    runtime_space_id: UUID | None = None


class WorkspaceRuntimeResponse(TimestampedContract):
    workspace_id: UUID
    runtime_template_id: UUID | None
    runtime_space_id: UUID | None
    sandbox_backend_id: str
    sandbox_options: dict[str, object]
    name: str
    status: Literal["active", "disabled", "deleted"]


class RuntimeCommandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command: list[str] = Field(min_length=1, max_length=32)


class RuntimeCommandResponse(FromAttributesContract):
    id: UUID
    workspace_id: UUID
    workspace_runtime_id: UUID
    runtime_space_id: UUID | None
    command: list[str]
    status: str
    exit_code: int | None
    stdout: str
    stderr: str
    error: str | None
    started_at: datetime | None
    completed_at: datetime | None


class RuntimeEventResponse(FromAttributesContract):
    id: UUID
    workspace_id: UUID
    workspace_runtime_id: UUID
    runtime_space_id: UUID | None
    event_type: str
    message: str
    event_metadata: dict[str, object]
    created_at: datetime
