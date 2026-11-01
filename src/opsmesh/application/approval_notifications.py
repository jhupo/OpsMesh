"""Approval deadlines, reminders and notification retries."""

from datetime import UTC, datetime, timedelta

from opsmesh.framework.errors import OpsMeshError
from opsmesh.modules.runtime.api import AgentRuntime
from opsmesh.modules.runtime.queries import iter_runs

from .ports import ApplicationTriggerServices, NotificationObserver


class ApprovalNotificationCoordinator:
    def __init__(
        self,
        runtime: AgentRuntime,
        triggers: ApplicationTriggerServices,
        notifications: NotificationObserver,
    ) -> None:
        self.runtime, self.triggers, self.notifications = runtime, triggers, notifications

    async def tick(self) -> None:
        await self.triggers.reconcile()
        async for run in iter_runs(self.runtime, statuses=("waiting_approval",)):
            if run.status != "waiting_approval" or run.approval_requested_at is None:
                continue
            deadline = run.approval_requested_at + timedelta(
                seconds=run.approval_policy.timeout_seconds
            )
            if datetime.now(UTC) >= deadline:
                try:
                    await self.runtime.expire_approvals(run.id, run.revision)
                except OpsMeshError as error:
                    if error.code != "CONFLICT":
                        raise
            elif datetime.now(UTC) >= run.approval_requested_at + timedelta(
                seconds=run.approval_policy.timeout_seconds / 2
            ):
                recipients = set(run.approval_policy.approver_ids)
                recipients.add(run.principal_id)
                for recipient in recipients:
                    await self.notifications.observe(
                        event_id=f"{run.id}:approval-reminder:{run.approval_requested_at.isoformat()}",
                        event_type="runtime.approval.reminder",
                        owner_id=recipient,
                        resource_id=run.id,
                        status="waiting_approval",
                    )
        await self.notifications.tick()
