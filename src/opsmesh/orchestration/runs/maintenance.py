"""Composition-owned state and explicit execution operations."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from opsmesh.orchestration.runs.model_operations import ModelOperation
from opsmesh.orchestration.runs.models import FlowRun, OperationReceipt, Run
from opsmesh.orchestration.runtime.models import EnvironmentOperationReceipt, MCPOperationReceipt


def unsettled_execution_count(session: Session) -> int:
    total = (
        session.scalar(
            select(func.count())
            .select_from(Run)
            .where(
                Run.status.in_(
                    ("running", "preparing", "cancelling", "finalizing", "needs_attention")
                )
            )
        )
        or 0
    )
    for model in (
        ModelOperation,
        OperationReceipt,
        EnvironmentOperationReceipt,
        MCPOperationReceipt,
    ):
        total += (
            session.scalar(
                select(func.count())
                .select_from(model)
                .where(model.status.in_(("started", "uncertain", "needs_attention")))
            )
            or 0
        )
    return total


def unsettled_flow_count(session: Session) -> int:
    return (
        session.scalar(
            select(func.count())
            .select_from(FlowRun)
            .where((FlowRun.active_steps > 0) | (FlowRun.status == "needs_attention"))
        )
        or 0
    )
