from uuid import UUID

from sqlalchemy import select

from backend.app.agent_messages.models import AgentMessage, AgentMessageThread
from backend.app.agent_messages.service import AgentMailboxService
from backend.app.agents.models import AgentProfile
from backend.app.api.pagination import PageParams
from backend.app.api.schemas.agent_messages import (
    AgentMessageCreateRequest,
    AgentMessageResponse,
    AgentMessageThreadCreateRequest,
)
from backend.app.tasks.models import Task
from backend.app.teams.models import AgentTeam, AgentTeamMember
from backend.app.tools.context import ToolContext
from backend.app.tools.errors import ToolResourceNotFoundError
from backend.app.tools.product_tools.events import ProductToolEventRecorder
from backend.app.tools.product_tools.normalization import (
    bounded_optional,
    optional_uuid_from_metadata,
)


class AgentMailboxProductTools(ProductToolEventRecorder):
    def send_agent_message(
        self,
        context: ToolContext,
        *,
        recipient_agent_profile_id: UUID,
        body: str,
        thread_id: UUID | None = None,
        subject: str | None = None,
        message_type: str = "message",
        payload: dict[str, object] | None = None,
        reply_to_message_id: UUID | None = None,
    ) -> dict[str, object]:
        context.require_tool("send_agent_message")
        self._append_tool_event(context, "tool.called", "send_agent_message")
        sender_agent_profile_id = self._sender_agent_profile_id(context)
        scope = agent_mailbox_scope(context)
        resolved_thread_id = thread_id or scope["thread_id"]
        thread = (
            self._require_agent_message_thread(context, resolved_thread_id)
            if resolved_thread_id is not None
            else None
        )
        if thread is None and reply_to_message_id is not None:
            raise ValueError("reply_to_message_id requires thread_id")
        self._require_agent_can_message_recipient(
            context,
            sender_agent_profile_id=sender_agent_profile_id,
            recipient_agent_profile_id=recipient_agent_profile_id,
            thread=thread,
        )
        if thread is None:
            thread = self._create_agent_message_thread(context, subject)
        message = AgentMailboxService(self._session).create_message(
            workspace_id=context.workspace_id,
            thread_id=thread.id,
            data=AgentMessageCreateRequest(
                task_id=thread.task_id if thread.task_id is not None else scope["task_id"],
                agent_team_id=thread.agent_team_id,
                sender_agent_profile_id=sender_agent_profile_id,
                recipient_agent_profile_id=recipient_agent_profile_id,
                reply_to_message_id=reply_to_message_id,
                message_type=message_type,
                body=body,
                payload=payload or {},
            ),
        )
        self._append_tool_event(context, "tool.completed", "send_agent_message")
        return {
            "thread": agent_message_thread_payload(thread),
            "message": agent_message_payload(message),
        }

    def list_agent_thread_messages(
        self,
        context: ToolContext,
        *,
        thread_id: UUID,
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
    ) -> dict[str, object]:
        context.require_tool("list_agent_thread_messages")
        self._append_tool_event(context, "tool.called", "list_agent_thread_messages")
        sender_agent_profile_id = self._sender_agent_profile_id(context)
        thread = self._require_agent_message_thread(context, thread_id)
        if not self._agent_participates_in_thread(context, thread.id, sender_agent_profile_id):
            raise ToolResourceNotFoundError("Agent message thread not found for agent")
        bounded_limit = max(1, min(100, limit))
        bounded_offset = max(0, offset)
        messages, total = AgentMailboxService(self._session).list_messages(
            workspace_id=context.workspace_id,
            thread_id=thread.id,
            page=PageParams(limit=bounded_limit, offset=bounded_offset),
            status=status,
        )
        self._append_tool_event(context, "tool.completed", "list_agent_thread_messages")
        return {
            "thread": agent_message_thread_payload(thread),
            "items": [agent_message_payload(message) for message in messages],
            "total": total,
            "limit": bounded_limit,
            "offset": bounded_offset,
        }

    def get_agent_inbox(
        self,
        context: ToolContext,
        *,
        latest_limit: int = 20,
        unread_only: bool = False,
    ) -> dict[str, object]:
        context.require_tool("get_agent_inbox")
        self._append_tool_event(context, "tool.called", "get_agent_inbox")
        agent_profile_id = self._sender_agent_profile_id(context)
        scope = agent_mailbox_scope(context)
        inbox = AgentMailboxService(self._session).get_agent_inbox(
            context.workspace_id,
            agent_profile_id,
            latest_limit=max(1, min(100, latest_limit)),
            unread_only=unread_only,
            thread_id=scope["thread_id"],
            task_id=scope["task_id"],
        )
        self._append_tool_event(context, "tool.completed", "get_agent_inbox")
        return {
            **inbox,
            "latest_messages": [
                agent_message_payload(message)
                for message in inbox["latest_messages"]
            ],
        }

    def mark_agent_message_read(
        self,
        context: ToolContext,
        *,
        message_id: UUID,
    ) -> dict[str, object]:
        context.require_tool("mark_agent_message_read")
        self._append_tool_event(context, "tool.called", "mark_agent_message_read")
        agent_profile_id = self._sender_agent_profile_id(context)
        message = self._session.get(AgentMessage, message_id)
        if (
            message is None
            or message.workspace_id != context.workspace_id
            or message.recipient_agent_profile_id != agent_profile_id
            or not message_matches_mailbox_scope(context, message)
        ):
            raise ToolResourceNotFoundError("Agent message not found for agent")
        updated = AgentMailboxService(self._session).mark_message_read(
            context.workspace_id,
            message_id,
        )
        if updated is None:
            raise ToolResourceNotFoundError("Agent message not found for agent")
        self._append_tool_event(context, "tool.completed", "mark_agent_message_read")
        return {"message": agent_message_payload(updated)}

    def _create_agent_message_thread(
        self,
        context: ToolContext,
        subject: str | None,
    ) -> AgentMessageThread:
        scope = agent_mailbox_scope(context)
        return AgentMailboxService(self._session).create_thread(
            workspace_id=context.workspace_id,
            data=AgentMessageThreadCreateRequest(
                task_id=scope["task_id"] or context.task_id,
                subject=bounded_optional(subject, 240) or "",
            ),
        )

    def _require_agent_message_thread(
        self,
        context: ToolContext,
        thread_id: UUID,
    ) -> AgentMessageThread:
        thread = self._session.get(AgentMessageThread, thread_id)
        if thread is None or thread.workspace_id != context.workspace_id:
            raise ToolResourceNotFoundError("Agent message thread not found")
        return thread

    def _require_agent_can_message_recipient(
        self,
        context: ToolContext,
        *,
        sender_agent_profile_id: UUID,
        recipient_agent_profile_id: UUID,
        thread: AgentMessageThread | None,
    ) -> None:
        sender = self._session.get(AgentProfile, sender_agent_profile_id)
        recipient = self._session.get(AgentProfile, recipient_agent_profile_id)
        if (
            sender is None
            or recipient is None
            or sender.workspace_id != context.workspace_id
            or recipient.workspace_id != context.workspace_id
        ):
            raise ToolResourceNotFoundError("Agent not found in workspace")
        task_id = (
            (thread.task_id if thread is not None else None)
            or agent_mailbox_scope(context)["task_id"]
            or context.task_id
        )
        if (
            task_id is None
            and thread is not None
            and thread.agent_team_id is not None
            and not self._agent_ids_belong_to_team(
                context.workspace_id,
                thread.agent_team_id,
                {sender_agent_profile_id, recipient_agent_profile_id},
            )
        ):
            raise ToolResourceNotFoundError("Agent not found in thread team")
        if task_id is None:
            return
        task = self._session.get(Task, task_id)
        if task is None or task.workspace_id != context.workspace_id:
            raise ToolResourceNotFoundError("Task not found")
        if task.agent_team_id is None:
            return
        if not self._agent_ids_belong_to_team(
            context.workspace_id,
            task.agent_team_id,
            {sender_agent_profile_id, recipient_agent_profile_id},
        ):
            raise ToolResourceNotFoundError("Agent not found in task team")

    def _agent_ids_belong_to_team(
        self,
        workspace_id: UUID,
        team_id: UUID,
        agent_profile_ids: set[UUID],
    ) -> bool:
        team = self._session.scalar(
            select(AgentTeam).where(
                AgentTeam.workspace_id == workspace_id,
                AgentTeam.id == team_id,
                AgentTeam.status == "active",
            )
        )
        if team is None:
            return False
        team_agent_ids = set(
            self._session.scalars(
                select(AgentTeamMember.agent_profile_id).where(
                    AgentTeamMember.workspace_id == workspace_id,
                    AgentTeamMember.agent_team_id == team_id,
                    AgentTeamMember.status == "active",
                )
            ).all()
        )
        if team.manager_agent_profile_id is not None:
            team_agent_ids.add(team.manager_agent_profile_id)
        return agent_profile_ids.issubset(team_agent_ids)

    def _agent_participates_in_thread(
        self,
        context: ToolContext,
        thread_id: UUID,
        agent_profile_id: UUID,
    ) -> bool:
        participant = self._session.scalar(
            select(AgentMessage.id).where(
                AgentMessage.workspace_id == context.workspace_id,
                AgentMessage.thread_id == thread_id,
                (
                    (AgentMessage.sender_agent_profile_id == agent_profile_id)
                    | (AgentMessage.recipient_agent_profile_id == agent_profile_id)
                ),
            )
        )
        return participant is not None


def agent_mailbox_scope(context: ToolContext) -> dict[str, UUID | None]:
    metadata = context.metadata if isinstance(context.metadata, dict) else {}
    raw_scope = metadata.get("agent_mailbox")
    scope = raw_scope.get("scope") if isinstance(raw_scope, dict) else None
    if not isinstance(scope, dict):
        return {"thread_id": None, "task_id": None}
    return {
        "thread_id": optional_uuid_from_metadata(scope.get("thread_id")),
        "task_id": optional_uuid_from_metadata(scope.get("task_id")),
    }


def message_matches_mailbox_scope(context: ToolContext, message: AgentMessage) -> bool:
    scope = agent_mailbox_scope(context)
    if scope["thread_id"] is not None:
        return message.thread_id == scope["thread_id"]
    if scope["task_id"] is not None:
        return message.task_id == scope["task_id"]
    return True


def agent_message_thread_payload(thread: AgentMessageThread) -> dict[str, object]:
    return {
        "id": str(thread.id),
        "workspace_id": str(thread.workspace_id),
        "task_id": str(thread.task_id) if thread.task_id is not None else None,
        "agent_team_id": str(thread.agent_team_id) if thread.agent_team_id is not None else None,
        "subject": thread.subject,
        "status": thread.status,
        "created_at": thread.created_at.isoformat(),
        "updated_at": thread.updated_at.isoformat(),
    }


def agent_message_payload(message: AgentMessage) -> dict[str, object]:
    return AgentMessageResponse.model_validate(message).model_dump(mode="json")
