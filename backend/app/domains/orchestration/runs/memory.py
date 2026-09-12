from dataclasses import dataclass

from sqlalchemy.orm import Session

from backend.app.domains.agents.memory.stores.episodic import AgentEpisodicMemoryService
from backend.app.domains.agents.memory.stores.working import AgentWorkingMemoryService
from backend.app.domains.agents.models import AgentProfile
from backend.app.domains.agents.runtime.execution.contracts import AgentRunResult
from backend.app.domains.orchestration.runs.models import AgentRun
from backend.app.domains.orchestration.runs.queries import task_for_run
from backend.app.domains.orchestration.tasks.models import Task


@dataclass(slots=True)
class RunMemoryCompletionService:
    session: Session

    def capture(self, run: AgentRun, result: AgentRunResult) -> None:
        profile = self._profile_for_run(run)
        task = task_for_run(self.session, run)
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
            task=task_for_run(self.session, run),
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
