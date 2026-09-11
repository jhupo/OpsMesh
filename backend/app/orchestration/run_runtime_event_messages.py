from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.agent_runtime.core.contracts import AgentRunResult
from backend.app.agent_runtime.event_mapping import RuntimeEventTaskMessageMapper
from backend.app.orchestration.run_events import RunEventRecorder
from backend.app.runs.models import AgentRun
from backend.app.tasks.message_append import TaskMessageAppendService
from backend.app.tasks.models import TaskStep


@dataclass(slots=True)
class RunRuntimeEventMessageMapper:
    session: Session

    def map(self, run: AgentRun, result: AgentRunResult) -> None:
        if not result.events and not result.stream_events:
            return

        step = self._step_for_run(run) if run.task_id is not None else None
        events = RunEventRecorder(self.session)
        messages = TaskMessageAppendService(self.session)
        mapper = RuntimeEventTaskMessageMapper()

        for event in result.events:
            events.append_event(
                run,
                event.event_type,
                event.message,
                {"runtime_event": event.payload},
            )
            if run.task_id is None:
                continue
            draft = mapper.map_event(event=event, run=run, step=step)
            if draft is None:
                continue
            messages.append(
                task_id=run.task_id,
                workspace_id=run.workspace_id,
                message_type=draft.message_type,
                body=draft.body,
                task_step_id=run.task_step_id,
                agent_run_id=run.id,
                agent_profile_id=run.agent_profile_id,
                payload=draft.payload,
            )
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
