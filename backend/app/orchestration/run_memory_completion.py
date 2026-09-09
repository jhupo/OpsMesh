from dataclasses import dataclass

from sqlalchemy.orm import Session

from backend.app.agent_runtime.contracts import AgentRunResult
from backend.app.agents.models import AgentProfile
from backend.app.memory.episodic import AgentEpisodicMemoryService
from backend.app.memory.working import AgentWorkingMemoryService
from backend.app.runs.models import AgentRun
from backend.app.tasks.models import Task


@dataclass(slots=True)
class RunMemoryCompletionService:
    session: Session

    def capture(self, run: AgentRun, result: AgentRunResult) -> None:
        profile = self._profile_for_run(run)
        task = self._task_for_run(run)
        AgentEpisodicMemoryService(self.session).capture_run_completed(
            run,
            result,
            profile=profile,
            task=task,
        )

    def capture_failed(self, run: AgentRun, error: dict[str, object]) -> None:
        profile = self._profile_for_run(run)
        AgentEpisodicMemoryService(self.session).capture_run_failed(
            run,
            error,
            profile=profile,
            task=self._task_for_run(run),
        )

    def capture_task_completed(
        self,
        run: AgentRun,
        task: Task,
        result: AgentRunResult,
    ) -> None:
        AgentEpisodicMemoryService(self.session).capture_task_completed(
            task,
            event_id=run.id,
            run=run,
            profile=self._profile_for_run(run),
            summary=result.final_output,
        )

    def expire_working(self, run: AgentRun) -> int:
        return AgentWorkingMemoryService(self.session).expire_run(
            workspace_id=run.workspace_id,
            run_id=run.id,
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
