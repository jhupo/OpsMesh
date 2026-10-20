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

from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from opsmesh.platform.approvals.models import (
    APPROVAL_CANCELLED,
    APPROVAL_EXPIRED,
    APPROVAL_PENDING,
    ApprovalRequest,
)
from opsmesh.shared.approvals import ApprovalDecisionNotifier, ApprovalSnapshot, ApprovalTarget
from opsmesh.shared.audit import AuditActor, AuditRecord, AuditSubject, record_audit
from opsmesh.shared.errors import ConflictError, ForbiddenError, InvalidArgumentError, NotFoundError
from opsmesh.shared.pagination import PageParams
from opsmesh.shared.serialization import canonical_json, payload_digest

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
    approver_user_id: UUID | None = None,
    approver_role: str | None = None,
    request_key: str = "default",
    requester: AuditSubject | None = None,
    requester_installation_id: UUID | None = None,
    requester_invocation_id: UUID | None = None,
    expires_at: datetime | None = None,
) -> ApprovalRequest:
    """Register one approval request; the same target and kind replays it."""
    if (approver_user_id is None) == (approver_role is None):
        raise InvalidArgumentError("Exactly one approver user or platform role is required")
    if not request_key or len(request_key) > 200:
        raise InvalidArgumentError("An approval requires a stable attempt key")
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"platform.approval:{kind}:{target.target_type}:{target.target_id}:{request_key}"},
    )
    declared = {
        "requester_user_id": str(requester.user_id) if requester else None,
        "requester_installation_id": str(requester_installation_id)
        if requester_installation_id
        else None,
        "requester_invocation_id": str(requester_invocation_id)
        if requester_invocation_id
        else None,
        "approver_role": approver_role,
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
            ApprovalRequest.request_key == request_key,
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
        request_key=request_key,
        approver_role=approver_role,
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
    record_audit(
        session,
        AuditRecord(
            category="approval",
            action="approval.requested",
            actor=AuditActor(
                actor_type="delegation" if requester_installation_id is not None else "user",
                actor_id=str(requester.user_id) if requester is not None else None,
                installation_id=requester_installation_id,
                invocation_id=requester_invocation_id,
            ),
            source="platform",
            target_type="approval_request",
            target_id=str(request.id),
            summary=title,
            payload_json=canonical_json(
                {"kind": kind, "target_type": target.target_type, "target_id": target.target_id}
            ),
        ),
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
        select(ApprovalRequest)
        .where(ApprovalRequest.id == approval_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if request is None:
        raise NotFoundError("The approval request does not exist")
    if not may_decide(request, decided_by):
        # Authorization precedes every return branch: an idempotency key is
        # not a read credential, so a replay by anyone else is refused
        # exactly like a first decision would be.
        raise ForbiddenError(
            "Only the assigned approver may decide this request",
            details={"approval_id": str(request.id)},
        )
    if request.status != APPROVAL_PENDING:
        if (
            request.decision_idempotency_key == idempotency_key
            and request.status == decision
            and request.decided_by_user_id == decided_by.user_id
            and request.decision_reason == reason
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
    if not may_decide(request, decided_by):
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
    record_audit(
        session,
        AuditRecord(
            category="approval",
            action=f"approval.{decision}",
            actor=AuditActor(actor_type="user", actor_id=str(decided_by.user_id)),
            source="platform",
            target_type="approval_request",
            target_id=str(request.id),
            summary=f"Approval request {decision}",
            payload_json=canonical_json({"kind": request.kind, "reason": reason}),
        ),
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
        select(ApprovalRequest)
        .where(ApprovalRequest.id == approval_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if request is None:
        raise NotFoundError("The approval request does not exist")
    if request.requester_user_id != actor.user_id and not actor.has_platform_role("admin"):
        raise ForbiddenError("Only the requester or a platform administrator may cancel approval")
    if request.status != APPROVAL_PENDING:
        raise ConflictError(
            "The approval request is already decided",
            details={"status": request.status},
        )
    request.status = APPROVAL_CANCELLED
    request.cancelled_reason = reason
    record_audit(
        session,
        AuditRecord(
            category="approval",
            action="approval.cancelled",
            actor=AuditActor(actor_type="user", actor_id=str(actor.user_id)),
            source="platform",
            target_type="approval_request",
            target_id=str(request.id),
            summary="Approval request cancelled",
            payload_json=canonical_json({"reason": reason}),
        ),
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
    approver_roles: tuple[str, ...] = (),
    page: PageParams,
    status: str | None = None,
) -> tuple[list[ApprovalRequest], int]:
    """One offset page of the requests assigned to one approver."""
    assignment = or_(
        ApprovalRequest.approver_user_id == approver_user_id,
        ApprovalRequest.approver_role.in_(approver_roles),
    )
    query = select(ApprovalRequest).where(assignment)
    count_query = select(func.count()).select_from(ApprovalRequest).where(assignment)
    if status is not None:
        query = query.where(ApprovalRequest.status == status)
        count_query = count_query.where(ApprovalRequest.status == status)
    total = session.scalar(count_query) or 0
    rows = list(
        session.scalars(
            query.order_by(ApprovalRequest.created_at.desc()).limit(page.limit).offset(page.offset)
        )
    )
    return rows, total


def effective_status(
    request: ApprovalRequest | ApprovalSnapshot, *, now: datetime | None = None
) -> str:
    """The presented state of one request; expiry is derived, never rewritten."""
    if request.status != APPROVAL_PENDING:
        return request.status
    if request.expires_at is not None and request.expires_at <= (now or datetime.now(UTC)):
        return APPROVAL_EXPIRED
    return APPROVAL_PENDING


def may_read(request: ApprovalRequest, reader: ApprovalSubject) -> bool:
    """The assigned approver reads the request; platform administrators too."""
    return (
        request.requester_user_id == reader.user_id
        or may_decide(request, reader)
        or reader.has_platform_role("admin")
    )


def may_decide(request: ApprovalRequest, subject: ApprovalSubject) -> bool:
    return request.approver_user_id == subject.user_id or (
        request.approver_role is not None and subject.has_platform_role(request.approver_role)
    )
