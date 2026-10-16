"""Deterministic command payloads for audit verification.

These models are the only types the verification Workflow and its Activities
exchange; they carry no I/O handles, so the Workflow sandbox never imports the
service or ORM layers.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

AUDIT_VERIFICATION_WORKFLOW = "AuditVerificationWorkflow"
VERIFICATION_BATCH_SIZE = 500


class AuditVerificationExecution(BaseModel):
    """The durable command that runs one chain verification."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    execution_id: UUID
    workflow_id: str = Field(min_length=1, max_length=255)
    workflow_name: str = AUDIT_VERIFICATION_WORKFLOW
    verification_id: UUID
    from_sequence: int = Field(ge=1)
    to_sequence: int = Field(ge=1)


class VerifyAuditSegment(BaseModel):
    """One bounded chain segment to recompute, including its anchor event."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    verification_id: UUID
    from_sequence: int = Field(ge=1)
    to_sequence: int = Field(ge=1)


class AuditSegmentResult(BaseModel):
    """How many events were verified and where the segment first broke."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    checked_count: int = Field(ge=0)
    first_broken_sequence: int | None = None


class AuditVerificationProgress(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    verification_id: UUID
    checked_count: int = Field(ge=0)


class AuditVerificationCompletion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    verification_id: UUID
    status: Literal["succeeded", "failed", "cancelled"]
    first_broken_sequence: int | None = None
    failure_reason: str | None = Field(default=None, max_length=500)


class AuditVerificationOutcome(BaseModel):
    """The durable result a finished verification reports to its starter."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    verification_id: UUID
    status: Literal["succeeded", "failed", "cancelled"]
    first_broken_sequence: int | None = None
