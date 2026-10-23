"""Runtime HTTP schemas, detached commands and authorized directory values."""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from opsmesh.shared.files import validate_relative_path

MAX_FILE_BYTES = 4_000_000
SANDBOX_WORKDIR = "/workspace"


class SandboxCommandCancelled(asyncio.CancelledError):
    """Cancellation acknowledged after the native command returned a known result."""

    def __init__(self, result: dict[str, object]) -> None:
        super().__init__("The native command completed while cancellation was pending")
        self.result = result


@dataclass(frozen=True)
class SandboxDirectory:
    directory_id: UUID
    host_path: Path = field(repr=False)


# Rechecked before every attach. An environment ID must be resolved from stored
# facts, never accepted as proof that the caller may mount an arbitrary host path.
class SandboxDirectoryAuthority(Protocol):
    def __call__(
        self, *, environment_id: UUID, principal_id: UUID, directory_id: UUID | None
    ) -> SandboxDirectory: ...


class EnvironmentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    directory_id: UUID | None = None
    image: str = Field(min_length=1, max_length=500, pattern=r"^[A-Za-z0-9][A-Za-z0-9._/@:-]+$")


class EnvironmentReconciliation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_key: str = Field(min_length=1, max_length=200)
    evidence: str = Field(min_length=1, max_length=4000)


class OperationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_key: str = Field(min_length=1, max_length=200)
    action: Literal["resume", "close", "command", "read", "write"]
    command: list[str] | None = Field(default=None, min_length=1, max_length=128)
    path: str | None = Field(default=None, max_length=1000)
    content_base64: str | None = Field(default=None, max_length=(MAX_FILE_BYTES + 2) // 3 * 4)
    timeout_seconds: int = Field(default=60, ge=1, le=300)

    @model_validator(mode="after")
    def validate_arguments(self) -> OperationCreate:
        if (self.command is not None) != (self.action == "command"):
            raise ValueError("Only command operations accept an argument vector")
        if (self.path is not None) != (self.action in {"read", "write"}):
            raise ValueError("Read and write operations require a relative path")
        if (self.content_base64 is not None) != (self.action == "write"):
            raise ValueError("Only write operations accept file content")
        if self.command and any(not v or "\x00" in v or len(v) > 8192 for v in self.command):
            raise ValueError("Command arguments must be bounded nonempty strings")
        if self.path is not None:
            validate_relative_path(self.path)
        if (
            self.content_base64 is not None
            and len(base64.b64decode(self.content_base64, validate=True)) > MAX_FILE_BYTES
        ):
            raise ValueError("File content exceeds the transfer limit")
        return self


class EnvironmentView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    owner_user_id: UUID
    directory_id: UUID
    image: str
    status: str
    active_operation_id: UUID | None
    active_run_id: UUID | None
    created_at: datetime
    updated_at: datetime


class OperationView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    environment_id: UUID
    action: str
    status: str
    error_code: str | None
    created_at: datetime
    updated_at: datetime


class RuntimeCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    execution_id: UUID
    environment_id: UUID
