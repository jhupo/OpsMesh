"""Workspace capacity service: one ledger per workspace, never the user quota.

Reservations serialize on the account row: the key is re-checked under the
lock so a retry replays its committed reservation instead of racing toward
the unique constraint, exactly like the identity ledgers. A workspace
limit and a user quota are different facts with different owners; nothing
here reads the user quota tables.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.governance.audit.service import AuditActor, append_platform_event
from opsmesh.identity.context import CurrentUser
from opsmesh.shared.errors import (
    ConflictError,
    DomainError,
    InvalidArgumentError,
    NotFoundError,
)
from opsmesh.shared.serialization import payload_digest
from opsmesh.workspaces.capacity.models import (
    CAPACITY_KINDS,
    RESERVATION_RELEASED,
    RESERVATION_RESERVED,
    WorkspaceCapacityAccount,
    WorkspaceCapacityReservation,
)
from opsmesh.workspaces.members import access


class CapacityExceededError(DomainError):
    """The workspace capacity account cannot admit this reservation."""

    def __init__(
        self,
        message: str = "The workspace capacity cannot admit this reservation",
        *,
        code: str = "capacity_exceeded",
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message, code=code, status_code=429, details=details or {})


@dataclass(frozen=True)
class CapacityKindView:
    """One kind's configured limit and currently held reservations."""

    kind: str
    limit_value: int | None
    reserved_value: int


def ensure_accounts(
    session: Session,
    *,
    workspace_id: UUID,
    limits: Mapping[str, int | None],
) -> None:
    """Initialize one workspace's capacity accounts; repeat calls are no-ops."""
    for kind in CAPACITY_KINDS:
        if session.get(WorkspaceCapacityAccount, (workspace_id, kind)) is None:
            session.add(
                WorkspaceCapacityAccount(
                    workspace_id=workspace_id,
                    kind=kind,
                    limit_value=limits.get(kind),
                    reserved_value=0,
                )
            )


def set_limit(
    session: Session,
    *,
    actor: CurrentUser,
    workspace_id: UUID,
    kind: str,
    limit_value: int | None,
) -> WorkspaceCapacityAccount:
    """Replace one kind's configured limit; lowering it below the held
    reservations is refused instead of silently over-subscribing."""
    access.require_workspace_member(
        session, workspace_id=workspace_id, user_id=actor.user_id, permission="manage", lock=True
    )
    if kind not in CAPACITY_KINDS:
        raise InvalidArgumentError(
            "The capacity kind is unknown",
            details={"kind": kind, "known_kinds": list(CAPACITY_KINDS)},
        )
    if limit_value is not None and limit_value < 0:
        raise InvalidArgumentError("A capacity limit cannot be negative")
    account = _locked_account(session, workspace_id=workspace_id, kind=kind)
    if limit_value is not None and account.reserved_value > limit_value:
        raise ConflictError(
            "The capacity limit is below the outstanding reservations",
            details={
                "kind": kind,
                "limit_value": limit_value,
                "reserved_value": account.reserved_value,
            },
        )
    previous = account.limit_value
    account.limit_value = limit_value
    append_platform_event(
        session,
        actor=_actor(actor, workspace_id=workspace_id),
        action="workspace.capacity_limit_updated",
        target_type="workspace_capacity_account",
        target_id=f"{workspace_id}:{kind}",
        summary="Workspace capacity limit updated",
        details={"kind": kind, "previous_limit_value": previous, "limit_value": limit_value},
    )
    return account


def read_capacity(
    session: Session, *, actor: CurrentUser, workspace_id: UUID
) -> list[CapacityKindView]:
    """Every kind's limit and held reservations, for one authorized member."""
    access.require_workspace_member(session, workspace_id=workspace_id, user_id=actor.user_id)
    return capacity_snapshot(session, workspace_id=workspace_id)


def capacity_snapshot(session: Session, *, workspace_id: UUID) -> list[CapacityKindView]:
    """Every kind's limit and held reservations, in a stable order."""
    accounts = list(
        session.scalars(
            select(WorkspaceCapacityAccount)
            .where(WorkspaceCapacityAccount.workspace_id == workspace_id)
            .order_by(WorkspaceCapacityAccount.kind)
        )
    )
    return [
        CapacityKindView(
            kind=account.kind,
            limit_value=account.limit_value,
            reserved_value=account.reserved_value,
        )
        for account in accounts
    ]


def reserve(
    session: Session,
    *,
    workspace_id: UUID,
    kind: str,
    amount: int,
    idempotency_scope: str,
    idempotency_key: str,
) -> WorkspaceCapacityReservation:
    """Hold one amount of one kind; the same key replays the same reservation.

    The account row lock is the serialization point: two concurrent
    reservations both find no receipt before either lock, so the waiter
    must read the winner's committed reservation instead of creating a
    duplicate that only the unique constraint would stop.
    """
    if kind not in CAPACITY_KINDS:
        raise InvalidArgumentError(
            "The capacity kind is unknown",
            details={"kind": kind, "known_kinds": list(CAPACITY_KINDS)},
        )
    if amount <= 0:
        raise InvalidArgumentError("A capacity reservation must be positive")
    account = _locked_account(session, workspace_id=workspace_id, kind=kind)
    existing = session.scalar(
        select(WorkspaceCapacityReservation).where(
            WorkspaceCapacityReservation.workspace_id == workspace_id,
            WorkspaceCapacityReservation.kind == kind,
            WorkspaceCapacityReservation.idempotency_scope == idempotency_scope,
            WorkspaceCapacityReservation.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        _require_same_input(existing.input_digest, {"amount": amount})
        return existing
    if account.limit_value is not None:
        projected = account.reserved_value + amount
        if projected > account.limit_value:
            raise CapacityExceededError(
                details={
                    "kind": kind,
                    "limit_value": account.limit_value,
                    "reserved_value": account.reserved_value,
                    "requested_amount": amount,
                }
            )
    reservation = WorkspaceCapacityReservation(
        workspace_id=workspace_id,
        kind=kind,
        amount=amount,
        status=RESERVATION_RESERVED,
        idempotency_scope=idempotency_scope,
        idempotency_key=idempotency_key,
        input_digest=payload_digest({"amount": amount}),
    )
    session.add(reservation)
    account.reserved_value += amount
    session.flush()
    return reservation


def release(
    session: Session, *, workspace_id: UUID, reservation_id: UUID
) -> WorkspaceCapacityReservation:
    """Release one held reservation; an already released one replays it.

    The reservation row is locked first because its kind decides which
    account to lock; a concurrent reserve only ever inserts a new row, so
    no cycle exists between the two orders.
    """
    reservation = session.scalar(
        select(WorkspaceCapacityReservation)
        .where(
            WorkspaceCapacityReservation.id == reservation_id,
            WorkspaceCapacityReservation.workspace_id == workspace_id,
        )
        .with_for_update()
    )
    if reservation is None:
        raise NotFoundError("The capacity reservation does not exist")
    if reservation.status == RESERVATION_RELEASED:
        return reservation
    account = _locked_account(session, workspace_id=workspace_id, kind=reservation.kind)
    account.reserved_value -= reservation.amount
    reservation.status = RESERVATION_RELEASED
    reservation.released_at = datetime.now(UTC)
    return reservation


def _locked_account(
    session: Session, *, workspace_id: UUID, kind: str
) -> WorkspaceCapacityAccount:
    account = session.scalar(
        select(WorkspaceCapacityAccount)
        .where(
            WorkspaceCapacityAccount.workspace_id == workspace_id,
            WorkspaceCapacityAccount.kind == kind,
        )
        .with_for_update()
    )
    if account is None:
        raise InvalidArgumentError("The workspace capacity account is not initialized")
    return account


def _require_same_input(input_digest: str, declared: dict[str, object]) -> None:
    if input_digest != payload_digest(declared):
        raise ConflictError("The idempotency key was reused with a different input")


def _actor(actor: CurrentUser, *, workspace_id: UUID) -> AuditActor:
    return AuditActor(actor_type="user", actor_id=str(actor.user_id), workspace_id=workspace_id)


__all__ = [
    "CapacityExceededError",
    "CapacityKindView",
    "capacity_snapshot",
    "ensure_accounts",
    "release",
    "reserve",
    "set_limit",
]
