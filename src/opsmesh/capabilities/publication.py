"""Capability-owned publication targets backed by platform approval facts."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Select, cast, select
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import Session

from opsmesh.shared.approvals import ApprovalSnapshot, ApprovalTarget, approvals_for
from opsmesh.shared.audit import AuditActor
from opsmesh.shared.errors import ConflictError, NotFoundError

KIND = "capability_publication"


@dataclass(frozen=True)
class PublicationReview:
    approval: ApprovalSnapshot

    @property
    def id(self) -> UUID:
        return self.approval.id

    @property
    def version_id(self) -> UUID:
        return UUID(self.approval.target_id)

    @property
    def status(self) -> str:
        return self.approval.status

    @property
    def requested_by_user_id(self) -> UUID:
        value = self.approval.requester_user_id
        if value is None:
            raise ConflictError("Publication approval has no verified author")
        return value

    @property
    def submission_note(self) -> str:
        return self.approval.summary

    @property
    def reviewed_by_user_id(self) -> UUID | None:
        return self.approval.decided_by_user_id

    @property
    def decision_note(self) -> str | None:
        return self.approval.decision_reason

    @property
    def created_at(self) -> datetime:
        return self.approval.created_at

    @property
    def reviewed_at(self) -> datetime | None:
        return self.approval.decided_at


@dataclass(frozen=True)
class PublicationSubject:
    user_id: UUID
    is_admin: bool = False

    def has_platform_role(self, role: str) -> bool:
        return role == "admin" and self.is_admin


def get_review(session: Session, review_id: UUID, *, lock: bool = False) -> PublicationReview:
    approval = approvals_for(session).get(session, review_id, lock=lock)
    if approval is None or approval.kind != KIND or approval.target_type != "capability_version":
        raise NotFoundError("The publication review does not exist")
    return PublicationReview(approval)


def approved_versions(session: Session) -> Select[tuple[UUID]]:
    targets = approvals_for(session).approved_targets(kind=KIND).subquery()
    return select(cast(targets.c.target_id, PostgresUUID(as_uuid=True)))


def request_publication_review(
    session: Session,
    *,
    version_id: UUID,
    target_version: str,
    requester_id: UUID,
    submission_note: str,
) -> PublicationReview:
    pending, _ = approvals_for(session).search(
        session, kind=KIND, target_id=str(version_id), status="pending", limit=1
    )
    if pending:
        raise ConflictError("This version already has a pending publication review")
    return PublicationReview(
        approvals_for(session).create(
            session,
            kind=KIND,
            target=ApprovalTarget("capability_version", str(version_id), target_version),
            title="Review public capability release",
            summary=submission_note,
            details={},
            requester=PublicationSubject(requester_id),
            approver_role="admin",
            request_key=str(uuid4()),
        )
    )


def record_publication_decision(
    session: Session,
    *,
    review: PublicationReview,
    actor: AuditActor,
    actor_is_platform_admin: bool,
    decision: str,
    decision_note: str,
) -> PublicationReview:
    if decision not in {"approved", "rejected"}:
        raise ValueError("Invalid publication decision")
    from typing import Literal
    from typing import cast as typing_cast

    result = approvals_for(session).decide(
        session,
        approval_id=review.id,
        decision=typing_cast(Literal["approved", "rejected"], decision),
        decided_by=PublicationSubject(UUID(actor.actor_id or ""), actor_is_platform_admin),
        reason=decision_note,
        idempotency_key=f"publication:{review.id}:{decision}",
    )
    return PublicationReview(result)


def cancel_publication_reviews(
    session: Session, *, version_ids: Sequence[UUID], actor: AuditActor
) -> None:
    for version_id in version_ids:
        # One pending attempt per version; caller holds the definition lock.
        pending, _ = approvals_for(session).search(
            session, kind=KIND, target_id=str(version_id), status="pending", limit=1
        )
        for request in pending:
            approvals_for(session).cancel(
                session,
                approval_id=request.id,
                actor=PublicationSubject(UUID(actor.actor_id or "")),
                reason="Definition visibility or status changed",
            )


def withdraw_publication_review(
    session: Session, *, review: PublicationReview, actor: AuditActor
) -> PublicationReview:
    return PublicationReview(
        approvals_for(session).cancel(
            session,
            approval_id=review.id,
            actor=PublicationSubject(UUID(actor.actor_id or "")),
            reason="Author withdrew submission",
        )
    )
