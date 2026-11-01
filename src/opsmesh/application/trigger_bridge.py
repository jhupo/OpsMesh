"""Submit externally triggered work through the same authorized Run use case."""

from opsmesh.framework.errors import OpsMeshError
from opsmesh.modules.runtime.management import RunSubmission
from opsmesh.modules.scheduling.api import PersistentSchedule
from opsmesh.modules.sessions.api import Conversations
from opsmesh.modules.triggers.api import TriggerDefinition, TriggerInvocation

from .ports import NotificationObserver
from .schedule_bridge import SchedulePlatformBridge


class TriggerPlatformBridge:
    submit_run: RunSubmission | None = None

    def __init__(
        self,
        *,
        notifications: NotificationObserver,
        schedule_bridge: SchedulePlatformBridge,
        sessions: Conversations,
    ) -> None:
        self.notifications = notifications
        self.schedule_bridge = schedule_bridge
        self.sessions = sessions

    async def validate(self, trigger: TriggerDefinition, actor_id: str) -> None:
        await self.schedule_bridge.validate(
            PersistentSchedule(
                id=trigger.id,
                principal_id=trigger.principal_id,
                agent_revision=trigger.agent_revision,
                run_overrides=trigger.run_overrides,
                session_policy="shared" if trigger.session_policy == "shared" else "per_fire",
                session_id=trigger.session_id,
                reply_target=trigger.reply_target,
            ),
            actor_id,
        )

    async def submit(self, invocation: TriggerInvocation) -> str:
        if self.submit_run is None:
            raise OpsMeshError("CONFIG_INVALID", "Trigger Run submission is not configured")
        key = f"trigger:{invocation.trigger_id}"
        if invocation.session_policy == "per_event":
            key += f":{invocation.delivery_id}"
        session = (
            await self.sessions.get(invocation.session_id)
            if invocation.session_id
            else await self.sessions.get_or_open(
                key, invocation.principal_id, invocation.agent_revision
            )
        )
        metadata = {
            "trigger_id": invocation.trigger_id,
            "trigger_delivery_id": invocation.delivery_id,
            "event_id": invocation.event_id,
            "notify_on_failure": invocation.notify_on_failure,
            "notify_on_approval": invocation.notify_on_approval,
        }
        if invocation.reply_target:
            metadata["reply_target"] = invocation.reply_target.model_dump(mode="json")
        try:
            run = await self.submit_run(
                principal_id=invocation.principal_id,
                session_id=session.id,
                agent_ref=invocation.agent_revision,
                input=invocation.input,
                idempotency_key=invocation.idempotency_key,
                overrides=invocation.run_overrides,
                metadata=metadata,
            )
        except Exception:
            if invocation.notify_on_failure:
                await self.notifications.observe(
                    event_id=f"trigger:{invocation.delivery_id}:failed",
                    event_type="triggers.delivery.failed",
                    owner_id=invocation.principal_id,
                    resource_id=invocation.trigger_id,
                    status="failed",
                    default_target=invocation.reply_target,
                )
            raise
        return run.id
