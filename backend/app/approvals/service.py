from datetime import UTC, datetime
from typing import TypeVar
from uuid import UUID

from sqlalchemy import Select, or_, select
from sqlalchemy.orm import Session

from backend.app.agents.models import AgentProfile
from backend.app.api.pagination import PageParams
from backend.app.approvals.models import Approval
from backend.app.audit.service import AuditService
from backend.app.capabilities.models import (
    Capability,
    McpCredentialReference,
    McpServer,
    McpToolAllowlist,
    Skill,
)
from backend.app.db.pagination import page_scalars
from backend.app.marketplace.models import MarketplaceListing, TalentListing
from backend.app.reviews.constants import (
    RESOURCE_STATUS_ACTIVE,
    RESOURCE_STATUS_REJECTED,
)
from backend.app.runs.models import AgentRun
from backend.app.runs.service import RunStateService
from backend.app.runs.status import RunStatus
from backend.app.tasks.models import Task
from backend.app.tasks.service import TaskStateService
from backend.app.tasks.status import TaskStatus
from backend.app.workers.jobs import JobPayload, JobType
from backend.app.workers.queue.redis_queue import RedisQueue

T = TypeVar("T")
ResourceReviewTarget = (
    AgentProfile
    | Capability
    | Skill
    | McpServer
    | McpToolAllowlist
    | McpCredentialReference
    | MarketplaceListing
    | TalentListing
)


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
        *,
        include_resource_reviews: bool = True,
    ) -> tuple[list[Approval], int]:
        statement = select(Approval).where(Approval.workspace_id == workspace_id)
        if status is not None:
            statement = statement.where(Approval.status == status)
        if not include_resource_reviews:
            payload_kind = Approval.payload["kind"].as_string()
            statement = statement.where(
                or_(
                    payload_kind.is_(None),
                    payload_kind != "resource_review",
                )
            )
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
        self._apply_resource_review_decision(approval, user_id, status)
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

    def _apply_resource_review_decision(
        self,
        approval: Approval,
        user_id: UUID,
        status: str,
    ) -> None:
        payload = approval.payload if isinstance(approval.payload, dict) else {}
        if payload.get("kind") != "resource_review":
            return
        target_type = str(payload.get("target_type") or "")
        target_id = _uuid_or_none(payload.get("target_id"))
        if target_id is None:
            return
        target = self._resource_review_target(approval.workspace_id, target_type, target_id)
        if target is None:
            return
        next_status = _review_target_next_status(target, status)
        target.status = next_status
        AuditService(self._session).record_user_action(
            workspace_id=approval.workspace_id,
            user_id=user_id,
            action=f"{target_type}.{next_status}",
            target_type=target_type,
            target_id=target_id,
            metadata={"approval_id": str(approval.id), "approval_type": approval.approval_type},
        )

    def _resource_review_target(
        self,
        workspace_id: UUID,
        target_type: str,
        target_id: UUID,
    ) -> ResourceReviewTarget | None:
        model_by_type = {
            "agent_profile": AgentProfile,
            "capability": Capability,
            "skill": Skill,
            "mcp_server": McpServer,
            "mcp_tool_allowlist": McpToolAllowlist,
            "mcp_credential_reference": McpCredentialReference,
            "marketplace_listing": MarketplaceListing,
            "talent_listing": TalentListing,
        }
        model = model_by_type.get(target_type)
        if model is None:
            return None
        target = self._session.get(model, target_id)
        if target is None:
            return None
        if isinstance(target, Capability):
            return target
        target_workspace_id = getattr(target, "workspace_id", None)
        if target_workspace_id is None:
            target_workspace_id = getattr(target, "owner_workspace_id", None)
        if target_workspace_id is None:
            target_workspace_id = getattr(target, "source_workspace_id", None)
        if target_workspace_id != workspace_id:
            return None
        return target

    def _mark_run_failed(self, approval: Approval) -> None:
        if approval.agent_run_id is not None:
            run = self._session.get(AgentRun, approval.agent_run_id)
            if run is not None:
                RunStateService().transition(
                    run,
                    RunStatus.FAILED,
                    completed_at=datetime.now(UTC),
                    error={"code": "approval_rejected", "message": "Approval was rejected"},
                )
        if approval.task_id is not None:
            task = self._session.get(Task, approval.task_id)
            if task is not None:
                TaskStateService().transition(
                    task,
                    TaskStatus.FAILED,
                    completed_at=datetime.now(UTC),
                )

    def _append_audit_event(self, approval: Approval, user_id: UUID, action: str) -> None:
        AuditService(self._session).record_user_action(
            workspace_id=approval.workspace_id,
            user_id=user_id,
            action=action,
            target_type="approval",
            target_id=approval.id,
        )

    def _page(self, statement: Select[tuple[T]], page: PageParams) -> tuple[list[T], int]:
        return page_scalars(self._session, statement, page)


def _review_target_next_status(target: ResourceReviewTarget, approval_status: str) -> str:
    if approval_status != "approved":
        return RESOURCE_STATUS_REJECTED
    if isinstance(target, MarketplaceListing | TalentListing):
        return "public"
    return RESOURCE_STATUS_ACTIVE


def _uuid_or_none(value: object) -> UUID | None:
    if isinstance(value, UUID):
        return value
    if isinstance(value, str):
        try:
            return UUID(value)
        except ValueError:
            return None
    return None
