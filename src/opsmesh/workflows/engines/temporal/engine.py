"""Stage Temporal commands in the same transaction as workflow product records."""

from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from opsmesh.temporal.dispatch.outbox import stage_cancellation, stage_execution, stage_signal
from opsmesh.workflows.engines.temporal.workflow import WorkflowCommand


class TemporalWorkflowEngine:
    name = "temporal"

    def __init__(self, session: Session) -> None:
        self.session = session

    def start(self, run_id: UUID) -> None:
        stage_execution(
            self.session, WorkflowCommand(execution_id=run_id, workflow_id=f"workflow:{run_id}")
        )

    def cancel(self, run_id: UUID) -> None:
        stage_cancellation(self.session, dispatch_id=uuid4(), workflow_id=f"workflow:{run_id}")

    def signal(self, run_id: UUID, signal_id: UUID) -> None:
        stage_signal(
            self.session,
            dispatch_id=signal_id,
            workflow_id=f"workflow:{run_id}",
            signal_name="wake",
            signal_args=(str(signal_id),),
        )
