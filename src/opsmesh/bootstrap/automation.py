"""Bind automation to the existing owned Agent and Workflow run services."""

from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.execution.instances.access import get_instance
from opsmesh.execution.runs.access import get_run
from opsmesh.execution.runs.schemas import RunCreate
from opsmesh.execution.runs.service import cancel_run, submit_run
from opsmesh.platform.billing.models import ModelCharge
from opsmesh.platform.targets import TargetPorts
from opsmesh.shared.security.encryption import SecretCipher
from opsmesh.temporal.dispatch.outbox import stage_execution
from opsmesh.users.access import CurrentUser
from opsmesh.workflows.engines.temporal.engine import TemporalWorkflowEngine
from opsmesh.workflows.engines.temporal.evaluation_workflow import EvaluationCommand
from opsmesh.workflows.engines.temporal.execution_dispatch import (
    dispatch_cancellation,
    dispatch_run,
)
from opsmesh.workflows.instances.access import get_instance as get_workflow
from opsmesh.workflows.runs.access import get_run as get_workflow_run
from opsmesh.workflows.runs.control import cancel_run as cancel_workflow
from opsmesh.workflows.runs.models import WorkflowNodeRun
from opsmesh.workflows.runs.schemas import RunCreate as WorkflowCreate
from opsmesh.workflows.runs.service import submit_run as submit_workflow


def target_ports(cipher: SecretCipher) -> TargetPorts:
    def authorize(session: Session, actor: CurrentUser, kind: str, identifier: UUID) -> None:
        (get_instance if kind == "agent" else get_workflow)(
            session, actor=actor, instance_id=identifier
        )

    def submit(
        session: Session, actor: CurrentUser, kind: str, identifier: UUID, value: object, key: str
    ) -> UUID:
        if kind == "agent":
            run = submit_run(
                session,
                actor=actor,
                cipher=cipher,
                request=RunCreate(
                    instance_id=identifier, driver="temporal", request_key=key, input=value
                ),
            )
            dispatch_run(session, run.id)
            return run.id
        workflow = submit_workflow(
            session,
            actor=actor,
            cipher=cipher,
            request=WorkflowCreate(instance_id=identifier, request_key=key, input=value),
        )
        TemporalWorkflowEngine(session).start(workflow.id)
        return workflow.id

    def result(
        session: Session, actor: CurrentUser, kind: str, identifier: UUID
    ) -> dict[str, object]:
        row = (get_run if kind == "agent" else get_workflow_run)(
            session, actor=actor, run_id=identifier
        )
        payload = cipher.decrypt_payload(row.encrypted_result) if row.encrypted_result else {}
        result: dict[str, object] = {
            "run_id": str(row.id),
            "status": row.status,
            "output": payload.get("output"),
            "error_code": row.error_code,
            "created_at": row.created_at.isoformat(),
            "updated_at": row.updated_at.isoformat(),
        }
        if row.status in {"completed", "failed", "cancelled"}:
            query = select(ModelCharge).where(ModelCharge.user_id == actor.user_id)
            query = (
                query.where(ModelCharge.run_id == identifier)
                if kind == "agent"
                else query.where(
                    ModelCharge.run_id.in_(
                        select(WorkflowNodeRun.execution_run_id).where(
                            WorkflowNodeRun.run_id == identifier
                        )
                    )
                )
            )
            charges = list(session.scalars(query))
            costs: dict[str, str] = {}
            for charge in charges:
                if charge.currency is not None and charge.amount is not None:
                    costs[charge.currency] = str(
                        Decimal(costs.get(charge.currency, "0")) + charge.amount
                    )
            result["usage"] = {
                "input_tokens": sum(c.input_tokens or 0 for c in charges),
                "output_tokens": sum(c.output_tokens or 0 for c in charges),
                "unconfirmed_operations": sum(c.status == "reserved" for c in charges),
                "unpriced_operations": sum(c.pricing is None for c in charges),
                "costs": costs,
            }
            result["elapsed_seconds"] = (row.updated_at - row.created_at).total_seconds()
        return result

    def cancel(session: Session, actor: CurrentUser, kind: str, identifier: UUID) -> None:
        current = (get_run if kind == "agent" else get_workflow_run)(
            session, actor=actor, run_id=identifier
        )
        if current.status in {"completed", "failed", "cancelled", "cancelling", "needs_attention"}:
            return
        if kind == "agent":
            run = cancel_run(session, actor=actor, run_id=identifier, cipher=cipher)
            dispatch_cancellation(session, run)
        else:
            cancel_workflow(session, actor=actor, run_id=identifier, cipher=cipher)
            TemporalWorkflowEngine(session).cancel(identifier)

    return TargetPorts(authorize, submit, result, cancel)


def dispatch_evaluation(session: Session, identifier: UUID) -> None:
    stage_execution(
        session, EvaluationCommand(execution_id=identifier, workflow_id=f"evaluation:{identifier}")
    )
