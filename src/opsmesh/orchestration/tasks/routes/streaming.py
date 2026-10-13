from uuid import UUID

from fastapi import Request
from sqlalchemy.orm import Session, sessionmaker

from opsmesh.identity.auth.service import AuthenticationService
from opsmesh.identity.authorization.context import AuthenticatedUser
from opsmesh.identity.authorization.permissions import WorkspaceAction
from opsmesh.identity.authorization.resources import (
    ResourceAction,
    ResourceAuthorizationService,
    ResourceKind,
)
from opsmesh.identity.authorization.service import WorkspaceAuthorizationService
from opsmesh.orchestration.tasks.async_events import AsyncPostgresTaskEventReader
from opsmesh.orchestration.tasks.events import TaskEvent
from opsmesh.orchestration.tasks.observation.live_status import TaskLiveStatusService

STREAM_TERMINAL_TASK_STATUSES = {"completed", "failed", "cancelled"}


def read_task_stream_snapshot(
    sessions: sessionmaker[Session],
    user: AuthenticatedUser,
    workspace_id: UUID,
    task_id: UUID,
    after_sequence: int,
    message_limit: int,
) -> dict[str, object] | None:
    with sessions() as session:
        live_user = AuthenticationService(session).refresh_authenticated_user(user)
        WorkspaceAuthorizationService(session).require_workspace(
            user_id=live_user.user_id,
            workspace_id=workspace_id,
            action=WorkspaceAction.READ,
            authenticated_user=live_user,
        )
        ResourceAuthorizationService(session, live_user).require(
            workspace_id, ResourceKind.TASK, task_id, ResourceAction.READ
        )
        return TaskLiveStatusService(session).get_status(
            workspace_id=workspace_id,
            task_id=task_id,
            after_sequence=after_sequence,
            message_limit=message_limit,
        )


def get_task_event_reader(request: Request) -> AsyncPostgresTaskEventReader:
    sessions = getattr(request.app.state, "task_event_sessions", None)
    if sessions is None:
        raise RuntimeError("Task event database sessions are not configured")
    return AsyncPostgresTaskEventReader(
        sessions=sessions, notifications=request.app.state.task_event_notifications
    )


def _task_event_stream_payload(task_event: TaskEvent, cursor: int) -> dict[str, object]:
    payload: dict[str, object] = {
        **task_event.as_dict(),
        "event_id": task_event.event_id or task_event.id,
        "stream": {
            "cursor": cursor,
            "event_cursor": task_event.id,
        },
    }
    return payload


def _snapshot_messages(snapshot: dict[str, object]) -> list[dict[str, object]]:
    messages = snapshot.get("recent_messages")
    if not isinstance(messages, list):
        return []
    return [message for message in messages if isinstance(message, dict)]


def _message_sequence(message: dict[str, object], default: int) -> int:
    sequence = message.get("sequence")
    return sequence if isinstance(sequence, int) else default


def _task_stream_complete(snapshot: dict[str, object]) -> bool:
    task = snapshot.get("task")
    summary = snapshot.get("summary")
    if not isinstance(task, dict) or not isinstance(summary, dict):
        return False
    status_value = task.get("status")
    active_runs = summary.get("active_run_count")
    return status_value in STREAM_TERMINAL_TASK_STATUSES and active_runs == 0
