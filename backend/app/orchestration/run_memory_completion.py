from dataclasses import dataclass

from sqlalchemy.orm import Session

from backend.app.agent_runtime.contracts import AgentRunResult
from backend.app.agents.models import AgentProfile
from backend.app.memory.run_capture import AgentRunMemoryCaptureService
from backend.app.runs.models import AgentRun
from backend.app.tasks.models import Task


@dataclass(slots=True)
class RunMemoryCompletionService:
    session: Session

    def capture(self, run: AgentRun, result: AgentRunResult) -> None:
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
