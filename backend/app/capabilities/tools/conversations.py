"""Trusted product gateway for a manager's durable, workspace-scoped delegation."""

from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.sql import ColumnElement

from backend.app.agents.profiles.models import AgentProfile
from backend.app.capabilities.tools.contracts import ToolContext, ToolResourceNotFoundError
from backend.app.identity.authorization.execution import ExecutionIdentityService
from backend.app.identity.authorization.resources import (
    ResourceAction,
    ResourceAuthorizationService,
    ResourceKind,
)
from backend.app.orchestration.conversations.models import (
    Conversation,
    ConversationExecution,
    ConversationTurn,
)
from backend.app.orchestration.conversations.service import TERMINAL, create_execution
from backend.app.orchestration.runs.models import AgentRun
from backend.app.orchestration.tasks.models import Task
from backend.app.teams.management.models import AgentTeam


class ConversationProductTools:
    def __init__(self, session: Session) -> None:
        self.session = session

    def _scope(self, context: ToolContext, name: str) -> tuple[Conversation, ConversationTurn]:
        context.require_tool(name)
        row = self.session.execute(
            select(Conversation, ConversationTurn)
            .join(
                ConversationTurn,
                ConversationTurn.conversation_id == Conversation.id,
            )
            .join(ConversationExecution, ConversationExecution.turn_id == ConversationTurn.id)
            .join(AgentRun, AgentRun.task_id == ConversationExecution.task_id)
            .where(
                Conversation.workspace_id == context.workspace_id,
                ConversationTurn.workspace_id == context.workspace_id,
                ConversationExecution.workspace_id == context.workspace_id,
                ConversationExecution.purpose == "manager",
                ConversationExecution.task_id == context.task_id,
                AgentRun.workspace_id == context.workspace_id,
                AgentRun.id == context.agent_run_id,
                AgentRun.agent_profile_id == Conversation.agent_profile_id,
                AgentRun.status == "running",
                Conversation.mode == "auto",
            )
            .with_for_update(of=ConversationTurn)
        ).first()
        if row is None or row[1].status in TERMINAL:
            raise ToolResourceNotFoundError("Active conversation manager execution not found")
        return row[0], row[1]

    def discover(
        self, context: ToolContext, *, query: str = "", offset: int = 0
    ) -> dict[str, object]:
        if offset < 0 or len(query) > 200:
            raise ValueError("Invalid discovery query or offset")
        conversation, turn = self._scope(context, "discover_conversation_targets")
        user = ExecutionIdentityService(self.session).restore(
            context.workspace_id, turn.execution_identity
        )
        access = ResourceAuthorizationService(self.session, user)
        items: list[dict[str, object]] = []
        for model, kind, label in (
            (AgentProfile, ResourceKind.AGENT, "agent"),
            (AgentTeam, ResourceKind.TEAM, "team"),
        ):
            statement = select(model).where(
                model.workspace_id == context.workspace_id,
                model.status == "active",
                access.predicate(
                    context.workspace_id,
                    kind,
                    cast(ColumnElement[UUID], model.id),
                    ResourceAction.INVOKE,
                ),
            )
            if model is AgentProfile:
                statement = statement.where(
                    model.id != conversation.agent_profile_id, AgentProfile.archived_at.is_(None)
                )
            if query:
                statement = statement.where(
                    model.name.ilike(f"%{query}%") | model.description.ilike(f"%{query}%")
                )
            for raw_target in self.session.scalars(
                statement.order_by(model.id).offset(offset).limit(20)
            ):
                target = cast(AgentProfile | AgentTeam, raw_target)
                items.append(
                    {
                        "kind": label,
                        "id": str(target.id),
                        "name": target.name,
                        "description": target.description,
                    }
                )
        return {
            "items": items,
            "offset": offset,
            "next_offset": offset + 20,
            "note": "Invoke only suitable targets. A team runs asynchronously.",
        }

    def delegate(
        self, context: ToolContext, *, kind: str, target_id: UUID, body: str, request_key: str
    ) -> dict[str, object]:
        conversation, turn = self._scope(context, "delegate_conversation_task")
        if kind not in {"agent", "team"} or not body.strip() or len(body) > 32000:
            raise ValueError("Invalid delegation target or objective")
        if not request_key or len(request_key) > 100:
            raise ValueError("A stable request_key of at most 100 characters is required")
        if kind == "agent" and target_id == conversation.agent_profile_id:
            raise ValueError("A manager cannot delegate to itself")
        user = ExecutionIdentityService(self.session).restore(
            context.workspace_id, turn.execution_identity
        )
        ResourceAuthorizationService(self.session, user).require(
            context.workspace_id,
            ResourceKind.AGENT if kind == "agent" else ResourceKind.TEAM,
            target_id,
            ResourceAction.INVOKE,
        )
        links = list(
            self.session.scalars(
                select(ConversationExecution).where(
                    ConversationExecution.workspace_id == context.workspace_id,
                    ConversationExecution.turn_id == turn.id,
                    ConversationExecution.purpose == "delegate",
                )
            )
        )
        key = f"delegate:{request_key}"
        existing = next((item for item in links if item.key == key), None)
        if existing is not None:
            task = self.session.scalar(
                select(Task).where(
                    Task.workspace_id == context.workspace_id, Task.id == existing.task_id
                )
            )
            if task is None:
                raise ToolResourceNotFoundError("Delegated task is unavailable")
            actual = task.owner_agent_profile_id if kind == "agent" else task.agent_team_id
            if actual != target_id or task.description != body:
                raise ValueError("request_key already identifies a different delegation")
        else:
            if len(links) >= 8 or turn.round >= 3:
                raise ValueError(
                    "Conversation delegation budget exhausted; summarize existing results"
                )
            task = create_execution(
                self.session,
                conversation,
                turn,
                key=key,
                purpose="delegate",
                body=body,
                agent_id=target_id if kind == "agent" else None,
                team_id=target_id if kind == "team" else None,
                parent_run_id=context.agent_run_id,
            )
        return {
            "task_id": str(task.id),
            "status": task.status,
            "instruction": "End this run; the platform resumes you with results.",
        }
