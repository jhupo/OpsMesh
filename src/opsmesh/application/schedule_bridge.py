"""Validate scheduled execution resources and record pre-submission failures."""

from typing import TYPE_CHECKING

from opsmesh.modules.runtime.api import resolve_run_agent
from opsmesh.modules.scheduling.api import PersistentSchedule, ScheduleOccurrence

if TYPE_CHECKING:
    from .ports import ApplicationServices


class SchedulePlatformBridge:
    def __init__(self, services: "ApplicationServices") -> None:
        self.services = services

    async def validate(self, schedule: PersistentSchedule, actor_id: str) -> None:
        services = self.services
        for identity in {actor_id, schedule.principal_id}:
            if schedule.reply_target:
                await services.bots.get_account(schedule.reply_target.bot.bot_id, actor_id=identity)
                await services.authorization.check(
                    identity, "bots.send", schedule.reply_target.bot.bot_id
                )
            if schedule.session_id:
                session = await services.sessions.get(schedule.session_id)
                await services.accounts.authorize(
                    await services.accounts.get_principal(identity),
                    "run:submit",
                    session.id,
                    owner_id=session.owner_id,
                )
            if schedule.agent_revision:
                effective = resolve_run_agent(
                    await services.agents.get_revision(schedule.agent_revision),
                    schedule.run_overrides,
                )
                if effective.workspace_id:
                    await services.authorization.workspace(
                        identity, effective.workspace_id, write=True
                    )
                if effective.sandbox and effective.sandbox.snapshot_ref:
                    snapshot = await services.workspaces.get_snapshot(
                        effective.sandbox.snapshot_ref
                    )
                    await services.authorization.workspace(
                        identity, snapshot.workspace_id, write=True
                    )
                if effective.sandbox and effective.sandbox.session_ref:
                    workspace = await services.sandbox_bridge.authorize_session_source(
                        identity, effective.sandbox
                    )
                    await services.authorization.workspace(identity, workspace, write=True)

    async def observe(self, occurrence: ScheduleOccurrence, schedule: PersistentSchedule) -> None:
        if occurrence.status == "failed" and not occurrence.run_id and schedule.notify_on_failure:
            await self.services.notifications.observe(
                event_id=f"schedule:{occurrence.id}:failed",
                event_type="scheduling.occurrence.failed",
                owner_id=schedule.principal_id,
                resource_id=schedule.id,
                status="failed",
                default_target=schedule.reply_target,
            )
