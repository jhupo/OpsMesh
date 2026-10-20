"""Approval target and notification contracts.

These are static value types plus the one callback a target owner registers:
governance decides once and durably, and the owner learns the decision through
this contract. No business module is imported here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from sqlalchemy import Select
from sqlalchemy.orm import Session

from opsmesh.shared.audit import AuditSubject


@dataclass(frozen=True)
class ApprovalTarget:
    """The static reference of what one approval governs.

    ``target_version`` is the revision the requester expected; a target that
    moved on refuses a decision taken against a stale revision.
    """

    target_type: str
    target_id: str
    target_version: str | None = None


@dataclass(frozen=True)
class EffectiveDecision:
    """The decision one target owner receives, with its audit anchor."""

    approval_id: UUID
    decision: str
    target: ApprovalTarget
    decided_by_user_id: UUID
    reason: str | None


class ApprovalDecisionNotifier(Protocol):
    """Deliver one effective decision to its target owner.

    The implementation runs inside the caller's transaction and stages the
    durable delivery (for example a Workflow signal through the Outbox); it
    never performs the target's work itself.
    """

    def notify_decided(
        self,
        session: Session,
        *,
        approval_id: UUID,
        decision: str,
        target: ApprovalTarget,
        decided_by_user_id: UUID,
        reason: str | None,
    ) -> None: ...


class ApprovalSubject(Protocol):
    @property
    def user_id(self) -> UUID: ...
    def has_platform_role(self, role: str) -> bool: ...


@dataclass(frozen=True)
class ApprovalSnapshot:
    id: UUID
    kind: str
    status: str
    title: str
    summary: str
    details_json: str
    target_type: str
    target_id: str
    target_version: str | None
    approver_user_id: UUID | None
    approver_role: str | None
    requester_user_id: UUID | None
    requester_installation_id: UUID | None
    requester_invocation_id: UUID | None
    expires_at: datetime | None
    created_at: datetime
    decided_at: datetime | None
    decided_by_user_id: UUID | None
    decision_reason: str | None
    decision_idempotency_key: str | None
    cancelled_reason: str | None
    input_digest: str
    request_key: str

    @property
    def details(self) -> dict[str, object]:
        import json

        value: dict[str, object] = json.loads(self.details_json)
        return value


def effective_status(request: ApprovalSnapshot, *, now: datetime | None = None) -> str:
    from datetime import UTC

    if (
        request.status == "pending"
        and request.expires_at is not None
        and request.expires_at <= (now or datetime.now(UTC))
    ):
        return "expired"
    return request.status


class ApprovalPort(Protocol):
    """Caller-owned transaction; the platform retains requests and decisions.

    A target owner locks and validates its target before create/cancel/decide.
    Decisions also revalidate the verified subject, including on replay.
    Query expressions expose only target IDs, never mutable platform models.
    """

    def get(
        self, session: Session, approval_id: UUID | None, *, lock: bool = False
    ) -> ApprovalSnapshot | None: ...

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
    ) -> ApprovalSnapshot: ...

    def cancel(
        self, session: Session, *, approval_id: UUID, actor: ApprovalSubject, reason: str | None
    ) -> ApprovalSnapshot: ...

    def decide(
        self,
        session: Session,
        *,
        approval_id: UUID,
        decision: Literal["approved", "rejected"],
        decided_by: ApprovalSubject,
        reason: str | None,
        idempotency_key: str,
    ) -> ApprovalSnapshot: ...

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
    ) -> tuple[list[ApprovalSnapshot], int]: ...

    def approved_targets(self, *, kind: str) -> Select[tuple[str]]: ...


def approvals_for(session: Session) -> ApprovalPort:
    from typing import cast

    port = session.info.get("approvals")
    if port is None:
        raise RuntimeError("Application approvals are not assembled")
    return cast(ApprovalPort, port)
