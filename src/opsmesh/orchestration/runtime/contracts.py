"""Serializable commands for explicitly owned sandbox operations."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from opsmesh.orchestration.contracts import ReservationRef


@dataclass(frozen=True)
class SandboxDirectory:
    directory_id: UUID
    host_path: Path


class SandboxDirectoryAuthority(Protocol):
    def __call__(
        self, *, environment_id: UUID, principal_id: UUID, directory_id: UUID | None
    ) -> SandboxDirectory:
        """Reauthorize the stored directory and return its materialized host location."""
        ...


class RuntimeStorage(Protocol):
    def reserve_storage(
        self,
        session: Session,
        *,
        user_id: UUID,
        amount: int,
        idempotency_scope: str,
        idempotency_key: str,
    ) -> ReservationRef: ...
    def settle_storage(
        self, session: Session, *, reservation_id: UUID, actual_amount: int
    ) -> object: ...
    def release_storage(self, session: Session, *, reservation_id: UUID) -> object: ...
    def reclaim_storage(self, session: Session, *, reservation_id: UUID) -> object: ...


class RuntimeCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    execution_id: UUID
    workflow_id: str
    workflow_name: str = "ExecutionRuntimeWorkflow"
    environment_id: UUID
    principal_id: UUID
    operation_id: str = Field(min_length=1, max_length=240)
    action: Literal["create", "resume", "close", "command", "read", "write"]
    arguments: dict[str, Any] = Field(default_factory=dict)
    cleanup_ref: UUID | None = None


class RuntimeResult(BaseModel):
    operation_id: str
    status: str
    result: dict[str, Any] | None = None


class MCPCommand(BaseModel):
    cleanup_ref: UUID | None = None
    execution_id: UUID
    workflow_id: str
    workflow_name: str = "ExecutionMCPWorkflow"
    connection_id: UUID
    principal_id: UUID
    operation_id: str
    action: Literal["connect", "discover", "close"]
