"""Platform approval review policy, independent of SDK interruption payloads."""

from datetime import datetime, timedelta
from typing import Literal

from pydantic import Field, computed_field

from opsmesh.framework.agents import PendingToolApproval
from opsmesh.framework.typing import OpsMeshContract


class RunApprovalPolicy(OpsMeshContract):
    approver_ids: tuple[str, ...] = ()
    allow_owner: bool = True
    timeout_seconds: int = Field(default=86400, ge=1, le=2_592_000)
    require_reason: bool = False


class ApprovalDecisionRecord(OpsMeshContract):
    actor_id: str
    approved: bool
    reason: str = Field(default="", max_length=2000)
    decided_at: datetime


class RunApprovalItem(OpsMeshContract):
    run_id: str
    run_revision: int
    principal_id: str
    approval: PendingToolApproval
    policy: RunApprovalPolicy
    requested_at: datetime
    status: Literal["pending", "approved", "rejected", "expired"]
    decision: ApprovalDecisionRecord | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def expires_at(self) -> datetime:
        return self.requested_at + timedelta(seconds=self.policy.timeout_seconds)
