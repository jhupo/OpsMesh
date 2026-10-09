import json
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid5

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.agents.profiles.models import AgentProfile
from backend.app.agents.sessions.models import PersistentAgentSession, PersistentAgentSessionRef
from backend.app.agents.sessions.store import SQLAlchemyAgentSession
from backend.app.identity.authorization.execution import ExecutionIdentityService
from backend.app.identity.authorization.resources import ResourceAccessDenied
from backend.app.orchestration.automations.models import AutomationEvent
from backend.app.orchestration.conversations.models import (
    Conversation,
    ConversationExecution,
    ConversationTurn,
)
from backend.app.orchestration.runs.authorization.validation import (
    authorized_profile_for_run,
    authorized_task_for_run,
)
from backend.app.orchestration.runs.models import AgentRun
from backend.app.orchestration.tasks.models import Task


@dataclass(slots=True)
class RunRequestSessionService:
    session: Session

    def persistent_session_for_run(
        self,
        run: AgentRun,
        task: Task | None,
        profile: AgentProfile,
        ref: PersistentAgentSessionRef | None = None,
    ) -> SQLAlchemyAgentSession | None:
        if profile.id is None:
            return None
        ref = ref or self.persistent_session_ref_for_run(run, task, profile)
        return SQLAlchemyAgentSession(
            db_session=self.session,
            ref=ref,
            agent_profile_id=profile.id,
            agent_team_id=task.agent_team_id if task is not None else None,
            task_id=task.id if task is not None and ref.scope_type == "task_agent" else None,
            metadata={
                "agent_role": profile.role,
                "source": "run_orchestration",
            },
        )

    def sync_provider_conversation_id(self, run: AgentRun) -> None:
        conversation_id = sdk_continuation_conversation_id(run)
        if conversation_id is None:
            return
        task = authorized_task_for_run(self.session, run)
        profile = authorized_profile_for_run(self.session, run)
        if profile is None:
            return
        session_ref = self.persistent_session_ref_for_run(run, task, profile)
        persistent_session = self.session.scalar(
            select(PersistentAgentSession).where(
                PersistentAgentSession.workspace_id == session_ref.workspace_id,
                PersistentAgentSession.session_key == session_ref.session_key,
            )
        )
        if persistent_session is None:
            return
        persistent_session.openai_conversation_id = conversation_id
        persistent_session.updated_at = datetime.now(UTC)
        self.session.flush([persistent_session])

    def persistent_session_ref_for_run(
        self,
        run: AgentRun,
        task: Task | None,
        profile: AgentProfile,
    ) -> PersistentAgentSessionRef:
        if task is None:
            raise ResourceAccessDenied()
        user = ExecutionIdentityService(self.session).restore(
            run.workspace_id,
            task.execution_identity,
        )
        execution = self.session.execute(
            select(Conversation, ConversationExecution.purpose)
            .join(ConversationTurn, ConversationTurn.conversation_id == Conversation.id)
            .join(ConversationExecution, ConversationExecution.turn_id == ConversationTurn.id)
            .where(
                Conversation.workspace_id == run.workspace_id,
                ConversationTurn.workspace_id == run.workspace_id,
                ConversationExecution.workspace_id == run.workspace_id,
                ConversationExecution.task_id == task.id,
            )
        ).one_or_none()
        event = self.session.scalar(
            select(AutomationEvent).where(
                AutomationEvent.workspace_id == run.workspace_id,
                AutomationEvent.task_id == task.id,
            )
        )
        if execution is not None and execution[1] == "manager":
            conversation = execution[0]
            if conversation.created_by_user_id != user.user_id:
                raise ResourceAccessDenied()
            scope_type = "conversation_agent"
            scope_id = f"{conversation.id}:{profile.id}"
        elif event is not None and event.configuration.get("trigger_type") == "message":
            sender_id = event.input_payload.get("sender_id")
            if not isinstance(sender_id, str) or not sender_id:
                raise ResourceAccessDenied()
            scope_type = "automation_agent"
            conversation_id = uuid5(
                event.automation_id,
                json.dumps([sender_id, event.conversation_id], ensure_ascii=False),
            )
            scope_id = f"{conversation_id}:{profile.id}"
        elif task.agent_team_id is not None and execution is None:
            scope_type = "team_agent"
            scope_id = f"{task.agent_team_id}:{profile.id}"
        else:
            scope_type = "task_agent"
            scope_id = f"{task.id}:{profile.id}"
        key = f"{run.workspace_id}:{user.user_id}:{scope_type}:{scope_id}"
        if run.session_key is not None and run.session_key != key:
            raise ResourceAccessDenied()
        run.session_key = key
        return PersistentAgentSessionRef(
            session_key=key,
            workspace_id=run.workspace_id,
            scope_type=scope_type,
            scope_id=scope_id,
        )


def sdk_continuation_conversation_id(run: AgentRun) -> str | None:
    if not isinstance(run.output, dict):
        return None
    raw_output = run.output.get("raw_output")
    if not isinstance(raw_output, dict):
        return None
    sdk_continuation = raw_output.get("sdk_continuation")
    if not isinstance(sdk_continuation, dict):
        return None
    conversation_id = sdk_continuation.get("conversation_id")
    return conversation_id if isinstance(conversation_id, str) and conversation_id else None
