"""Transactional Temporal intents injected into the engine independent HTTP API."""

from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from opsmesh.execution.runs.models import ExecutionRun
from opsmesh.execution.runtime.schemas import RuntimeCommand
from opsmesh.temporal.dispatch.outbox import stage_cancellation, stage_execution, stage_signal


class InstanceCommand(BaseModel):
    model_config = ConfigDict(frozen=True)
    execution_id: UUID
    workflow_id: str
    workflow_name: str = "AgentInstanceWorkflow"


class SandboxCommand(BaseModel):
    model_config = ConfigDict(frozen=True)
    execution_id: UUID
    environment_id: UUID
    workflow_id: str
    workflow_name: str = "InstanceSandboxWorkflow"


def dispatch_run(session: Session, run_id: UUID) -> None:
    row = session.get(ExecutionRun, run_id)
    if row is None or row.driver != "temporal":
        raise ValueError("Temporal dispatch requires an assigned run")
    if row.approval_id is not None:
        stage_signal(
            session,
            dispatch_id=row.approval_id,
            workflow_id=f"instance:{run_id}",
            signal_name="approval_decided",
            signal_args=(str(row.approval_id),),
        )
        return
    stage_execution(session, InstanceCommand(execution_id=run_id, workflow_id=f"instance:{run_id}"))


def dispatch_cancellation(session: Session, row: ExecutionRun) -> None:
    if row.driver == "temporal":
        stage_cancellation(session, dispatch_id=uuid4(), workflow_id=f"instance:{row.id}")


def dispatch_sandbox(session: Session, command: RuntimeCommand) -> None:
    stage_execution(
        session,
        SandboxCommand(
            **command.model_dump(), workflow_id=f"instance-sandbox:{command.execution_id}"
        ),
    )
