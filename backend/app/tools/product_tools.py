from datetime import UTC, datetime
from hashlib import sha256
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.agent_messages.models import AgentMessage, AgentMessageThread
from backend.app.agent_messages.service import AgentMailboxService
from backend.app.agents.models import AgentProfile
from backend.app.api.pagination import PageParams
from backend.app.api.schemas.agent_messages import (
    AgentMessageCreateRequest,
    AgentMessageResponse,
    AgentMessageThreadCreateRequest,
)
from backend.app.artifacts.models import Artifact
from backend.app.files.models import WorkspaceFile
from backend.app.files.security import safe_filename
from backend.app.memory.models import WorkspaceMemoryEntry
from backend.app.runs.models import AgentRun, RunEvent
from backend.app.tasks.models import Task, TaskStep
from backend.app.teams.models import AgentTeam, AgentTeamMember
from backend.app.tools.context import ToolContext
from backend.app.tools.errors import ToolResourceNotFoundError
from backend.app.tools.workspace_memory import WorkspaceMemorySearchService


class ProductToolService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_workspace_files(self, context: ToolContext) -> list[WorkspaceFile]:
        context.require_tool("list_workspace_files")
        self._append_tool_event(context, "tool.called", "list_workspace_files")
        files = list(
            self._session.scalars(
                select(WorkspaceFile)
                .where(WorkspaceFile.workspace_id == context.workspace_id)
                .order_by(WorkspaceFile.created_at.desc())
            ).all()
        )
        self._append_tool_event(context, "tool.completed", "list_workspace_files")
        return files

    def read_workspace_file(self, context: ToolContext, file_id: UUID) -> WorkspaceFile:
        context.require_tool("read_workspace_file")
        self._append_tool_event(context, "tool.called", "read_workspace_file")
        file = self._session.get(WorkspaceFile, file_id)
        if file is None or file.workspace_id != context.workspace_id:
            raise ToolResourceNotFoundError("Workspace file not found")
        self._append_tool_event(context, "tool.completed", "read_workspace_file")
        return file

    def write_artifact(
        self,
        context: ToolContext,
        *,
        filename: str,
        content: bytes,
        content_type: str,
        artifact_type: str = "file",
    ) -> Artifact:
        context.require_tool("write_artifact")
        self._append_tool_event(context, "tool.called", "write_artifact")
        checksum = sha256(content).hexdigest()
        sanitized_filename = safe_filename(filename, default="artifact.bin")
        binding = self._artifact_binding(context)
        artifact = Artifact(
            workspace_id=context.workspace_id,
            task_id=context.task_id,
            agent_run_id=context.agent_run_id,
            task_step_id=binding["task_step_id"],
            agent_profile_id=binding["agent_profile_id"],
            work_package_id=binding["work_package_id"],
            version=binding["version"],
            supersedes_artifact_id=binding["supersedes_artifact_id"],
            review_status="pending",
            artifact_type=artifact_type,
            filename=sanitized_filename,
            content_type=content_type,
            size_bytes=len(content),
            checksum_sha256=checksum,
            storage_key=f"workspaces/{context.workspace_id}/artifacts/{checksum}/{sanitized_filename}",
            created_at=datetime.now(UTC),
        )
        self._session.add(artifact)
        self._append_tool_event(context, "tool.completed", "write_artifact")
        self._session.flush()
        return artifact

    def search_workspace_memory(
        self,
        context: ToolContext,
        query: str,
        *,
        limit: int = 10,
        source_types: set[str] | None = None,
    ) -> list[dict[str, object]]:
        context.require_tool("search_workspace_memory")
        self._append_tool_event(context, "tool.called", "search_workspace_memory")
        results = WorkspaceMemorySearchService(self._session).search(
            workspace_id=context.workspace_id,
            query=query,
            limit=limit,
            source_types=source_types,
        )
        self._append_tool_event(context, "tool.completed", "search_workspace_memory")
        return results

    def remember_workspace_memory(
        self,
        context: ToolContext,
        *,
        title: str,
        content: str,
        entry_type: str = "note",
        tags: list[str] | None = None,
        source_type: str | None = None,
        source_id: str | None = None,
        visibility_scope: str = "workspace",
        importance: int = 0,
        metadata: dict[str, object] | None = None,
    ) -> WorkspaceMemoryEntry:
        context.require_tool("remember_workspace_memory")
        self._append_tool_event(context, "tool.called", "remember_workspace_memory")
        run = self._run_for_context(context)
        entry = WorkspaceMemoryEntry(
            workspace_id=context.workspace_id,
            created_by_agent_profile_id=run.agent_profile_id if run is not None else None,
            created_by_agent_run_id=context.agent_run_id,
            source_type=_bounded_optional(source_type, 80),
            source_id=_bounded_optional(source_id, 120),
            entry_type=_bounded_text(entry_type, 80, "note"),
            title=_bounded_text(title, 240, "Untitled memory"),
            content=content.strip(),
            tags=_normalized_tags(tags),
            visibility_scope=_bounded_text(visibility_scope, 32, "workspace"),
            importance=max(0, min(100, importance)),
            status="active",
            memory_metadata=metadata or {},
        )
        self._session.add(entry)
        self._session.flush()
        self._append_tool_event(context, "tool.completed", "remember_workspace_memory")
        return entry

    def archive_workspace_memory(
        self,
        context: ToolContext,
        memory_entry_id: UUID,
    ) -> WorkspaceMemoryEntry:
        context.require_tool("archive_workspace_memory")
        self._append_tool_event(context, "tool.called", "archive_workspace_memory")
        entry = self._session.get(WorkspaceMemoryEntry, memory_entry_id)
        if entry is None or entry.workspace_id != context.workspace_id:
            raise ToolResourceNotFoundError("Workspace memory entry not found")
        entry.status = "archived"
        self._session.flush([entry])
        self._append_tool_event(context, "tool.completed", "archive_workspace_memory")
        return entry

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
        scope = _agent_mailbox_scope(context)
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
            "thread": _agent_message_thread_payload(thread),
            "message": _agent_message_payload(message),
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
            "thread": _agent_message_thread_payload(thread),
            "items": [_agent_message_payload(message) for message in messages],
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
        scope = _agent_mailbox_scope(context)
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
                _agent_message_payload(message)
                for message in inbox.get("latest_messages", [])
                if isinstance(message, AgentMessage)
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
            or not _message_matches_mailbox_scope(context, message)
        ):
            raise ToolResourceNotFoundError("Agent message not found for agent")
        updated = AgentMailboxService(self._session).mark_message_read(
            context.workspace_id,
            message_id,
        )
        if updated is None:
            raise ToolResourceNotFoundError("Agent message not found for agent")
        self._append_tool_event(context, "tool.completed", "mark_agent_message_read")
        return {"message": _agent_message_payload(updated)}

    def _run_for_context(self, context: ToolContext) -> AgentRun | None:
        if context.agent_run_id is None:
            return None
        run = self._session.get(AgentRun, context.agent_run_id)
        if run is None or run.workspace_id != context.workspace_id:
            return None
        return run

    def _sender_agent_profile_id(self, context: ToolContext) -> UUID:
        run = self._run_for_context(context)
        if run is None or run.agent_profile_id is None:
            raise ToolResourceNotFoundError("Agent run is not bound to an agent profile")
        agent = self._session.get(AgentProfile, run.agent_profile_id)
        if agent is None or agent.workspace_id != context.workspace_id:
            raise ToolResourceNotFoundError("Agent not found in workspace")
        return run.agent_profile_id

    def _create_agent_message_thread(
        self,
        context: ToolContext,
        subject: str | None,
    ) -> AgentMessageThread:
        scope = _agent_mailbox_scope(context)
        return AgentMailboxService(self._session).create_thread(
            workspace_id=context.workspace_id,
            data=AgentMessageThreadCreateRequest(
                task_id=scope["task_id"] or context.task_id,
                subject=_bounded_optional(subject, 240) or "",
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
            or _agent_mailbox_scope(context)["task_id"]
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

    def _append_tool_event(self, context: ToolContext, event_type: str, tool_name: str) -> None:
        if context.agent_run_id is None:
            return
        next_sequence = (
            self._session.scalar(
                select(func.coalesce(func.max(RunEvent.sequence), 0)).where(
                    RunEvent.workspace_id == context.workspace_id,
                    RunEvent.agent_run_id == context.agent_run_id,
                )
            )
            or 0
        ) + 1
        self._session.add(
            RunEvent(
                workspace_id=context.workspace_id,
                agent_run_id=context.agent_run_id,
                event_type=event_type,
                sequence=next_sequence,
                message=tool_name,
                created_at=datetime.now(UTC),
            )
        )

    def _artifact_binding(self, context: ToolContext) -> dict[str, object]:
        run = (
            self._session.get(AgentRun, context.agent_run_id)
            if context.agent_run_id is not None
            else None
        )
        step = (
            self._session.get(TaskStep, run.task_step_id)
            if run is not None and run.task_step_id is not None
            else None
        )
        if step is not None and step.workspace_id != context.workspace_id:
            step = None
        work_package_id = step.work_package_id if step is not None else None
        previous = self._latest_artifact_for_work_package(context, work_package_id)
        return {
            "task_step_id": step.id if step is not None else None,
            "agent_profile_id": run.agent_profile_id if run is not None else None,
            "work_package_id": work_package_id,
            "version": (previous.version + 1) if previous is not None else 1,
            "supersedes_artifact_id": previous.id if previous is not None else None,
        }

    def _latest_artifact_for_work_package(
        self,
        context: ToolContext,
        work_package_id: str | None,
    ) -> Artifact | None:
        if context.task_id is None or work_package_id is None:
            return None
        return self._session.scalar(
            select(Artifact)
            .where(
                Artifact.workspace_id == context.workspace_id,
                Artifact.task_id == context.task_id,
                Artifact.work_package_id == work_package_id,
            )
            .order_by(Artifact.version.desc(), Artifact.created_at.desc())
        )


def _bounded_text(value: str, max_length: int, default: str) -> str:
    text = value.strip()
    if not text:
        text = default
    return text[:max_length]


def _bounded_optional(value: str | None, max_length: int) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text[:max_length] if text else None


def _normalized_tags(tags: list[str] | None) -> list[str]:
    if not tags:
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        text = tag.strip().lower()[:64]
        if not text or text in seen:
            continue
        seen.add(text)
        normalized.append(text)
        if len(normalized) >= 20:
            break
    return normalized


def _agent_mailbox_scope(context: ToolContext) -> dict[str, UUID | None]:
    metadata = context.metadata if isinstance(context.metadata, dict) else {}
    raw_scope = metadata.get("agent_mailbox")
    scope = raw_scope.get("scope") if isinstance(raw_scope, dict) else None
    if not isinstance(scope, dict):
        return {"thread_id": None, "task_id": None}
    return {
        "thread_id": _optional_uuid_from_metadata(scope.get("thread_id")),
        "task_id": _optional_uuid_from_metadata(scope.get("task_id")),
    }


def _message_matches_mailbox_scope(context: ToolContext, message: AgentMessage) -> bool:
    scope = _agent_mailbox_scope(context)
    if scope["thread_id"] is not None:
        return message.thread_id == scope["thread_id"]
    if scope["task_id"] is not None:
        return message.task_id == scope["task_id"]
    return True


def _optional_uuid_from_metadata(value: object) -> UUID | None:
    if isinstance(value, UUID):
        return value
    if not isinstance(value, str) or not value:
        return None
    try:
        return UUID(value)
    except ValueError:
        return None


def _agent_message_thread_payload(thread: AgentMessageThread) -> dict[str, object]:
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


def _agent_message_payload(message: AgentMessage) -> dict[str, object]:
    return AgentMessageResponse.model_validate(message).model_dump(mode="json")
