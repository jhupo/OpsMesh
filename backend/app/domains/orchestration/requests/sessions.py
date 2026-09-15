from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.common.values import uuid_or_none
from backend.app.domains.agents.models import AgentProfile
from backend.app.domains.agents.sessions.models import (
    PersistentAgentSession,
    PersistentAgentSessionRef,
    SQLAlchemyAgentSession,
)
from backend.app.domains.orchestration.requests.authorization import (
    authorized_profile_for_run,
    authorized_task_for_run,
)
from backend.app.domains.orchestration.runs.models import AgentRun
from backend.app.domains.orchestration.runs.state import RunStatus
from backend.app.domains.orchestration.tasks.models import Task


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
            task_id=task.id if task is not None and task.agent_team_id is None else None,
            metadata={
                "agent_role": profile.role,
                "source": "run_orchestration",
            },
        )

    def provider_continuation_for_run(
        self,
        *,
        run: AgentRun,
        session_ref: PersistentAgentSessionRef,
    ) -> dict[str, str | None]:
        persistent_session = self.session.scalar(
            select(PersistentAgentSession).where(
                PersistentAgentSession.workspace_id == session_ref.workspace_id,
                PersistentAgentSession.session_key == session_ref.session_key,
            )
        )
        previous_run = self.latest_completed_run_for_session(run, session_ref)
        return {
            "previous_response_id": sdk_continuation_last_response_id(previous_run),
            "conversation_id": persistent_session.openai_conversation_id
            if persistent_session is not None
            else None,
        }

    def latest_completed_run_for_session(
        self,
        run: AgentRun,
        session_ref: PersistentAgentSessionRef,
    ) -> AgentRun | None:
        statement = select(AgentRun).where(
            AgentRun.workspace_id == run.workspace_id,
            AgentRun.id != run.id,
            AgentRun.agent_profile_id == run.agent_profile_id,
            AgentRun.status == RunStatus.COMPLETED.value,
            AgentRun.output.is_not(None),
        )
        if session_ref.scope_type == "task_agent":
            task_id = uuid_or_none(session_ref.scope_id.split(":", 1)[0])
            if task_id is None:
                return None
            statement = statement.where(AgentRun.task_id == task_id)
        elif session_ref.scope_type == "team_agent":
            team_id = uuid_or_none(session_ref.scope_id.split(":", 1)[0])
            if team_id is None:
                return None
            statement = statement.join(Task, Task.id == AgentRun.task_id).where(
                Task.agent_team_id == team_id,
            )
        elif session_ref.scope_type != "workspace_agent":
            return None
        return self.session.scalar(
            statement.order_by(AgentRun.completed_at.desc().nullslast(), AgentRun.updated_at.desc())
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
        if task is not None and task.agent_team_id is not None:
            scope_type = "team_agent"
            scope_id = f"{task.agent_team_id}:{profile.id}"
        elif task is not None:
            scope_type = "task_agent"
            scope_id = f"{task.id}:{profile.id}"
        else:
            scope_type = "workspace_agent"
            scope_id = str(profile.id)
        return PersistentAgentSessionRef(
            session_key=f"{run.workspace_id}:{scope_type}:{scope_id}",
            workspace_id=run.workspace_id,
            scope_type=scope_type,
            scope_id=scope_id,
        )


def sdk_continuation_last_response_id(run: AgentRun | None) -> str | None:
    if run is None or not isinstance(run.output, dict):
        return None
    raw_output = run.output.get("raw_output")
    if not isinstance(raw_output, dict):
        return None
    sdk_continuation = raw_output.get("sdk_continuation")
    if not isinstance(sdk_continuation, dict):
        return None
    last_response_id = sdk_continuation.get("last_response_id")
    return last_response_id if isinstance(last_response_id, str) and last_response_id else None


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
