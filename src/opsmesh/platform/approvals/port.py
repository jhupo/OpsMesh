"""Immutable request views and owner-controlled transaction/query operations."""

from collections.abc import Callable
from dataclasses import fields
from datetime import datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from opsmesh.platform.approvals import service
from opsmesh.platform.approvals.models import ApprovalRequest
from opsmesh.shared.approvals import (
    ApprovalDecisionNotifier,
    ApprovalSnapshot,
    ApprovalSubject,
    ApprovalTarget,
)
from opsmesh.shared.audit import AuditSubject
from opsmesh.shared.serialization import canonical_json


def snapshot(request: ApprovalRequest) -> ApprovalSnapshot:
    values = {
        field.name: getattr(request, field.name)
        for field in fields(ApprovalSnapshot)
        if field.name != "details_json"
    }
    return ApprovalSnapshot(**values, details_json=canonical_json(request.details))


class PlatformApprovals:
    """Keep mutable records, request locking and decision authorization in one owner."""

    def __init__(
        self,
        notifier: ApprovalDecisionNotifier,
        prepare_decision: Callable[[Session, ApprovalSnapshot, ApprovalSubject, str], None],
    ) -> None:
        self.notifier = notifier
        self.prepare_decision = prepare_decision

    def get(
        self, session: Session, approval_id: UUID | None, *, lock: bool = False
    ) -> ApprovalSnapshot | None:
        if approval_id is None:
            return None
        record = session.get(
            ApprovalRequest, approval_id, with_for_update=lock, populate_existing=lock
        )
        return snapshot(record) if record is not None else None

    def create(
        self,
        session: Session,
        *,
        kind: str,
        target: ApprovalTarget,
        title: str,
        summary: str,
        details: dict[str, object],
        approver_user_id: UUID | None = None,
        approver_role: str | None = None,
        requester: AuditSubject | None = None,
        requester_installation_id: UUID | None = None,
        requester_invocation_id: UUID | None = None,
        expires_at: datetime | None = None,
        request_key: str = "default",
    ) -> ApprovalSnapshot:
        return snapshot(
            service.create_request(
                session,
                kind=kind,
                target=target,
                title=title,
                summary=summary,
                details=details,
                approver_user_id=approver_user_id,
                approver_role=approver_role,
                requester=requester,
                requester_installation_id=requester_installation_id,
                requester_invocation_id=requester_invocation_id,
                expires_at=expires_at,
                request_key=request_key,
            )
        )

    def cancel(
        self, session: Session, *, approval_id: UUID, actor: ApprovalSubject, reason: str | None
    ) -> ApprovalSnapshot:
        return snapshot(
            service.cancel_request(session, approval_id=approval_id, actor=actor, reason=reason)
        )

    def decide(
        self,
        session: Session,
        *,
        approval_id: UUID,
        decision: Literal["approved", "rejected"],
        decided_by: ApprovalSubject,
        reason: str | None,
        idempotency_key: str,
    ) -> ApprovalSnapshot:
        record = service.get_request(session, approval_id)
        if not service.may_decide(record, decided_by):
            from opsmesh.shared.errors import ForbiddenError

            raise ForbiddenError("Only the assigned approver may decide this request")
        self.prepare_decision(session, snapshot(record), decided_by, decision)
        return snapshot(
            service.decide(
                session,
                approval_id=approval_id,
                decision=decision,
                decided_by=decided_by,
                reason=reason,
                idempotency_key=idempotency_key,
                notifier=self.notifier,
            )
        )

    def search(
        self,
        session: Session,
        *,
        kind: str,
        target_id: str | None = None,
        requester_user_id: UUID | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[ApprovalSnapshot], int]:
        if not 1 <= limit <= 500 or offset < 0:
            raise ValueError("Approval search must be bounded")
        filters = [ApprovalRequest.kind == kind]
        if target_id is not None:
            filters.append(ApprovalRequest.target_id == target_id)
        if requester_user_id is not None:
            filters.append(ApprovalRequest.requester_user_id == requester_user_id)
        if status is not None:
            filters.append(ApprovalRequest.status == status)
        total = (
            session.scalar(select(func.count()).select_from(ApprovalRequest).where(*filters)) or 0
        )
        records = session.scalars(
            select(ApprovalRequest)
            .where(*filters)
            .order_by(ApprovalRequest.created_at.desc(), ApprovalRequest.id)
            .limit(limit)
            .offset(offset)
        )
        return [snapshot(record) for record in records], total

    def approved_targets(self, *, kind: str) -> Select[tuple[str]]:
        return select(ApprovalRequest.target_id).where(
            ApprovalRequest.kind == kind, ApprovalRequest.status == "approved"
        )
