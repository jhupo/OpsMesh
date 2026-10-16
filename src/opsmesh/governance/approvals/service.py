"""Approval service: create requests, authorize and record one decision.

A decision is written exactly once: the row is locked, the pending state is
re-checked under that lock, and only a replay of the very same decision
idempotency key returns the recorded outcome instead of conflicting. The
effective decision is audited and handed to the target owner inside the same
transaction.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal, Protocol
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from opsmesh.governance.approvals.models import (
    APPROVAL_CANCELLED,
    APPROVAL_EXPIRED,
    APPROVAL_PENDING,
    ApprovalRequest,
)
from opsmesh.governance.approvals.targets import (
    ApprovalDecisionNotifier,
    ApprovalTarget,
)
from opsmesh.governance.audit.service import AuditActor, append_platform_event
from opsmesh.shared.errors import ConflictError, ForbiddenError, NotFoundError
from opsmesh.shared.pagination import PageParams
from opsmesh.shared.serialization import payload_digest

ApprovalDecision = Literal["approved", "rejected"]


class ApprovalSubject(Protocol):
    """The verified subject that requests or decides; identity owns the value."""

    @property
    def user_id(self) -> UUID: ...

    def has_platform_role(self, role: str) -> bool: ...


def create_request(
    session: Session,
    *,
    kind: str,
    target: ApprovalTarget,
    title: str,
    summary: str,
    details: dict[str, object],
    approver_user_id: UUID,
    requester: ApprovalSubject | None = None,
    requester_installation_id: UUID | None = None,
    requester_invocation_id: UUID | None = None,
    expires_at: datetime | None = None,
) -> ApprovalRequest:
    """Register one approval request; the same target and kind replays it."""
    declared = {
        "title": title,
        "summary": summary,
        "details": details,
        "approver_user_id": str(approver_user_id),
        "target_version": target.target_version,
        "expires_at": expires_at.isoformat() if expires_at else None,
    }
    existing = session.scalar(
        select(ApprovalRequest).where(
            ApprovalRequest.kind == kind,
            ApprovalRequest.target_type == target.target_type,
            ApprovalRequest.target_id == target.target_id,
        )
    )
    if existing is not None:
        if existing.input_digest != payload_digest(declared):
            raise ConflictError(
                "The approval target already carries a different request",
                details={"approval_id": str(existing.id)},
            )
        return existing
    request = ApprovalRequest(
        kind=kind,
        status=APPROVAL_PENDING,
        title=title,
        summary=summary,
        details=details,
        target_type=target.target_type,
        target_id=target.target_id,
        target_version=target.target_version,
        approver_user_id=approver_user_id,
        requester_user_id=requester.user_id if requester is not None else None,
        requester_installation_id=requester_installation_id,
        requester_invocation_id=requester_invocation_id,
        expires_at=expires_at,
        input_digest=payload_digest(declared),
    )
    session.add(request)
    session.flush()
    append_platform_event(
        session,
        actor=AuditActor(
            actor_type="delegation" if requester_installation_id is not None else "user",
            actor_id=str(requester.user_id) if requester is not None else None,
            installation_id=requester_installation_id,
            invocation_id=requester_invocation_id,
        ),
        action="approval.requested",
        target_type="approval_request",
        target_id=str(request.id),
        summary=title,
        details={"kind": kind, "target_type": target.target_type, "target_id": target.target_id},
    )
    return request


def decide(
    session: Session,
    *,
    approval_id: UUID,
    decision: ApprovalDecision,
    decided_by: ApprovalSubject,
    reason: str | None,
    idempotency_key: str,
    notifier: ApprovalDecisionNotifier,
) -> ApprovalRequest:
    """Record the one decision of one request and notify its target owner."""
    request = session.scalar(
        select(ApprovalRequest).where(ApprovalRequest.id == approval_id).with_for_update()
    )
    if request is None:
        raise NotFoundError("The approval request does not exist")
    if request.status != APPROVAL_PENDING:
        if (
            request.decision_idempotency_key == idempotency_key
            and request.status == decision
        ):
            return request
        raise ConflictError(
            "The approval request is already decided",
            details={"status": request.status},
        )
    if request.expires_at is not None and request.expires_at <= datetime.now(UTC):
        raise ConflictError(
            "The approval request has expired", details={"status": APPROVAL_EXPIRED}
        )
    if request.approver_user_id != decided_by.user_id:
        raise ForbiddenError(
            "Only the assigned approver may decide this request",
            details={"approval_id": str(request.id)},
        )
    now = datetime.now(UTC)
    request.status = decision
    request.decided_by_user_id = decided_by.user_id
    request.decided_at = now
    request.decision_reason = reason
    request.decision_idempotency_key = idempotency_key
    append_platform_event(
        session,
        actor=AuditActor(actor_type="user", actor_id=str(decided_by.user_id)),
        action=f"approval.{decision}",
        target_type="approval_request",
        target_id=str(request.id),
        summary=f"Approval request {decision}",
        details={"kind": request.kind, "reason": reason},
    )
    notifier.notify_decided(
        session,
        approval_id=request.id,
        decision=decision,
        target=ApprovalTarget(
            target_type=request.target_type,
            target_id=request.target_id,
            target_version=request.target_version,
        ),
        decided_by_user_id=decided_by.user_id,
        reason=reason,
    )
    return request


def cancel_request(
    session: Session,
    *,
    approval_id: UUID,
    actor: ApprovalSubject,
    reason: str | None,
) -> ApprovalRequest:
    """Withdraw one pending request; a decided request is never cancelled."""
    request = session.scalar(
        select(ApprovalRequest).where(ApprovalRequest.id == approval_id).with_for_update()
    )
    if request is None:
        raise NotFoundError("The approval request does not exist")
    if request.status != APPROVAL_PENDING:
        raise ConflictError(
            "The approval request is already decided",
            details={"status": request.status},
        )
    request.status = APPROVAL_CANCELLED
    request.cancelled_reason = reason
    append_platform_event(
        session,
        actor=AuditActor(actor_type="user", actor_id=str(actor.user_id)),
        action="approval.cancelled",
        target_type="approval_request",
        target_id=str(request.id),
        summary="Approval request cancelled",
        details={"reason": reason},
    )
    return request


def get_request(session: Session, approval_id: UUID) -> ApprovalRequest:
    request = session.get(ApprovalRequest, approval_id)
    if request is None:
        raise NotFoundError("The approval request does not exist")
    return request


def list_inbox(
    session: Session,
    *,
    approver_user_id: UUID,
    page: PageParams,
    status: str | None = None,
) -> tuple[list[ApprovalRequest], int]:
    """One offset page of the requests assigned to one approver."""
    query = select(ApprovalRequest).where(
        ApprovalRequest.approver_user_id == approver_user_id
    )
    count_query = (
        select(func.count())
        .select_from(ApprovalRequest)
        .where(ApprovalRequest.approver_user_id == approver_user_id)
    )
    if status is not None:
        query = query.where(ApprovalRequest.status == status)
        count_query = count_query.where(ApprovalRequest.status == status)
    total = session.scalar(count_query) or 0
    rows = list(
        session.scalars(
            query.order_by(ApprovalRequest.created_at.desc())
            .limit(page.limit)
            .offset(page.offset)
        )
    )
    return rows, total


def effective_status(request: ApprovalRequest, *, now: datetime | None = None) -> str:
    """The presented state of one request; expiry is derived, never rewritten."""
    if request.status != APPROVAL_PENDING:
        return request.status
    if request.expires_at is not None and request.expires_at <= (now or datetime.now(UTC)):
        return APPROVAL_EXPIRED
    return APPROVAL_PENDING


def may_read(request: ApprovalRequest, reader: ApprovalSubject) -> bool:
    """The assigned approver reads the request; platform administrators too."""
    return request.approver_user_id == reader.user_id or reader.has_platform_role("admin")
