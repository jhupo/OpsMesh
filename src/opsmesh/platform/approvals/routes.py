"""Approval inbox, detail, approve and reject APIs.

Governance never imports identity: the deciding subject is a placeholder
dependency that bootstrap replaces with the assembled identity channel, and
the target notifier is the contract the producers register later.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from opsmesh.identity.access import PlatformRole
from opsmesh.platform.approvals import service
from opsmesh.platform.approvals.models import APPROVAL_STATUSES, ApprovalRequest
from opsmesh.platform.approvals.schemas import (
    ApprovalDecisionRequest,
    ApprovalPage,
    ApprovalRequestView,
)
from opsmesh.platform.approvals.service import ApprovalSubject
from opsmesh.shared.approvals import ApprovalSnapshot, approvals_for
from opsmesh.shared.db.session import session_transaction
from opsmesh.shared.errors import ForbiddenError, InvalidArgumentError
from opsmesh.shared.http.dependencies import get_db_session
from opsmesh.shared.pagination import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT, PageParams

router = APIRouter(prefix="/platform/approvals", tags=["platform"])

DbSession = Annotated[Session, Depends(get_db_session)]


def require_approval_subject() -> ApprovalSubject:
    """Placeholder for the deciding subject; bootstrap injects identity."""
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Approval decisions are not assembled",
    )


SubjectDep = Annotated[ApprovalSubject, Depends(require_approval_subject)]


def _view(request: ApprovalRequest | ApprovalSnapshot) -> ApprovalRequestView:
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
        approver_role=request.approver_role,
        request_key=request.request_key,
        requester_user_id=request.requester_user_id,
        expires_at=request.expires_at,
        created_at=request.created_at,
        decided_at=request.decided_at,
        decided_by_user_id=request.decided_by_user_id,
        decision_reason=request.decision_reason,
    )


@router.get("", response_model=ApprovalPage, operation_id="platform.approvals.list")
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
        session,
        approver_user_id=subject.user_id,
        approver_roles=tuple(str(role) for role in PlatformRole if subject.has_platform_role(role)),
        page=page,
        status=status_filter,
    )
    return ApprovalPage(approvals=[_view(request) for request in requests], total=total)


@router.get(
    "/{approval_id}",
    response_model=ApprovalRequestView,
    operation_id="platform.approvals.read",
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
    operation_id="platform.approvals.approve",
)
def approve_approval(
    approval_id: UUID,
    payload: ApprovalDecisionRequest,
    session: DbSession,
    subject: SubjectDep,
) -> ApprovalRequestView:
    return _decide(
        session,
        approval_id=approval_id,
        payload=payload,
        subject=subject,
        decision="approved",
    )


@router.post(
    "/{approval_id}/reject",
    response_model=ApprovalRequestView,
    operation_id="platform.approvals.reject",
)
def reject_approval(
    approval_id: UUID,
    payload: ApprovalDecisionRequest,
    session: DbSession,
    subject: SubjectDep,
) -> ApprovalRequestView:
    return _decide(
        session,
        approval_id=approval_id,
        payload=payload,
        subject=subject,
        decision="rejected",
    )


def _decide(
    session: Session,
    *,
    approval_id: UUID,
    payload: ApprovalDecisionRequest,
    subject: ApprovalSubject,
    decision: service.ApprovalDecision,
) -> ApprovalRequestView:
    with session_transaction(session):
        request = approvals_for(session).decide(
            session,
            approval_id=approval_id,
            decision=decision,
            decided_by=subject,
            reason=payload.reason,
            idempotency_key=payload.idempotency_key,
        )
    return _view(request)
