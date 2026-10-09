"""Durable task instructions are frozen at a new Run's request boundary."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.orchestration.requests.context_budget import ContextFragment, ContextPriority
from backend.app.orchestration.runs.events import RunEventRecorder
from backend.app.orchestration.runs.models import AgentRun, RunEvent
from backend.app.orchestration.tasks.models import TaskMessage


@dataclass
class RunInstructionService:
    session: Session

    def _event(self, run: AgentRun, status: str) -> RunEvent | None:
        return self.session.scalar(
            select(RunEvent)
            .where(
                RunEvent.workspace_id == run.workspace_id,
                RunEvent.agent_run_id == run.id,
                RunEvent.event_type == f"task.instructions.{status}",
            )
            .order_by(RunEvent.sequence.asc())
        )

    def context(self, run: AgentRun) -> tuple[ContextFragment | None, list[str]]:
        if run.task_id is None:
            return None, []
        statement = select(TaskMessage).where(
            TaskMessage.workspace_id == run.workspace_id,
            TaskMessage.task_id == run.task_id,
            TaskMessage.message_type == "task.control.add_instruction",
        )
        previous = self._event(run, "delivered")
        if previous is not None:
            raw = previous.event_metadata.get("message_ids", [])
            ids = [UUID(str(item)) for item in raw] if isinstance(raw, list) else []
            statement = statement.where(TaskMessage.id.in_(ids))
        # A resumed SDK state retains the original input. An instruction arriving
        # during execution applies to a subsequent Run, never to this resumed state.
        messages = list(self.session.scalars(statement.order_by(TaskMessage.sequence.asc())))
        if not messages:
            return None, []
        return ContextFragment(
            key="task.instructions",
            text="Additional user instructions:\n" + "\n".join(row.body for row in messages),
            priority=ContextPriority.CRITICAL,
            required=True,
            allow_truncation=False,
        ), [str(row.id) for row in messages]

    def delivered(self, run: AgentRun, message_ids: list[str]) -> None:
        if self._event(run, "delivered") is None:
            # Persist an empty snapshot too, so a later provider fallback/approval
            # resume does not silently absorb a new instruction into an active Run.
            RunEventRecorder(self.session).append_event(
                run,
                "task.instructions.delivered",
                "Task instruction snapshot frozen for Run",
                {"message_ids": message_ids, "delivery_mode": "next_run"},
            )

    def consumed(self, run: AgentRun) -> None:
        delivered = self._event(run, "delivered")
        if delivered is not None and self._event(run, "consumed") is None:
            RunEventRecorder(self.session).append_event(
                run,
                "task.instructions.consumed",
                "SDK returned after receiving task instructions",
                {"message_ids": delivered.event_metadata.get("message_ids", [])},
            )
