"""Cross-run review queue and bounded delegation of tool approvals."""

from datetime import UTC, datetime, timedelta

from opsmesh.framework.agents import PendingToolApproval
from opsmesh.framework.errors import OpsMeshError
from opsmesh.modules.audit.operations import audited_operation
from opsmesh.modules.runtime.internal.management_base import RuntimeManagementBase

from .api import AgentRunView, RunApprovalItem, RunApprovalPolicy


class ApprovalManagement(RuntimeManagementBase):
    async def _review_permission(self, run: AgentRunView, actor_id: str) -> None:
        actor = await self.accounts.get_principal(actor_id)
        if not actor.active:
            raise OpsMeshError("FORBIDDEN", "User is disabled")
        policy = run.approval_policy
        if actor.is_admin or actor.id in policy.approver_ids:
            return
        if actor.id == run.principal_id and policy.allow_owner:
            await self.authorize(actor_id, run, "run:approve")
            return
        if policy.approver_ids or not policy.allow_owner:
            raise OpsMeshError("FORBIDDEN", "This review is assigned to designated approvers")
        await self.accounts.authorize(actor, "run:approve", run.id)

    async def list_approvals(
        self, run_id: str, *, actor_id: str
    ) -> tuple[PendingToolApproval, ...]:
        run = await self.runtime.get_run(run_id)
        await self._review_permission(run, actor_id)
        return run.approvals

    async def approval_queue(
        self,
        *,
        actor_id: str,
        include_decided: bool = False,
    ) -> list[RunApprovalItem]:
        actor = await self.accounts.get_principal(actor_id)
        if not actor.active:
            raise OpsMeshError("FORBIDDEN", "User is disabled")
        items = []
        for run in await self.runtime.list_runs():
            if run.status != "waiting_approval":
                continue
            try:
                await self._review_permission(run, actor_id)
            except OpsMeshError as error:
                if error.code != "FORBIDDEN":
                    raise
                continue
            requested_at = run.approval_requested_at or run.updated_at
            expired = datetime.now(UTC) >= requested_at + timedelta(
                seconds=run.approval_policy.timeout_seconds
            )
            for approval in run.approvals:
                if approval.approved is not None and not include_decided:
                    continue
                items.append(
                    RunApprovalItem(
                        run_id=run.id,
                        run_revision=run.revision,
                        principal_id=run.principal_id,
                        approval=approval,
                        policy=run.approval_policy,
                        requested_at=requested_at,
                        decision=run.approval_reviews.get(approval.id),
                        status="approved"
                        if approval.approved is True
                        else "rejected"
                        if approval.approved is False
                        else "expired"
                        if expired
                        else "pending",
                    )
                )
        return sorted(items, key=lambda item: (item.requested_at, item.run_id, item.approval.id))

    async def decide(
        self,
        run_id: str,
        approval_id: str,
        approved: bool,
        expected_revision: int,
        *,
        actor_id: str,
        reason: str = "",
    ) -> AgentRunView:
        run = await self.runtime.get_run(run_id)
        await self._review_permission(run, actor_id)
        if run.revision != expected_revision:
            raise OpsMeshError("CONFLICT", "Run changed before review")
        if len(reason) > 2000:
            raise OpsMeshError("INVALID_REQUEST", "Approval reason exceeds 2000 characters")
        async with audited_operation(
            self.audit,
            "run.approval.decide",
            run_id,
            actor_id,
            details={"approval_id": approval_id, "approved": approved, "reason": reason},
        ):
            return await self.runtime.decide(
                run_id, approval_id, approved, expected_revision, actor_id=actor_id, reason=reason
            )

    async def set_approval_policy(
        self,
        run_id: str,
        policy: RunApprovalPolicy,
        expected_revision: int,
        *,
        actor_id: str,
    ) -> AgentRunView:
        run = await self.runtime.get_run(run_id)
        await self.authorize(actor_id, run, "run:approval:manage")
        for principal_id in policy.approver_ids:
            if not (await self.accounts.get_principal(principal_id)).active:
                raise OpsMeshError("INVALID_REQUEST", "Approval reviewer is disabled")
        async with audited_operation(
            self.audit,
            "run.approval.policy",
            run_id,
            actor_id,
            details={
                "expected_revision": expected_revision,
                "policy": policy.model_dump(mode="json"),
            },
        ):
            return await self.runtime.set_approval_policy(
                run_id, policy, expected_revision, actor_id=actor_id
            )
