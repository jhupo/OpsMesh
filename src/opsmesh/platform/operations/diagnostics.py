from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from opsmesh.governance.security_events.models import SecurityEvent
from opsmesh.orchestration.approvals.models import Approval
from opsmesh.orchestration.runs.models import AgentRun
from opsmesh.orchestration.tasks.models import Task
from opsmesh.platform.operations.schemas import (
    FailureCodeCount,
    PlatformExecutionSummary,
    SecurityReasonCount,
)
from opsmesh.shared.security.redaction import redact_sensitive_text


class PlatformExecutionDiagnostics:
    def __init__(self, session: Session) -> None:
        self._session = session

    def summary(self, *, window_seconds: int) -> PlatformExecutionSummary:
        cutoff = datetime.now(UTC) - timedelta(seconds=window_seconds)
        failure_code = func.coalesce(AgentRun.error["code"].astext, "unknown")
        failures = (
            self._session.execute(
                select(failure_code, func.count())
                .where(
                    AgentRun.status == "failed",
                    AgentRun.updated_at >= cutoff,
                )
                .group_by(failure_code)
                .order_by(func.count().desc(), failure_code)
                .limit(10)
            )
            .tuples()
            .all()
        )
        reasons = (
            self._session.execute(
                select(SecurityEvent.reason, func.count())
                .where(
                    SecurityEvent.created_at >= cutoff,
                    SecurityEvent.outcome == "denied",
                )
                .group_by(SecurityEvent.reason)
                .order_by(func.count().desc(), SecurityEvent.reason)
                .limit(10)
            )
            .tuples()
            .all()
        )
        pending = (
            self._session.scalar(
                select(func.count()).select_from(Approval).where(Approval.status == "pending")
            )
            or 0
        )
        waiting = (
            self._session.scalar(
                select(func.count())
                .select_from(AgentRun)
                .where(AgentRun.status.in_(("waiting_approval", "waiting_subworkflow")))
            )
            or 0
        )
        blocked = (
            self._session.scalar(
                select(func.count())
                .select_from(Task)
                .where(Task.status.in_(("blocked", "waiting_approval")))
            )
            or 0
        )
        return PlatformExecutionSummary(
            window_seconds=window_seconds,
            pending_approvals=pending,
            waiting_runs=waiting,
            blocked_tasks=blocked,
            top_failure_codes=[
                FailureCodeCount(code=redact_sensitive_text(code), count=count)
                for code, count in failures
            ],
            top_security_reasons=[
                SecurityReasonCount(reason=redact_sensitive_text(reason), count=count)
                for reason, count in reasons
            ],
        )
