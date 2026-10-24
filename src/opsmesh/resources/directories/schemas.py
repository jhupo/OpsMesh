"""Directory metadata and client file access; host paths are never public fields."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

DirectoryPermission = Literal["read", "write", "use"]


class DirectoryCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(default="Working directory", min_length=1, max_length=200)


class DirectoryUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)


class DirectoryView(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    directory_id: UUID = Field(validation_alias="id")
    owner_user_id: UUID
    name: str
    status: str
    is_default: bool
    created_at: datetime
    updated_at: datetime


class GrantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    permissions: list[DirectoryPermission] = Field(min_length=1, max_length=3)


class GrantView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    directory_id: UUID
    grantee_user_id: UUID
    permissions: list[DirectoryPermission]
    created_at: datetime
    updated_at: datetime


class FileEntryView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    path: str
    name: str
    kind: Literal["file", "directory", "symlink", "other"]
    size: int
    modified_at: datetime
