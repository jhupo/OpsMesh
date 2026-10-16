"""Approval inbox, detail, approve and reject APIs.

Governance never imports identity: the deciding subject is a placeholder
dependency that bootstrap replaces with the assembled identity channel, and
the target notifier is the contract the producers register later.
"""

from __future__ import annotations

from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from opsmesh.governance.approvals import service
from opsmesh.governance.approvals.models import APPROVAL_STATUSES, ApprovalRequest
from opsmesh.governance.approvals.schemas import (
    ApprovalDecisionRequest,
    ApprovalPage,
    ApprovalRequestView,
)
from opsmesh.governance.approvals.service import ApprovalSubject
from opsmesh.governance.approvals.targets import ApprovalDecisionNotifier
from opsmesh.shared.db.session import session_transaction
from opsmesh.shared.errors import ForbiddenError, InvalidArgumentError
from opsmesh.shared.http.dependencies import get_db_session
from opsmesh.shared.pagination import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT, PageParams

router = APIRouter(prefix="/governance/approvals", tags=["governance"])

DbSession = Annotated[Session, Depends(get_db_session)]


def require_approval_subject() -> ApprovalSubject:
    """Placeholder for the deciding subject; bootstrap injects identity."""
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Approval decisions are not assembled",
    )


def get_approval_notifier(request: Request) -> ApprovalDecisionNotifier:
    notifier = getattr(request.app.state, "approval_notifier", None)
    if notifier is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Approval notification is not assembled",
        )
    return cast(ApprovalDecisionNotifier, notifier)


SubjectDep = Annotated[ApprovalSubject, Depends(require_approval_subject)]
NotifierDep = Annotated[ApprovalDecisionNotifier, Depends(get_approval_notifier)]


def _view(request: ApprovalRequest) -> ApprovalRequestView:
    return ApprovalRequestView(
        approval_id=request.id,
        kind=request.kind,
        status=service.effective_status(request),
        title=request.title,
        summary=request.summary,
        details=request.details,
        target_type=request.target_type,
        target_id=request.target_id,
        target_version=request.target_version,
        approver_user_id=request.approver_user_id,
        requester_user_id=request.requester_user_id,
        expires_at=request.expires_at,
        created_at=request.created_at,
        decided_at=request.decided_at,
        decided_by_user_id=request.decided_by_user_id,
        decision_reason=request.decision_reason,
    )


@router.get("", response_model=ApprovalPage, operation_id="governance.approvals.list")
def list_approvals(
    session: DbSession,
    subject: SubjectDep,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_LIMIT)] = DEFAULT_PAGE_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
    status_filter: Annotated[str | None, Query(alias="status", max_length=20)] = None,
) -> ApprovalPage:
    """The requests assigned to the current subject."""
    if status_filter is not None and status_filter not in APPROVAL_STATUSES:
        raise InvalidArgumentError("The approval status filter is unknown")
    page = PageParams(limit=limit, offset=offset)
    requests, total = service.list_inbox(
        session, approver_user_id=subject.user_id, page=page, status=status_filter
    )
    return ApprovalPage(approvals=[_view(request) for request in requests], total=total)


@router.get(
    "/{approval_id}",
    response_model=ApprovalRequestView,
    operation_id="governance.approvals.read",
)
def read_approval(
    approval_id: UUID, session: DbSession, subject: SubjectDep
) -> ApprovalRequestView:
    request = service.get_request(session, approval_id)
    if not service.may_read(request, subject):
        raise ForbiddenError(
            "This approval request is not visible to the current subject",
            details={"approval_id": str(request.id)},
        )
    return _view(request)


@router.post(
    "/{approval_id}/approve",
    response_model=ApprovalRequestView,
    operation_id="governance.approvals.approve",
)
def approve_approval(
    approval_id: UUID,
    payload: ApprovalDecisionRequest,
    session: DbSession,
    subject: SubjectDep,
    notifier: NotifierDep,
) -> ApprovalRequestView:
    return _decide(
        session, approval_id=approval_id, payload=payload, subject=subject,
        notifier=notifier, decision="approved",
    )


@router.post(
    "/{approval_id}/reject",
    response_model=ApprovalRequestView,
    operation_id="governance.approvals.reject",
)
def reject_approval(
    approval_id: UUID,
    payload: ApprovalDecisionRequest,
    session: DbSession,
    subject: SubjectDep,
    notifier: NotifierDep,
) -> ApprovalRequestView:
    return _decide(
        session, approval_id=approval_id, payload=payload, subject=subject,
        notifier=notifier, decision="rejected",
    )


def _decide(
    session: Session,
    *,
    approval_id: UUID,
    payload: ApprovalDecisionRequest,
    subject: ApprovalSubject,
    notifier: ApprovalDecisionNotifier,
    decision: service.ApprovalDecision,
) -> ApprovalRequestView:
    with session_transaction(session):
        request = service.decide(
            session,
            approval_id=approval_id,
            decision=decision,
            decided_by=subject,
            reason=payload.reason,
            idempotency_key=payload.idempotency_key,
            notifier=notifier,
        )
    return _view(request)
