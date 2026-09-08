from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_serializer, model_validator

from backend.app.api.schemas.common import TimestampedModel
from backend.app.security.redaction import redact_sensitive_payload


class WorkspaceProjectCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    slug: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9][a-z0-9-]*$")
    description: str = Field(default="", max_length=2_000)
    input_path: str = Field(default="inputs", min_length=1, max_length=512)
    work_path: str = Field(default="work", min_length=1, max_length=512)
    output_path: str = Field(default="outputs", min_length=1, max_length=512)
    configuration: dict[str, object] = Field(default_factory=dict)


class WorkspaceProjectUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=2_000)
    input_path: str | None = Field(default=None, min_length=1, max_length=512)
    work_path: str | None = Field(default=None, min_length=1, max_length=512)
    output_path: str | None = Field(default=None, min_length=1, max_length=512)
    configuration: dict[str, object] | None = None

    @model_validator(mode="after")
    def _require_change(self) -> WorkspaceProjectUpdateRequest:
        if not any(
            getattr(self, field_name) is not None
            for field_name in (
                "name",
                "description",
                "input_path",
                "work_path",
                "output_path",
                "configuration",
            )
        ):
            raise ValueError("At least one project field is required")
        return self


class WorkspaceProjectResponse(TimestampedModel):
    workspace_id: UUID
    created_by_user_id: UUID | None
    name: str
    slug: str
    description: str
    input_path: str
    work_path: str
    output_path: str
    configuration: dict[str, object]
    status: str

    @field_serializer("configuration")
    def _serialize_configuration(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


class WorkspaceProjectFileCreateRequest(BaseModel):
    workspace_file_id: UUID
    project_path: str = Field(min_length=1, max_length=512)
    access_mode: Literal["read_only", "copy_on_write"] = "read_only"


class WorkspaceProjectFileResponse(TimestampedModel):
    workspace_id: UUID
    project_id: UUID
    workspace_file_id: UUID
    project_path: str
    access_mode: str
    status: str


class WorkspaceProjectOutputCreateRequest(BaseModel):
    project_path: str = Field(min_length=1, max_length=512)
    artifact_type: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_.-]+$")
    content_type: str | None = Field(default=None, min_length=1, max_length=120)
    required: bool = True
    max_bytes: int = Field(gt=0, le=1_073_741_824)


class WorkspaceProjectOutputResponse(TimestampedModel):
    workspace_id: UUID
    project_id: UUID
    project_path: str
    artifact_type: str
    content_type: str | None
    required: bool
    max_bytes: int
    status: str


class WorkspaceProjectDetailResponse(BaseModel):
    project: WorkspaceProjectResponse
    input_files: list[WorkspaceProjectFileResponse]
    outputs: list[WorkspaceProjectOutputResponse]
