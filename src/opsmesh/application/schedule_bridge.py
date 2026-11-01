"""Validate scheduled execution resources and record pre-submission failures."""

from opsmesh.modules.accounts.api import AccountDirectory
from opsmesh.modules.agents.api import AgentDefinitionCatalog
from opsmesh.modules.bots.api import BotAccounts
from opsmesh.modules.runtime.api import resolve_run_agent
from opsmesh.modules.scheduling.api import PersistentSchedule, ScheduleOccurrence
from opsmesh.modules.sessions.api import Conversations
from opsmesh.modules.workspaces.api import WorkspaceService

from .authorization import PlatformAuthorization
from .ports import NotificationObserver
from .sandbox_bridge import SandboxWorkspaceBridge


class SchedulePlatformBridge:
    def __init__(
        self,
        *,
        accounts: AccountDirectory,
        agents: AgentDefinitionCatalog,
        authorization: PlatformAuthorization,
        bots: BotAccounts,
        notifications: NotificationObserver,
        sandbox_bridge: SandboxWorkspaceBridge,
        sessions: Conversations,
        workspaces: WorkspaceService,
    ) -> None:
        self.accounts = accounts
        self.agents = agents
        self.authorization = authorization
        self.bots = bots
        self.notifications = notifications
        self.sandbox_bridge = sandbox_bridge
        self.sessions = sessions
        self.workspaces = workspaces

    async def validate(self, schedule: PersistentSchedule, actor_id: str) -> None:
        for identity in {actor_id, schedule.principal_id}:
            if schedule.reply_target:
                await self.bots.get_account(schedule.reply_target.bot.bot_id, actor_id=identity)
                await self.authorization.check(
                    identity, "bots.send", schedule.reply_target.bot.bot_id
                )
            if schedule.session_id:
                session = await self.sessions.get(schedule.session_id)
                await self.accounts.authorize(
                    await self.accounts.get_principal(identity),
                    "run:submit",
                    session.id,
                    owner_id=session.owner_id,
                )
            if schedule.agent_revision:
                effective = resolve_run_agent(
                    await self.agents.get_revision(schedule.agent_revision),
                    schedule.run_overrides,
                )
                if effective.workspace_id:
                    await self.authorization.workspace(identity, effective.workspace_id, write=True)
                if effective.sandbox and effective.sandbox.snapshot_ref:
                    snapshot = await self.workspaces.get_snapshot(effective.sandbox.snapshot_ref)
                    await self.authorization.workspace(identity, snapshot.workspace_id, write=True)
                if effective.sandbox and effective.sandbox.session_ref:
                    workspace = await self.sandbox_bridge.authorize_session_source(
                        identity, effective.sandbox
                    )
                    await self.authorization.workspace(identity, workspace, write=True)

    async def observe(self, occurrence: ScheduleOccurrence, schedule: PersistentSchedule) -> None:
        if occurrence.status == "failed" and not occurrence.run_id and schedule.notify_on_failure:
            await self.notifications.observe(
                event_id=f"schedule:{occurrence.id}:failed",
                event_type="scheduling.occurrence.failed",
                owner_id=schedule.principal_id,
                resource_id=schedule.id,
                status="failed",
                default_target=schedule.reply_target,
            )
