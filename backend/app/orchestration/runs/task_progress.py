from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.orm import Session

from backend.app.runs.models import AgentRun
from backend.app.tasks.models import Task, TaskMessage

from .state.task_progress import deep_merge_dict, task_progress_from_output

AppendTaskMessage = Callable[..., TaskMessage]


@dataclass(slots=True)
class RunTaskProgressService:
    session: Session
    append_task_message: AppendTaskMessage

    def apply_from_agent_output(
        self,
        task: Task,
        *,
        run: AgentRun,
        final_output: str,
    ) -> None:
        progress = task_progress_from_output(final_output)
        if progress is None:
            return
        changed_fields: list[str] = []
        if progress.generic_state:
            task.generic_state = deep_merge_dict(task.generic_state, progress.generic_state)
            changed_fields.append("generic_state")
        if progress.domain_state:
            task.domain_state = deep_merge_dict(task.domain_state, progress.domain_state)
            changed_fields.append("domain_state")
        if progress.task_input:
            task.input = deep_merge_dict(task.input, progress.task_input)
            changed_fields.append("input")
        if not changed_fields:
            return

        self.append_task_message(
            task_id=task.id,
            workspace_id=task.workspace_id,
            message_type="task.progress.updated",
            body="Task progress state updated from agent output.",
            task_step_id=run.task_step_id,
            agent_run_id=run.id,
            agent_profile_id=run.agent_profile_id,
            payload={
                "changed_fields": changed_fields,
                "progress": progress.progress,
                "summary": progress.summary,
                "source": "agent_output",
            },
        )

    def task_for_run(self, run: AgentRun) -> Task | None:
        if run.task_id is None:
            return None
        task = self.session.get(Task, run.task_id)
        if task is None or task.workspace_id != run.workspace_id:
            return None
        return task
