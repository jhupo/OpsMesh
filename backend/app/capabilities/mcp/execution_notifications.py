from dataclasses import dataclass

from sqlalchemy.orm import Session

from backend.app.orchestration.run_events import RunEventRecorder
from backend.app.runs.models import AgentRun, RunEvent
from backend.app.tasks.message_append import TaskMessageAppendService
from backend.app.tasks.models import TaskMessage


@dataclass(slots=True)
class McpExecutionNotifier:
    session: Session

    def append_run_event(
        self,
        *,
        run: AgentRun,
        event_type: str,
        message: str,
        metadata: dict[str, object],
    ) -> RunEvent:
        return RunEventRecorder(self.session).append_event(
            run,
            event_type,
            message,
            metadata,
        )

    def append_task_message(
        self,
        *,
        run: AgentRun,
        message_type: str,
        body: str,
        payload: dict[str, object],
    ) -> TaskMessage | None:
        if run.task_id is None:
            return None
        return TaskMessageAppendService(self.session).append(
            workspace_id=run.workspace_id,
            task_id=run.task_id,
            task_step_id=run.task_step_id,
            agent_run_id=run.id,
            agent_profile_id=run.agent_profile_id,
            message_type=message_type,
            body=body,
            payload=payload,
        )
