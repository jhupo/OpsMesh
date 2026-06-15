from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.agent_messages.models import AgentMessage
from backend.app.security.redaction import redact_sensitive_payload
from backend.app.teams.operations_console_utils import _preview
from backend.app.teams.runtime import TeamRuntimeState


class TeamOperationsMailboxReader:
    def __init__(self, session: Session) -> None:
        self._session = session

    def agent_payload(
        self,
        *,
        workspace_id: UUID,
        agent_team_id: UUID,
        agent_profile_id: UUID,
    ) -> dict[str, object]:
        unread_count = self._session.scalar(
            select(func.count()).where(
                AgentMessage.workspace_id == workspace_id,
                AgentMessage.agent_team_id == agent_team_id,
                AgentMessage.recipient_agent_profile_id == agent_profile_id,
                AgentMessage.read_at.is_(None),
                AgentMessage.status != "read",
            )
        )
        latest_message_at = self._session.scalar(
            select(func.max(AgentMessage.created_at)).where(
                AgentMessage.workspace_id == workspace_id,
                AgentMessage.agent_team_id == agent_team_id,
                (
                    AgentMessage.sender_agent_profile_id == agent_profile_id
                )
                | (AgentMessage.recipient_agent_profile_id == agent_profile_id),
            )
        )
        return {
            "unread_count": int(unread_count or 0),
            "latest_message_at": latest_message_at,
        }


    def team_payload(
        self,
        *,
        runtime_state: TeamRuntimeState,
        message_limit: int,
    ) -> dict[str, object]:
        if runtime_state.thread_id is None:
            return {
                "thread_id": None,
                "unread_count": 0,
                "latest_messages": [],
            }
        messages = list(
            self._session.scalars(
                select(AgentMessage)
                .where(
                    AgentMessage.workspace_id == runtime_state.workspace_id,
                    AgentMessage.thread_id == runtime_state.thread_id,
                    AgentMessage.agent_team_id == runtime_state.team_id,
                )
                .order_by(AgentMessage.created_at.desc(), AgentMessage.id.desc())
                .limit(message_limit)
            )
        )
        unread_count = self._session.scalar(
            select(func.count()).where(
                AgentMessage.workspace_id == runtime_state.workspace_id,
                AgentMessage.thread_id == runtime_state.thread_id,
                AgentMessage.agent_team_id == runtime_state.team_id,
                AgentMessage.read_at.is_(None),
                AgentMessage.status != "read",
            )
        )
        return {
            "thread_id": runtime_state.thread_id,
            "unread_count": int(unread_count or 0),
            "latest_messages": [_message_payload(message) for message in messages],
        }


def _message_payload(message: AgentMessage) -> dict[str, object]:
    return {
        "id": message.id,
        "thread_id": message.thread_id,
        "task_id": message.task_id,
        "agent_team_id": message.agent_team_id,
        "sender_agent_profile_id": message.sender_agent_profile_id,
        "recipient_agent_profile_id": message.recipient_agent_profile_id,
        "message_type": message.message_type,
        "status": message.status,
        "read_at": message.read_at,
        "created_at": message.created_at,
        "body_preview": _preview(message.body),
        "payload": redact_sensitive_payload(dict(message.payload)),
    }
