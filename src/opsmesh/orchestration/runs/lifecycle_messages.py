from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.agents.execution.contracts import AgentLifecycleEvent, AgentRunResult
from opsmesh.agents.execution.events import task_message_from_lifecycle_event
from opsmesh.orchestration.runs.events import RunEventRecorder, RunEventWriter
from opsmesh.orchestration.runs.models import AgentRun, RunEvent
from opsmesh.orchestration.tasks.message_append import TaskMessageAppendService
from opsmesh.orchestration.tasks.models import TaskStep


@dataclass(slots=True)
class RunLifecycleMessageWriter:
    session: Session

    def map(self, run: AgentRun, result: AgentRunResult) -> None:
        if not result.events and not result.stream_events:
            return

        events = RunEventRecorder(self.session)

        for event in result.events:
            self.map_event(run, event)
        for stream_event in result.stream_events:
            events.append_event(
                run,
                f"agent.stream.{stream_event.event_type}",
                "Agent SDK stream event",
                {
                    "runtime_stream_event": {
                        "event_type": stream_event.event_type,
                        "provider_sequence": stream_event.sequence,
                        "payload": stream_event.payload,
                        "delta": stream_event.delta,
                        "is_terminal": stream_event.is_terminal,
                    }
                },
            )

    def map_event(
        self, run: AgentRun, event: AgentLifecycleEvent, *, event_id: UUID | None = None
    ) -> None:
        if (
            event_id is not None
            and self.session.scalar(
                select(RunEvent.id).where(
                    RunEvent.workspace_id == run.workspace_id,
                    RunEvent.agent_run_id == run.id,
                    RunEvent.id == event_id,
                )
            )
            is not None
        ):
            return
        RunEventWriter(self.session).append(
            workspace_id=run.workspace_id,
            run_id=run.id,
            event_type=event.event_type,
            message=event.message,
            metadata={"runtime_event": event.payload},
            event_id=event_id,
        )
        if run.task_id is None:
            return
        draft = task_message_from_lifecycle_event(
            event=event, run=run, step=self._step_for_run(run)
        )
        if draft is not None:
            TaskMessageAppendService(self.session).append(
                task_id=run.task_id,
                workspace_id=run.workspace_id,
                message_type=draft.message_type,
                body=draft.body,
                task_step_id=run.task_step_id,
                agent_run_id=run.id,
                agent_profile_id=run.agent_profile_id,
                payload=draft.payload,
            )

    def _step_for_run(self, run: AgentRun) -> TaskStep | None:
        if run.task_step_id is None:
            return None
        return self.session.scalar(
            select(TaskStep).where(
                TaskStep.workspace_id == run.workspace_id,
                TaskStep.task_id == run.task_id,
                TaskStep.id == run.task_step_id,
            )
        )
