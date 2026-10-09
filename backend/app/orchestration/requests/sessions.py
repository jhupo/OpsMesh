import json
from dataclasses import dataclass
from uuid import uuid5

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.agents.execution.contracts import AgentSessionBinding
from backend.app.agents.profiles.models import AgentProfile
from backend.app.agents.sessions.models import PersistentAgentSessionRef
from backend.app.agents.sessions.store import prepare_session_binding
from backend.app.identity.authorization.execution import ExecutionIdentityService
from backend.app.identity.authorization.resources import ResourceAccessDenied
from backend.app.orchestration.automations.models import AutomationEvent
from backend.app.orchestration.conversations.models import (
    Conversation,
    ConversationExecution,
    ConversationTurn,
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
        *,
        sdk_provider: str,
    ) -> AgentSessionBinding | None:
        if profile.id is None:
            return None
        ref = ref or self.persistent_session_ref_for_run(run, task, profile)
        return prepare_session_binding(
            self.session,
            ref=ref,
            agent_profile_id=profile.id,
            agent_team_id=task.agent_team_id if task is not None else None,
            task_id=task.id if task is not None and ref.scope_type == "task_agent" else None,
            metadata={
                "agent_role": profile.role,
                "source": "run_orchestration",
                "sdk_provider": sdk_provider,
            },
        )

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
