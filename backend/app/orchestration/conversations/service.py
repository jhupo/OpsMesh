from __future__ import annotations

import json
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.agents.profiles.models import AgentProfile
from backend.app.governance.audit.service import AuditService
from backend.app.identity.authorization.context import WorkspaceContext
from backend.app.identity.authorization.execution import ExecutionIdentityService
from backend.app.identity.authorization.resources import (
    ResourceAction,
    ResourceAuthorizationService,
    ResourceKind,
)
from backend.app.orchestration.conversations.models import (
    Conversation,
    ConversationEvent,
    ConversationExecution,
    ConversationTurn,
    turn_event,
)
from backend.app.orchestration.conversations.schemas import ConversationCreate
from backend.app.orchestration.runs.control import RunControlService
from backend.app.orchestration.runs.models import AgentRun
from backend.app.orchestration.runs.service import RunOrchestrationService
from backend.app.orchestration.tasks.models import Task
from backend.app.orchestration.tasks.service import TaskCreateCommand, WorkspaceTaskService
from backend.app.shared.db.pagination import page_scalars
from backend.app.shared.errors import ConflictError, NotFoundError
from backend.app.shared.pagination import PageParams
from backend.app.shared.security.redaction import redact_sensitive_text
from backend.app.shared.utils import uuid_or_none
from backend.app.teams.management.models import AgentTeam

TERMINAL = frozenset({"completed", "failed", "cancelled"})


class ConversationService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(
        self, context: WorkspaceContext, conversation_id: UUID, *, lock: bool = False
    ) -> Conversation:
        statement = select(Conversation).where(
            Conversation.workspace_id == context.workspace.id,
            Conversation.created_by_user_id == context.user.user_id,
            Conversation.id == conversation_id,
        )
        if lock:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        result = self.session.scalar(statement)
        if result is None:
            raise NotFoundError("Conversation not found")
        return result

    def list_conversations(
        self, context: WorkspaceContext, page: PageParams
    ) -> tuple[list[Conversation], int]:
        return page_scalars(
            self.session,
            select(Conversation)
            .where(
                Conversation.workspace_id == context.workspace.id,
                Conversation.created_by_user_id == context.user.user_id,
            )
            .order_by(Conversation.updated_at.desc(), Conversation.id),
            page,
        )

    def create(self, context: WorkspaceContext, data: ConversationCreate) -> Conversation:
        values = data.model_dump()
        if data.mode == "auto" and data.agent_profile_id is None:
            config = context.workspace.settings.get("conversations", {})
            values["agent_profile_id"] = uuid_or_none(
                config.get("manager_agent_profile_id") if isinstance(config, dict) else None
            )
        access = ResourceAuthorizationService(self.session, context.user)
        for kind, key in (
            (ResourceKind.AGENT, "agent_profile_id"),
            (ResourceKind.TEAM, "agent_team_id"),
            (ResourceKind.WORKFLOW, "orchestration_definition_id"),
            (ResourceKind.RUNTIME_SPACE, "runtime_space_id"),
            (ResourceKind.PROJECT, "workspace_project_id"),
        ):
            if values[key] is not None:
                access.require(context.workspace.id, kind, values[key], ResourceAction.INVOKE)
        if data.mode != "team":
            profile = self.session.scalar(
                select(AgentProfile).where(
                    AgentProfile.workspace_id == context.workspace.id,
                    AgentProfile.id == values["agent_profile_id"],
                    AgentProfile.status == "active",
                    AgentProfile.archived_at.is_(None),
                )
            )
            if profile is None:
                raise ConflictError("An active conversation agent must be configured")
        else:
            team = self.session.scalar(
                select(AgentTeam).where(
                    AgentTeam.workspace_id == context.workspace.id,
                    AgentTeam.id == data.agent_team_id,
                    AgentTeam.status == "active",
                )
            )
            if team is None:
                raise ConflictError("Team is unavailable")
        row = Conversation(
            workspace_id=context.workspace.id, created_by_user_id=context.user.user_id, **values
        )
        self.session.add(row)
        self.session.flush()
        self._audit(context, "conversation.created", row.id)
        self.session.commit()
        return row

    def send(
        self, context: WorkspaceContext, conversation_id: UUID, body: str, key: str
    ) -> ConversationTurn:
        conversation = self.get(context, conversation_id, lock=True)
        existing = self.session.scalar(
            select(ConversationTurn).where(
                ConversationTurn.workspace_id == context.workspace.id,
                ConversationTurn.conversation_id == conversation.id,
                ConversationTurn.idempotency_key == key,
            )
        )
        if existing is not None:
            if existing.body != body:
                raise ConflictError("Idempotency-Key was already used for different content")
            return existing
        pending = (
            self.session.scalar(
                select(func.count())
                .select_from(ConversationTurn)
                .where(
                    ConversationTurn.workspace_id == context.workspace.id,
                    ConversationTurn.conversation_id == conversation_id,
                    ConversationTurn.status.not_in(TERMINAL),
                )
            )
            or 0
        )
        if pending >= 32:
            raise ConflictError("Conversation has reached its pending message limit")
        turn = ConversationTurn(
            workspace_id=context.workspace.id,
            conversation_id=conversation.id,
            sequence=conversation.next_sequence,
            idempotency_key=key,
            body=body,
            execution_identity=ExecutionIdentityService(self.session).capture(
                context.workspace.id,
                context.user.user_id,
            ),
        )
        conversation.next_sequence += 1
        self.session.add(turn)
        self.session.flush()
        self._audit(context, "conversation.message.accepted", turn.id)
        self.session.add(turn_event(turn))
        self.session.commit()
        return turn

    def turns(
        self, context: WorkspaceContext, conversation_id: UUID, page: PageParams
    ) -> tuple[list[ConversationTurn], int]:
        self.get(context, conversation_id)
        return page_scalars(
            self.session,
            select(ConversationTurn)
            .where(
                ConversationTurn.workspace_id == context.workspace.id,
                ConversationTurn.conversation_id == conversation_id,
            )
            .order_by(ConversationTurn.sequence),
            page,
        )

    def executions(
        self, context: WorkspaceContext, conversation_id: UUID, page: PageParams
    ) -> tuple[list[ConversationExecution], int]:
        self.get(context, conversation_id)
        return page_scalars(
            self.session,
            select(ConversationExecution)
            .join(
                ConversationTurn,
                ConversationTurn.id == ConversationExecution.turn_id,
            )
            .where(
                ConversationExecution.workspace_id == context.workspace.id,
                ConversationTurn.workspace_id == context.workspace.id,
                ConversationTurn.conversation_id == conversation_id,
            )
            .order_by(ConversationExecution.created_at, ConversationExecution.id),
            page,
        )

    def cancel(
        self, context: WorkspaceContext, conversation_id: UUID, turn_id: UUID
    ) -> ConversationTurn:
        self.get(context, conversation_id, lock=True)
        turn = self.session.scalar(
            select(ConversationTurn)
            .where(
                ConversationTurn.workspace_id == context.workspace.id,
                ConversationTurn.conversation_id == conversation_id,
                ConversationTurn.id == turn_id,
            )
            .with_for_update()
        )
        if turn is None:
            raise NotFoundError("Turn not found")
        if turn.status in TERMINAL:
            return turn
        control = RunControlService(self.session, RunOrchestrationService(self.session).enqueue_run)
        tasks = self.session.scalars(
            select(Task)
            .join(
                ConversationExecution,
                ConversationExecution.task_id == Task.id,
            )
            .where(
                Task.workspace_id == context.workspace.id,
                ConversationExecution.workspace_id == context.workspace.id,
                ConversationExecution.turn_id == turn.id,
            )
        ).all()
        for task in tasks:
            if task.status not in TERMINAL:
                control.cancel_task(
                    workspace_id=context.workspace.id,
                    task_id=task.id,
                    actor_user_id=context.user.user_id,
                    commit=False,
                )
        turn.status = "cancelled"
        self.session.add(turn_event(turn))
        self._audit(context, "conversation.turn.cancelled", turn.id)
        self.session.commit()
        return turn

    def _audit(self, context: WorkspaceContext, action: str, identifier: UUID) -> None:
        AuditService(self.session).record_user_action(
            workspace_id=context.workspace.id,
            user_id=context.user.user_id,
            action=action,
            target_type="conversation",
            target_id=identifier,
            metadata={},
        )

    def retry(
        self, context: WorkspaceContext, conversation_id: UUID, turn_id: UUID
    ) -> ConversationTurn:
        self.get(context, conversation_id, lock=True)
        turn = self.session.scalar(
            select(ConversationTurn)
            .where(
                ConversationTurn.workspace_id == context.workspace.id,
                ConversationTurn.conversation_id == conversation_id,
                ConversationTurn.id == turn_id,
            )
            .with_for_update()
        )
        if turn is None:
            raise NotFoundError("Turn not found")
        if turn.status != "failed" or turn.error_code == "conversation_delegation_limit":
            raise ConflictError("Only failed executions or rejected admission can be retried")
        if (
            self.session.scalar(
                select(ConversationTurn.id)
                .where(
                    ConversationTurn.workspace_id == context.workspace.id,
                    ConversationTurn.conversation_id == conversation_id,
                    ConversationTurn.sequence > turn.sequence,
                )
                .limit(1)
            )
            is not None
        ):
            raise ConflictError("A newer turn exists; submit a follow-up message instead")
        ExecutionIdentityService(self.session).restore(
            context.workspace.id, turn.execution_identity
        )
        link = self.session.scalar(
            select(ConversationExecution).where(
                ConversationExecution.workspace_id == context.workspace.id,
                ConversationExecution.turn_id == turn.id,
                ConversationExecution.purpose == "manager",
                ConversationExecution.round == turn.round,
            )
        )
        if link is not None:
            run = self.session.scalar(
                select(AgentRun)
                .where(
                    AgentRun.workspace_id == context.workspace.id,
                    AgentRun.task_id == link.task_id,
                )
                .order_by(AgentRun.created_at.desc())
                .limit(1)
            )
            if run is None or run.status != "failed":
                raise ConflictError("No failed manager run is available to retry")
            ResourceAuthorizationService(self.session, context.user).require(
                context.workspace.id,
                ResourceKind.TASK,
                link.task_id,
                ResourceAction.INVOKE,
            )
            RunControlService(
                self.session, RunOrchestrationService(self.session).enqueue_run
            ).retry_failed_run(
                workspace_id=context.workspace.id,
                run_id=run.id,
                actor_user_id=context.user.user_id,
                commit=False,
            )
        turn.status = "queued" if link is None else "running"
        turn.error_code = None
        self.session.add(turn_event(turn))
        self._audit(context, "conversation.turn.retry_requested", turn.id)
        self.session.commit()
        return turn

    def events(
        self, context: WorkspaceContext, conversation_id: UUID, after_id: int, limit: int
    ) -> list[ConversationEvent]:
        self.get(context, conversation_id)
        return list(
            self.session.scalars(
                select(ConversationEvent)
                .where(
                    ConversationEvent.workspace_id == context.workspace.id,
                    ConversationEvent.conversation_id == conversation_id,
                    ConversationEvent.id > after_id,
                )
                .order_by(ConversationEvent.id)
                .limit(limit)
            )
        )


def task_reply(task: Task) -> str:
    output = task.final_output or {}
    for key in ("final_output", "summary", "text"):
        if isinstance(output.get(key), str):
            return redact_sensitive_text(str(output[key]))
    return redact_sensitive_text(json.dumps(output, ensure_ascii=False, default=str))


def create_execution(
    session: Session,
    conversation: Conversation,
    turn: ConversationTurn,
    *,
    key: str,
    purpose: str,
    body: str,
    agent_id: UUID | None,
    team_id: UUID | None,
    parent_run_id: UUID | None = None,
) -> Task:
    task = WorkspaceTaskService(session).create_conversation_task(
        workspace_id=conversation.workspace_id,
        user_id=conversation.created_by_user_id,
        execution_identity=turn.execution_identity,
        parent_run_id=parent_run_id,
        command=TaskCreateCommand(
            title=conversation.title if purpose == "manager" else body[:240],
            description=body,
            agent_profile_id=agent_id,
            agent_team_id=team_id,
            orchestration_definition_id=conversation.orchestration_definition_id
            if purpose == "manager"
            else None,
            orchestration_version=conversation.orchestration_version
            if purpose == "manager"
            else None,
            runtime_space_id=conversation.runtime_space_id,
            workspace_project_id=conversation.workspace_project_id,
            input={"conversation_id": str(conversation.id), "turn_id": str(turn.id)},
        ),
    )
    session.add(
        ConversationExecution(
            workspace_id=conversation.workspace_id,
            turn_id=turn.id,
            task_id=task.id,
            key=key,
            purpose=purpose,
            parent_run_id=parent_run_id,
            round=turn.round,
        )
    )
    session.flush()
    return task
