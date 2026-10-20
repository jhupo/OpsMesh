"""Serializable execution Workflow and Activity contracts."""

from typing import Any
from uuid import UUID

from pydantic import Field

from opsmesh.orchestration.execution.contracts import Configuration, PreparedRun


class RunClaim(Configuration):
    prepared: PreparedRun | None = None


class RunCompletion(Configuration):
    run_id: UUID
    status: str
    output: Any = None
    artifacts: list[UUID] = Field(default_factory=list)
    error_code: str | None = None


class ApprovalCheckpoint(Configuration):
    run_id: UUID
    checkpoint_key: str
    state: str
    interruptions: list[dict[str, Any]]
