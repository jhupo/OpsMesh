"""Approval request, decision and status HTTP contracts."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

MAX_APPROVAL_REASON_LENGTH = 2000


class ApprovalRequestView(BaseModel):
    """One approval request; the presented status derives expiry."""

    model_config = ConfigDict(extra="forbid")

    approval_id: UUID
    kind: str
    status: str
    title: str
    summary: str
    details: dict[str, object]
    target_type: str
    target_id: str
    target_version: str | None
    approver_role: str | None
    request_key: str
    approver_user_id: UUID | None
    requester_user_id: UUID | None
    expires_at: datetime | None
    created_at: datetime
    decided_at: datetime | None
    decided_by_user_id: UUID | None
    decision_reason: str | None


class ApprovalDecisionRequest(BaseModel):
    """One decision; the idempotency key makes its retry replayable."""

    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=MAX_APPROVAL_REASON_LENGTH)
    idempotency_key: str = Field(min_length=1, max_length=200)


class ApprovalPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approvals: list[ApprovalRequestView]
    total: int
