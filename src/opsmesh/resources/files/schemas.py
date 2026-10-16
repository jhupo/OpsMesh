"""File, directory, grant and runtime policy HTTP contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

FilePermissionName = Literal["read", "write", "delete", "use_in_execution"]


class DirectoryView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    directory_id: UUID
    path: str
    parent_id: UUID | None
    created_at: datetime


class DirectoryCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=1000)


class FileView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_id: UUID
    directory_id: UUID
    name: str
    owner_user_id: UUID
    latest_version: int
    created_at: datetime
    updated_at: datetime


class FileCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    directory_id: UUID
    name: str = Field(min_length=1, max_length=260)


class FileVersionView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_id: UUID
    version: int
    digest: str
    size: int
    media_type: str | None
    created_by_user_id: UUID
    created_at: datetime


class GrantView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    grant_id: UUID
    grantee_user_id: UUID
    permissions: list[str]
    created_at: datetime


class GrantCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    grantee_user_id: UUID
    permissions: list[FilePermissionName] = Field(min_length=1, max_length=8)


class RuntimePolicyView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_id: UUID
    frozen_input_versions: list[int]
    relative_paths: list[str]
    output_declarations: list[dict[str, object]]
    use_in_execution: bool
    updated_at: datetime


class RuntimePolicyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    frozen_input_versions: list[int] = Field(default_factory=list, max_length=256)
    relative_paths: list[str] = Field(default_factory=list, max_length=256)
    output_declarations: list[dict[str, object]] = Field(default_factory=list, max_length=256)
    use_in_execution: bool = False


class VersionDiffView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_version: int
    to_version: int
    identical: bool
    from_size: int
    to_size: int
    size_delta: int


class FilePage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[FileView]
    total: int
