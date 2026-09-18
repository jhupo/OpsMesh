"""Authorized external projection of the existing task event stream, with explicit replay gaps."""

import asyncio
from collections.abc import AsyncIterator
from time import monotonic
from uuid import UUID

from opsmesh_plugin_sdk.contracts import AutomationStreamEvent, EventState
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.errors import DomainError
from backend.app.domains.access.context import AuthenticatedUser
from backend.app.domains.access.errors import AuthorizationError
from backend.app.domains.access.permissions import WorkspaceAction
from backend.app.domains.access.service import AuthorizationService
from backend.app.domains.capabilities.plugins.policy import require_plugin_resource
from backend.app.domains.integrations.automation_authorization import (
    require_automation_principal,
    require_event_principal,
)
from backend.app.domains.integrations.automation_contracts import AutomationConfiguration
from backend.app.domains.integrations.automation_models import Automation, AutomationEvent
from backend.app.domains.integrations.automations import AutomationService
from backend.app.domains.orchestration.tasks.events import RedisTaskEventBus, TaskEvent


class AutomationStreamService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def authorize(
        self, workspace_id: UUID, automation_id: UUID, event_id: UUID, user: AuthenticatedUser
    ) -> tuple[AutomationEvent, AutomationConfiguration]:
        authorization = AuthorizationService(self.session)
        refreshed = authorization.refresh_authenticated_user(user)
        authorization.require_workspace(
            user_id=refreshed.user_id,
            workspace_id=workspace_id,
            action=WorkspaceAction.READ,
            authenticated_user=refreshed,
        )
        pair = self.session.execute(
            select(AutomationEvent, Automation)
            .join(Automation, Automation.id == AutomationEvent.automation_id)
            .where(
                AutomationEvent.workspace_id == workspace_id,
                AutomationEvent.automation_id == automation_id,
                AutomationEvent.id == event_id,
                Automation.workspace_id == workspace_id,
            )
            .execution_options(populate_existing=True)
        ).one_or_none()
        if pair is None:
            raise ValueError("Message stream unavailable")
        event, item = pair
        if item.status != "active" or item.created_by_user_id != refreshed.user_id:
            raise ValueError("Message stream unavailable")
        require_automation_principal(self.session, item)
        require_event_principal(self.session, item, event)
        require_plugin_resource(self.session, workspace_id, "message_trigger", item.id)
        live = AutomationConfiguration.model_validate(item.configuration)
        config = AutomationConfiguration.model_validate(event.configuration)
        if (
            config.trigger_type == "message"
            and event.input_payload.get("sender_id") not in live.allowed_senders
        ):
            raise ValueError("Message stream unavailable")
        # A live edit can reduce an accepted event's visibility, never silently expand it.
        config.stream_output_nodes = list(
            set(config.stream_output_nodes) & set(live.stream_output_nodes)
        )
        config.stream_tool_events = config.stream_tool_events and live.stream_tool_events
        return event, config

    def state(self, event: AutomationEvent) -> EventState:
        return AutomationService(self.session).event_state(
            event.workspace_id, event.automation_id, event.id
        )

    async def frames(
        self,
        *,
        bus: RedisTaskEventBus,
        workspace_id: UUID,
        automation_id: UUID,
        event_id: UUID,
        user: AuthenticatedUser,
        cursor: str,
        once: bool,
    ) -> AsyncIterator[AutomationStreamEvent]:
        deadline = monotonic() + 55
        last_state: dict[str, object] | None = None
        while True:
            try:
                event, config = self.authorize(workspace_id, automation_id, event_id, user)
            except (ValueError, DomainError, AuthorizationError):
                self.session.rollback()
                yield AutomationStreamEvent(
                    event_id=event_id, task_id=None, kind="stream.revoked", cursor=cursor
                )
                return
            state = self.state(event)
            task_id = event.task_id
            items: list[TaskEvent] = []
            reset = False
            if task_id is not None:
                reset = cursor != "0-0" and not bus.contains_cursor(workspace_id, task_id, cursor)
                if reset:
                    cursor = "0-0"
                items = bus.read(
                    workspace_id=workspace_id, task_id=task_id, after_id=cursor, count=100
                )
            projected = [
                frame for item in items if (frame := self.project(event, config, item)) is not None
            ]
            if items:
                cursor = items[-1].id
            snapshot = state.model_dump(mode="json")
            terminal = state.task_status in {
                "completed",
                "failed",
                "cancelled",
            } or state.event.status in {
                "rejected",
                "skipped",
                "completed",
                "reply_failed",
                "reply_pending",
            }
            self.session.rollback()  # Never hold a database transaction while waiting on a client.
            if reset:
                yield AutomationStreamEvent(
                    event_id=event_id,
                    task_id=task_id,
                    kind="stream.reset",
                    cursor="0-0",
                    data={"reason": "cursor_not_retained", "state": snapshot},
                )
            for frame in projected:
                yield frame
            if snapshot != last_state:
                yield AutomationStreamEvent(
                    event_id=event_id, task_id=task_id, kind="state", cursor=cursor, data=snapshot
                )
                last_state = snapshot
                if state.pending_actions:
                    yield AutomationStreamEvent(
                        event_id=event_id,
                        task_id=task_id,
                        kind="approval.required",
                        cursor=cursor,
                        data={
                            "pending_actions": [
                                action.model_dump(mode="json") for action in state.pending_actions
                            ]
                        },
                    )
            yield AutomationStreamEvent(
                event_id=event_id, task_id=task_id, kind="checkpoint", cursor=cursor
            )
            if terminal and len(items) < 100:
                if state.task_status == "completed":
                    yield AutomationStreamEvent(
                        event_id=event_id,
                        task_id=task_id,
                        kind="output.rejected" if state.output_error else "output.completed",
                        cursor=cursor,
                        data={
                            "output": state.output,
                            "error_code": state.output_error,
                            "contract_version": state.contract_version,
                        },
                    )
                elif state.task_status in {"failed", "cancelled"}:
                    yield AutomationStreamEvent(
                        event_id=event_id,
                        task_id=task_id,
                        kind="task.failed" if state.task_status == "failed" else "task.cancelled",
                        cursor=cursor,
                    )
                yield AutomationStreamEvent(
                    event_id=event_id, task_id=task_id, kind="stream.completed", cursor=cursor
                )
                return
            if once or monotonic() >= deadline:
                yield AutomationStreamEvent(
                    event_id=event_id, task_id=task_id, kind="stream.reconnect", cursor=cursor
                )
                return
            if len(items) < 100:
                await asyncio.sleep(0.25)

    @staticmethod
    def project(
        event: AutomationEvent, config: AutomationConfiguration, item: TaskEvent
    ) -> AutomationStreamEvent | None:
        if (
            item.event_type != "run.live"
            or item.task_id != event.task_id
            or item.workspace_id != event.workspace_id
        ):
            return None
        if event.result_payload:
            return None  # Control acknowledgements do not replay the target task's model previews.
        raw = item.payload
        kind = str(raw.get("kind"))
        is_text = kind in {"output.reset", "output.text"}
        if is_text and raw.get("node_id") not in config.stream_output_nodes:
            return None
        if not is_text and (
            not config.stream_tool_events
            or kind
            not in {
                "tool.started",
                "tool.completed",
                "tool.failed",
                "tool.waiting",
                "approval.required",
            }
        ):
            return None
        return AutomationStreamEvent.model_validate(
            {
                "event_id": event.id,
                "task_id": event.task_id,
                "kind": kind,
                "cursor": item.id,
                "run_id": raw.get("run_id"),
                "step_id": raw.get("step_id"),
                "attempt_id": raw.get("attempt_id"),
                "sequence": raw.get("sequence"),
                "data": raw.get("data", {}),
            }
        )
