from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.agent_runtime.contracts import AgentRunResult
from backend.app.agent_runtime.session_management import PersistentAgentSessionManagementService
from backend.app.agent_runtime.sessions import PersistentAgentSession, PersistentAgentSessionRef
from backend.app.agents.models import AgentProfile
from backend.app.memory.run_capture import AgentRunMemoryCaptureService
from backend.app.runs.models import AgentRun
from backend.app.tasks.models import Task

PersistentSessionRefForRun = Callable[
    [AgentRun, Task | None, AgentProfile],
    PersistentAgentSessionRef,
]


@dataclass(slots=True)
class RunMemoryCompletionService:
    session: Session
    persistent_session_ref_for_run: PersistentSessionRefForRun

    def capture_and_compact(self, run: AgentRun, result: AgentRunResult) -> None:
        profile = self._profile_for_run(run)
        if profile is None:
            return
        task = self._task_for_run(run)
        AgentRunMemoryCaptureService(self.session).capture_completed_run(
            run,
            result,
            profile=profile,
            task=task,
        )
        self._compact_persistent_session(run, profile=profile, task=task)

    def _profile_for_run(self, run: AgentRun) -> AgentProfile | None:
        if run.agent_profile_id is None:
            return None
        profile = self.session.get(AgentProfile, run.agent_profile_id)
        if profile is None or profile.workspace_id != run.workspace_id:
            return None
        return profile

    def _task_for_run(self, run: AgentRun) -> Task | None:
        if run.task_id is None:
            return None
        task = self.session.get(Task, run.task_id)
        if task is None or task.workspace_id != run.workspace_id:
            return None
        return task

    def _compact_persistent_session(
        self,
        run: AgentRun,
        *,
        profile: AgentProfile,
        task: Task | None,
    ) -> None:
        ref = self.persistent_session_ref_for_run(run, task, profile)
        persistent_session = self.session.scalar(
            select(PersistentAgentSession).where(
                PersistentAgentSession.workspace_id == run.workspace_id,
                PersistentAgentSession.session_key == ref.session_key,
            )
        )
        if persistent_session is None:
            return
        PersistentAgentSessionManagementService(self.session).compact_if_needed(
            workspace_id=run.workspace_id,
            session_id=persistent_session.id,
            policy=profile.memory_policy,
        )
