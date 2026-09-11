from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.agent_messages.models import AgentMessage
from backend.app.agent_messages.service import AgentMailboxService
from backend.app.agents.models import AgentProfile
from backend.app.runs.models import AgentRun
from backend.app.security.redaction import redact_sensitive_text
from backend.app.tasks.models import Task
from backend.app.teams.models import AgentTeam, AgentTeamMember
from backend.app.teams.runtime import TeamRuntimeService


@dataclass(slots=True)
class RunRequestContextProvider:
    session: Session

    def mailbox_context_for_run(
        self,
        run: AgentRun,
        task: Task | None,
        profile: AgentProfile,
    ) -> dict[str, object]:
        if profile.id is None:
            return {}
        thread_id: UUID | None = None
        task_id: UUID | None = None
        if task is not None and task.agent_team_id is not None:
            runtime_state = TeamRuntimeService(self.session).get_state(
                workspace_id=run.workspace_id,
                team_id=task.agent_team_id,
                initialize=True,
            )
            thread_id = runtime_state.thread_id if runtime_state is not None else None
        else:
            task_id = run.task_id
        scope = mailbox_scope_metadata(thread_id=thread_id, task_id=task_id)
        inbox = AgentMailboxService(self.session).get_agent_inbox(
            run.workspace_id,
            profile.id,
            latest_limit=5,
            unread_only=True,
            thread_id=thread_id,
            task_id=task_id,
        )
        latest_messages = inbox.get("latest_messages")
        return {
            "agent_mailbox": {
                "scope": scope,
                "thread_count": inbox.get("thread_count", 0),
                "message_count": inbox.get("message_count", 0),
                "unread_count": inbox.get("unread_count", 0),
                "pending_count": inbox.get("pending_count", 0),
                "latest_unread_messages": [
                    mailbox_message_context(message)
                    for message in latest_messages
                    if isinstance(message, AgentMessage)
                ]
                if isinstance(latest_messages, list)
                else [],
            }
        }

    def team_context_for_run(
        self,
        run: AgentRun,
        task: Task | None,
        profile: AgentProfile,
    ) -> dict[str, object]:
        if task is None or task.agent_team_id is None:
            return {}
        team = self.session.scalar(
            select(AgentTeam).where(
                AgentTeam.workspace_id == run.workspace_id,
                AgentTeam.id == task.agent_team_id,
            )
        )
        if team is None:
            return {}
        runtime_state = TeamRuntimeService(self.session).get_state(
            workspace_id=run.workspace_id,
            team_id=team.id,
            initialize=True,
        )
        member = self.session.scalar(
            select(AgentTeamMember).where(
                AgentTeamMember.workspace_id == run.workspace_id,
                AgentTeamMember.agent_team_id == team.id,
                AgentTeamMember.agent_profile_id == profile.id,
                AgentTeamMember.status == "active",
            )
        )
        members = list(
            self.session.scalars(
                select(AgentTeamMember)
                .where(
                    AgentTeamMember.workspace_id == run.workspace_id,
                    AgentTeamMember.agent_team_id == team.id,
                    AgentTeamMember.status == "active",
                )
                .order_by(AgentTeamMember.order_index.asc(), AgentTeamMember.id.asc())
            )
        )
        return {
            "team_context": {
                "team_id": str(team.id),
                "team_name": team.name,
                "team_type": team.team_type,
                "team_status": team.status,
                "manager_agent_profile_id": str(team.manager_agent_profile_id)
                if team.manager_agent_profile_id is not None
                else None,
                "current_member": team_member_context(member),
                "members": [team_member_context(item) for item in members],
                "runtime": team_runtime_context(runtime_state),
            }
        }


def mailbox_message_context(message: AgentMessage) -> dict[str, object]:
    return {
        "id": str(message.id),
        "thread_id": str(message.thread_id),
        "task_id": str(message.task_id) if message.task_id is not None else None,
        "agent_team_id": str(message.agent_team_id) if message.agent_team_id is not None else None,
        "sender_agent_profile_id": str(message.sender_agent_profile_id)
        if message.sender_agent_profile_id is not None
        else None,
        "message_type": message.message_type,
        "status": message.status,
        "created_at": message.created_at.isoformat(),
        "body_preview": redact_sensitive_text(message.body[:500]),
    }


def mailbox_scope_metadata(
    *,
    thread_id: UUID | None,
    task_id: UUID | None,
) -> dict[str, str]:
    scope: dict[str, str] = {}
    if thread_id is not None:
        scope["thread_id"] = str(thread_id)
    if task_id is not None:
        scope["task_id"] = str(task_id)
    return scope


def team_member_context(member: AgentTeamMember | None) -> dict[str, object] | None:
    if member is None:
        return None
    return {
        "member_id": str(member.id),
        "agent_profile_id": str(member.agent_profile_id),
        "reports_to_member_id": str(member.reports_to_member_id)
        if member.reports_to_member_id is not None
        else None,
        "team_role": member.team_role,
        "department": member.department,
        "position_title": member.position_title,
        "responsibilities": [item for item in member.responsibilities if isinstance(item, str)][
            :20
        ],
        "accepts_tasks": member.accepts_tasks,
        "max_concurrent_tasks": member.max_concurrent_tasks,
    }


def team_runtime_context(runtime_state: object | None) -> dict[str, object] | None:
    if runtime_state is None:
        return None
    return {
        "status": getattr(runtime_state, "status", None),
        "workspace_runtime_id": str(getattr(runtime_state, "workspace_runtime_id", ""))
        if getattr(runtime_state, "workspace_runtime_id", None) is not None
        else None,
        "runtime_status": getattr(runtime_state, "runtime_status", None),
        "runtime_space_id": str(getattr(runtime_state, "runtime_space_id", ""))
        if getattr(runtime_state, "runtime_space_id", None) is not None
        else None,
        "thread_id": str(getattr(runtime_state, "thread_id", "")),
        "team_session_id": str(getattr(runtime_state, "team_session_id", "")),
        "member_session_count": getattr(runtime_state, "member_session_count", 0),
    }
