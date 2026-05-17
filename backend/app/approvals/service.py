from datetime import UTC, datetime
from typing import TypeVar
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from backend.app.api.pagination import PageParams
from backend.app.approvals.models import Approval
from backend.app.audit.service import AuditService
from backend.app.runs.models import AgentRun
from backend.app.runs.status import RunStatus
from backend.app.tasks.models import Task
from backend.app.tasks.status import TaskStatus
from backend.app.workers.jobs import JobPayload, JobType
from backend.app.workers.queue import RedisQueue

T = TypeVar("T")


class ApprovalService:
    def __init__(self, session: Session, queue: RedisQueue | None = None) -> None:
        self._session = session
        self._queue = queue

    def create_approval(
        self,
        *,
        workspace_id: UUID,
        task_id: UUID | None,
        agent_run_id: UUID | None,
        requested_by_agent_profile_id: UUID | None,
        approval_type: str,
        risk_level: str,
        payload: dict[str, object],
    ) -> Approval:
        approval = Approval(
            workspace_id=workspace_id,
            task_id=task_id,
            agent_run_id=agent_run_id,
            requested_by_agent_profile_id=requested_by_agent_profile_id,
            approval_type=approval_type,
            risk_level=risk_level,
            payload=payload,
            created_at=datetime.now(UTC),
        )
        self._session.add(approval)
        self._session.flush()
        return approval

    def list_approvals(
        self,
        workspace_id: UUID,
        page: PageParams,
        status: str | None = None,
    ) -> tuple[list[Approval], int]:
        statement = select(Approval).where(Approval.workspace_id == workspace_id)
        if status is not None:
            statement = statement.where(Approval.status == status)
        return self._page(statement.order_by(Approval.created_at.desc()), page)

    def approve(self, approval: Approval, user_id: UUID, reason: str | None = None) -> Approval:
        return self._decide(approval, user_id, "approved", reason)

    def reject(self, approval: Approval, user_id: UUID, reason: str | None = None) -> Approval:
        return self._decide(approval, user_id, "rejected", reason)

    def get_scoped(self, workspace_id: UUID, approval_id: UUID) -> Approval | None:
        approval = self._session.get(Approval, approval_id)
        if approval is None or approval.workspace_id != workspace_id:
            return None
        return approval

    def _decide(
        self,
        approval: Approval,
        user_id: UUID,
        status: str,
        reason: str | None,
    ) -> Approval:
        if approval.status != "pending":
            raise ValueError("Approval is not pending")
        approval.status = status
        approval.decided_by_user_id = user_id
        approval.decision_reason = reason
        approval.decided_at = datetime.now(UTC)
        self._append_audit_event(approval, user_id, f"approval.{status}")
        if status == "rejected":
            self._mark_run_failed(approval)
        if status == "approved" and approval.agent_run_id is not None and self._queue is not None:
            self._queue.enqueue(
                JobPayload(
                    workspace_id=approval.workspace_id,
                    job_type=JobType.AGENT_RUN,
                    resource_id=approval.agent_run_id,
                    requested_by_user_id=user_id,
                    idempotency_key=f"approval.resume:{approval.workspace_id}:{approval.id}",
                )
            )
        self._session.commit()
        self._session.refresh(approval)
        return approval

    def _mark_run_failed(self, approval: Approval) -> None:
        if approval.agent_run_id is not None:
            run = self._session.get(AgentRun, approval.agent_run_id)
            if run is not None:
                run.status = RunStatus.FAILED.value
                run.completed_at = datetime.now(UTC)
                run.error = {"code": "approval_rejected", "message": "Approval was rejected"}
        if approval.task_id is not None:
            task = self._session.get(Task, approval.task_id)
            if task is not None:
                task.status = TaskStatus.FAILED.value
                task.completed_at = datetime.now(UTC)

    def _append_audit_event(self, approval: Approval, user_id: UUID, action: str) -> None:
        AuditService(self._session).record_user_action(
            workspace_id=approval.workspace_id,
            user_id=user_id,
            action=action,
            target_type="approval",
            target_id=approval.id,
        )

    def _page(self, statement: Select[tuple[T]], page: PageParams) -> tuple[list[T], int]:
        total = self._session.scalar(
            select(func.count()).select_from(statement.order_by(None).subquery())
        )
        rows = self._session.scalars(statement.limit(page.limit).offset(page.offset)).all()
        return list(rows), int(total or 0)
