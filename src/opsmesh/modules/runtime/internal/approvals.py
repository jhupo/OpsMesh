"""Approval review policy and durable decisions, independent of worker execution."""

from __future__ import annotations

import time
from datetime import UTC, datetime

from opsmesh.framework.agents import PendingToolApproval
from opsmesh.framework.errors import OpsMeshError
from opsmesh.modules.runtime.api import AgentRunView, ApprovalDecisionRecord, RunApprovalPolicy
from opsmesh.modules.runtime.events import lifecycle_event

from .metrics import RunMetrics
from .records import RunRecordStore, run_view
from .session_recovery import RunSessionRecovery


class RunApprovalService:
    def __init__(
        self, runs: RunRecordStore, metrics: RunMetrics, sessions: RunSessionRecovery
    ) -> None:
        self.runs, self.metrics, self.sessions = runs, metrics, sessions

    async def list_approvals(self, run_id: str) -> tuple[PendingToolApproval, ...]:
        return (run_view(await self.runs.require(run_id))).approvals

    async def decide(
        self,
        run_id: str,
        approval_id: str,
        approved: bool,
        expected_revision: int,
        *,
        actor_id: str | None = None,
        reason: str = "",
    ) -> AgentRunView:
        record = await self.runs.require(run_id)
        if record.revision != expected_revision or record.data["status"] != "waiting_approval":
            raise OpsMeshError("conflict", "Run is not at this approval revision")
        approvals = [dict(item) for item in record.data["approvals"]]
        item = next((item for item in approvals if item["id"] == approval_id), None)
        if item is None:
            raise OpsMeshError("not_found", "Approval does not exist")
        if item.get("approved") is not None:
            raise OpsMeshError("conflict", "Approval already decided")
        policy = RunApprovalPolicy.model_validate(record.data.get("approval_policy", {}))
        started_at = record.data.get("approval_wait_started_at", record.updated_at.timestamp())
        if time.time() >= started_at + policy.timeout_seconds:
            raise OpsMeshError("APPROVAL_EXPIRED", "Approval review deadline has passed")
        if policy.require_reason and not reason.strip():
            raise OpsMeshError("INVALID_REQUEST", "Approval decision requires a reason")
        review = ApprovalDecisionRecord(
            actor_id=actor_id or record.data["principal_id"],
            approved=approved,
            reason=reason,
            decided_at=datetime.now(UTC),
        )
        item["approved"] = approved
        decisions = dict(record.data["decisions"], **{approval_id: approved})
        record = await self.runs.update(
            run_id,
            dict(
                record.data,
                approvals=approvals,
                decisions=decisions,
                approval_reviews={
                    **record.data.get("approval_reviews", {}),
                    approval_id: review.model_dump(mode="json"),
                },
            ),
            expected_revision,
            events=[
                lifecycle_event(
                    run_id,
                    "approval_decided",
                    {
                        "approval_id": approval_id,
                        "approved": approved,
                        "actor_id": review.actor_id,
                        "reason": reason,
                    },
                ),
            ],
        )
        return run_view(record)

    async def set_approval_policy(
        self,
        run_id: str,
        policy: RunApprovalPolicy,
        expected_revision: int,
        *,
        actor_id: str,
    ) -> AgentRunView:
        record = await self.runs.require(run_id)
        if record.data["status"] not in {"queued", "waiting_approval"} or record.data.get(
            "decisions"
        ):
            raise OpsMeshError(
                "CONFLICT", "Approval policy can change only before review decisions"
            )
        updated = await self.runs.update(
            run_id,
            {**record.data, "approval_policy": policy.model_dump(mode="json")},
            expected_revision,
            events=[lifecycle_event(run_id, "approval_policy_changed", {"actor_id": actor_id})],
        )
        return run_view(updated)

    async def expire_approvals(self, run_id: str, expected_revision: int) -> AgentRunView:
        record = await self.runs.require(run_id)
        policy = RunApprovalPolicy.model_validate(record.data.get("approval_policy", {}))
        started_at = record.data.get("approval_wait_started_at", record.updated_at.timestamp())
        pending = any(item.get("approved") is None for item in record.data.get("approvals", []))
        if record.data["status"] != "waiting_approval" or not pending:
            raise OpsMeshError("CONFLICT", "Run has no pending approval review")
        if time.time() < started_at + policy.timeout_seconds:
            raise OpsMeshError("CONFLICT", "Approval deadline has not passed")
        error = OpsMeshError("APPROVAL_EXPIRED", "Approval review expired; execution is blocked")
        updated = await self.runs.update(
            run_id,
            {**record.data, "status": "blocked", "error": error.info.model_dump(mode="json")},
            expected_revision,
            events=[lifecycle_event(run_id, "approval_expired", {})],
        )
        self.metrics.transition(record, updated)
        await self.sessions.release(updated)
        return run_view(updated)

    async def resume(self, run_id: str, expected_revision: int) -> AgentRunView:
        record = await self.runs.require(run_id)
        if record.revision != expected_revision or record.data["status"] != "waiting_approval":
            raise OpsMeshError("conflict", "Run is not at this resumable revision")
        if not record.data.get("decisions"):
            raise OpsMeshError("approval_required", "No approval decisions have been made")
        pending = any(item.get("approved") is None for item in record.data.get("approvals", []))
        if pending:
            policy = RunApprovalPolicy.model_validate(record.data.get("approval_policy", {}))
            started_at = record.data.get("approval_wait_started_at", record.updated_at.timestamp())
            if time.time() >= started_at + policy.timeout_seconds:
                raise OpsMeshError("APPROVAL_EXPIRED", "Approval review deadline has passed")
        updated = await self.runs.update(
            run_id,
            dict(
                record.data,
                status="resuming",
                worker_launch_token=None,
                worker_launch_expires=None,
                queued_at=time.time(),
            ),
            expected_revision,
            events=[lifecycle_event(run_id, "resuming", {})],
        )
        self.metrics.transition(record, updated)
        return run_view(updated)
