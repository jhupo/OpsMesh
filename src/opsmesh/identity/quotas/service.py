"""Quota service: atomic reservation, settlement, release and rejection.

Both ledgers serialize on one account row: a reservation checks the remaining
allowance and holds its upper bound in the same locked transaction, so
concurrent callers can never oversubscribe one account.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TypeVar
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.identity.access import ensure_active_user, require_platform_admin
from opsmesh.identity.context import CurrentUser
from opsmesh.identity.errors import QuotaExceededError, QuotaReservationError
from opsmesh.identity.quotas.models import (
    CAPACITY_KINDS,
    RESERVATION_RELEASED,
    RESERVATION_RESERVED,
    RESERVATION_SETTLED,
    StorageReservation,
    TokenReservation,
    UserCapacityAccount,
    UserCapacityReservation,
    UserQuotaLimit,
    UserStorageAccount,
    UserTokenAccount,
)
from opsmesh.shared.audit import AuditActor, AuditRecord, record_audit
from opsmesh.shared.errors import ConflictError, DomainError, InvalidArgumentError, NotFoundError
from opsmesh.shared.serialization import canonical_json, payload_digest

ReservationModel = TypeVar("ReservationModel", TokenReservation, StorageReservation)


@dataclass(frozen=True)
class QuotaSnapshot:
    """One account's limits and current ledger position."""

    token_limit: int | None
    used_tokens: int
    reserved_tokens: int
    storage_limit_bytes: int | None
    used_bytes: int
    reserved_bytes: int

    @property
    def available_tokens(self) -> int | None:
        return _remaining(self.token_limit, self.used_tokens, self.reserved_tokens)

    @property
    def available_bytes(self) -> int | None:
        return _remaining(self.storage_limit_bytes, self.used_bytes, self.reserved_bytes)


def ensure_ledger(
    session: Session,
    *,
    user_id: UUID,
    token_limit: int | None = None,
    storage_limit_bytes: int | None = None,
) -> None:
    """Create the limit and ledger rows of one account; repeat calls are no-ops."""
    if session.get(UserQuotaLimit, user_id) is None:
        session.add(
            UserQuotaLimit(
                user_id=user_id, token_limit=token_limit, storage_limit_bytes=storage_limit_bytes
            )
        )
    if session.get(UserTokenAccount, user_id) is None:
        session.add(UserTokenAccount(user_id=user_id))
    if session.get(UserStorageAccount, user_id) is None:
        session.add(UserStorageAccount(user_id=user_id))


def set_limits(
    session: Session,
    *,
    user_id: UUID,
    actor: AuditActor,
    token_limit: int | None = None,
    storage_limit_bytes: int | None = None,
) -> UserQuotaLimit:
    """Replace one account's configured allowances; the change is audited."""
    _validate_limits(token_limit=token_limit, storage_limit_bytes=storage_limit_bytes)
    _locked_token_account(session, user_id)
    _locked_storage_account(session, user_id)
    limits = session.get(UserQuotaLimit, user_id, populate_existing=True)
    if limits is None:
        raise NotFoundError("The user quota ledger is not initialized")
    previous = (limits.token_limit, limits.storage_limit_bytes)
    limits.token_limit = token_limit
    limits.storage_limit_bytes = storage_limit_bytes
    limits.updated_at = datetime.now(UTC)
    record_audit(
        session,
        AuditRecord(
            category="operation",
            action="user.quota_limits_updated",
            actor=actor,
            source="platform",
            target_type="user",
            target_id=str(user_id),
            summary="User quota limits updated",
            payload_json=canonical_json(
                {
                    "previous_token_limit": previous[0],
                    "previous_storage_limit_bytes": previous[1],
                    "token_limit": token_limit,
                    "storage_limit_bytes": storage_limit_bytes,
                }
            ),
        ),
    )
    session.flush()
    return limits


def get_quota(session: Session, user_id: UUID) -> QuotaSnapshot:
    limits = session.get(UserQuotaLimit, user_id, populate_existing=True)
    if limits is None:
        raise NotFoundError("The user quota ledger is not initialized")
    tokens = session.get(UserTokenAccount, user_id)
    storage = session.get(UserStorageAccount, user_id)
    if tokens is None or storage is None:
        raise InvalidArgumentError("The user quota ledger is incomplete")
    return QuotaSnapshot(
        token_limit=limits.token_limit,
        used_tokens=tokens.used_tokens,
        reserved_tokens=tokens.reserved_tokens,
        storage_limit_bytes=limits.storage_limit_bytes,
        used_bytes=storage.used_bytes,
        reserved_bytes=storage.reserved_bytes,
    )


def reserve_tokens(
    session: Session,
    *,
    user_id: UUID,
    amount: int,
    idempotency_scope: str,
    idempotency_key: str,
) -> TokenReservation:
    """Hold one token upper bound; the same key replays the same reservation.

    The key is re-checked under the account row lock: two concurrent
    reservations of one key both see no receipt before either lock, so the
    waiters must read the winner's committed reservation instead of creating
    a duplicate that only the unique constraint would stop.
    """
    if amount <= 0:
        raise InvalidArgumentError("A token reservation must be positive")
    account = _locked_token_account(session, user_id)
    existing = _reservation_by_key(
        session, TokenReservation, user_id=user_id, scope=idempotency_scope, key=idempotency_key
    )
    if existing is not None:
        _require_same_input(existing.input_digest, {"amount": amount})
        return existing
    limits = session.get(UserQuotaLimit, user_id, populate_existing=True)
    if limits is None:
        raise InvalidArgumentError("The user quota ledger is not initialized")
    if limits.token_limit is not None:
        projected = account.used_tokens + account.reserved_tokens + amount
        if projected > limits.token_limit:
            raise QuotaExceededError(
                "The user token quota cannot admit this reservation",
                details={
                    "token_limit": limits.token_limit,
                    "used_tokens": account.used_tokens,
                    "reserved_tokens": account.reserved_tokens,
                    "requested_tokens": amount,
                },
            )
    reservation = TokenReservation(
        user_id=user_id,
        amount=amount,
        status=RESERVATION_RESERVED,
        idempotency_scope=idempotency_scope,
        idempotency_key=idempotency_key,
        input_digest=payload_digest({"amount": amount}),
    )
    session.add(reservation)
    account.reserved_tokens += amount
    account.updated_at = datetime.now(UTC)
    session.flush()
    return reservation


def settle_tokens(
    session: Session, *, reservation_id: UUID, actual_amount: int
) -> TokenReservation:
    """Settle one reservation with its actual consumption."""
    if actual_amount < 0:
        raise InvalidArgumentError("A settled token amount cannot be negative")
    reservation = _locked_reservation(session, TokenReservation, reservation_id)
    if reservation.status == RESERVATION_SETTLED and reservation.settled_amount == actual_amount:
        return reservation
    _require_reserved(reservation)
    account = _locked_token_account(session, reservation.user_id)
    account.reserved_tokens -= reservation.amount
    account.used_tokens += actual_amount
    account.updated_at = datetime.now(UTC)
    reservation.status = RESERVATION_SETTLED
    reservation.settled_amount = actual_amount
    reservation.settled_at = datetime.now(UTC)
    session.flush()
    return reservation


def release_tokens(session: Session, *, reservation_id: UUID) -> TokenReservation:
    """Release one reservation without recording consumption."""
    reservation = _locked_reservation(session, TokenReservation, reservation_id)
    if reservation.status == RESERVATION_RELEASED:
        return reservation
    _require_reserved(reservation)
    account = _locked_token_account(session, reservation.user_id)
    account.reserved_tokens -= reservation.amount
    account.updated_at = datetime.now(UTC)
    reservation.status = RESERVATION_RELEASED
    reservation.settled_at = datetime.now(UTC)
    session.flush()
    return reservation


def reserve_storage(
    session: Session,
    *,
    user_id: UUID,
    amount: int,
    idempotency_scope: str,
    idempotency_key: str,
) -> StorageReservation:
    """Hold one storage upper bound in bytes; the same key replays it.

    Like the token ledger, the key is re-checked under the account row lock
    so a concurrent same-key reservation replays the committed reservation
    instead of racing toward the unique constraint.
    """
    if amount <= 0:
        raise InvalidArgumentError("A storage reservation must be positive")
    account = _locked_storage_account(session, user_id)
    existing = _reservation_by_key(
        session, StorageReservation, user_id=user_id, scope=idempotency_scope, key=idempotency_key
    )
    if existing is not None:
        _require_same_input(existing.input_digest, {"amount": amount})
        return existing
    limits = session.get(UserQuotaLimit, user_id, populate_existing=True)
    if limits is None:
        raise InvalidArgumentError("The user quota ledger is not initialized")
    if limits.storage_limit_bytes is not None:
        projected = account.used_bytes + account.reserved_bytes + amount
        if projected > limits.storage_limit_bytes:
            raise QuotaExceededError(
                "The user storage quota cannot admit this reservation",
                details={
                    "storage_limit_bytes": limits.storage_limit_bytes,
                    "used_bytes": account.used_bytes,
                    "reserved_bytes": account.reserved_bytes,
                    "requested_bytes": amount,
                },
            )
    reservation = StorageReservation(
        user_id=user_id,
        amount=amount,
        status=RESERVATION_RESERVED,
        idempotency_scope=idempotency_scope,
        idempotency_key=idempotency_key,
        input_digest=payload_digest({"amount": amount}),
    )
    session.add(reservation)
    account.reserved_bytes += amount
    account.updated_at = datetime.now(UTC)
    session.flush()
    return reservation


def settle_storage(
    session: Session, *, reservation_id: UUID, actual_amount: int
) -> StorageReservation:
    """Settle one storage reservation with its actual stored bytes."""
    if actual_amount < 0:
        raise InvalidArgumentError("A settled storage amount cannot be negative")
    reservation = _locked_reservation(session, StorageReservation, reservation_id)
    if reservation.status == RESERVATION_SETTLED and reservation.settled_amount == actual_amount:
        return reservation
    _require_reserved(reservation)
    account = _locked_storage_account(session, reservation.user_id)
    account.reserved_bytes -= reservation.amount
    account.used_bytes += actual_amount
    account.updated_at = datetime.now(UTC)
    reservation.status = RESERVATION_SETTLED
    reservation.settled_amount = actual_amount
    reservation.settled_at = datetime.now(UTC)
    session.flush()
    return reservation


def release_storage(session: Session, *, reservation_id: UUID) -> StorageReservation:
    """Release one storage reservation without recording usage."""
    reservation = _locked_reservation(session, StorageReservation, reservation_id)
    if reservation.status == RESERVATION_RELEASED:
        return reservation
    _require_reserved(reservation)
    account = _locked_storage_account(session, reservation.user_id)
    account.reserved_bytes -= reservation.amount
    account.updated_at = datetime.now(UTC)
    reservation.status = RESERVATION_RELEASED
    reservation.settled_at = datetime.now(UTC)
    session.flush()
    return reservation


def reclaim_storage(session: Session, *, reservation_id: UUID) -> StorageReservation:
    """Remove confirmed stored bytes after their owner proves deletion or replacement."""
    reservation = session.scalar(
        select(StorageReservation).where(StorageReservation.id == reservation_id).with_for_update()
    )
    if reservation is None:
        raise NotFoundError("The storage reservation does not exist")
    if reservation.status == RESERVATION_RELEASED:
        return reservation
    if reservation.status != RESERVATION_SETTLED:
        raise InvalidArgumentError("Only confirmed stored bytes may be reclaimed")
    account = _locked_storage_account(session, reservation.user_id)
    account.used_bytes -= reservation.settled_amount or 0
    account.updated_at = datetime.now(UTC)
    reservation.status = RESERVATION_RELEASED
    session.flush()
    return reservation


def _remaining(limit: int | None, used: int, reserved: int) -> int | None:
    if limit is None:
        return None
    return limit - used - reserved


def _validate_limits(*, token_limit: int | None, storage_limit_bytes: int | None) -> None:
    if token_limit is not None and token_limit < 0:
        raise InvalidArgumentError("A token limit cannot be negative")
    if storage_limit_bytes is not None and storage_limit_bytes < 0:
        raise InvalidArgumentError("A storage limit cannot be negative")


def _locked_token_account(session: Session, user_id: UUID) -> UserTokenAccount:
    account = session.scalar(
        select(UserTokenAccount)
        .where(UserTokenAccount.user_id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if account is None:
        raise InvalidArgumentError("The user quota ledger is not initialized")
    return account


def _locked_storage_account(session: Session, user_id: UUID) -> UserStorageAccount:
    account = session.scalar(
        select(UserStorageAccount)
        .where(UserStorageAccount.user_id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if account is None:
        raise InvalidArgumentError("The user quota ledger is not initialized")
    return account


def _reservation_by_key(
    session: Session,
    model: type[ReservationModel],
    *,
    user_id: UUID,
    scope: str,
    key: str,
) -> ReservationModel | None:
    return session.scalar(
        select(model).where(
            model.user_id == user_id,
            model.idempotency_scope == scope,
            model.idempotency_key == key,
        )
    )


def _require_same_input(input_digest: str, declared: dict[str, object]) -> None:
    if input_digest != payload_digest(declared):
        raise ConflictError("The idempotency key was reused with a different input")


def _locked_reservation(
    session: Session, model: type[ReservationModel], reservation_id: UUID
) -> ReservationModel:
    reservation = session.scalar(
        select(model)
        .where(model.id == reservation_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if reservation is None:
        raise NotFoundError("The quota reservation does not exist")
    return reservation


def _require_reserved(reservation: TokenReservation | StorageReservation) -> None:
    if reservation.status != RESERVATION_RESERVED:
        raise QuotaReservationError(
            "The quota reservation is already closed",
            details={"status": reservation.status},
        )


class CapacityExceededError(DomainError):
    """The user capacity account cannot admit this reservation."""

    def __init__(
        self,
        message: str = "The user capacity cannot admit this reservation",
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


def ensure_capacity_accounts(
    session: Session,
    *,
    user_id: UUID,
    limits: Mapping[str, int | None],
) -> None:
    """Initialize one user's capacity accounts; repeat calls are no-ops."""
    if set(limits) != set(CAPACITY_KINDS) or any(
        value is not None and value < 0 for value in limits.values()
    ):
        raise InvalidArgumentError("Every capacity kind requires an explicit nonnegative limit")
    for kind in CAPACITY_KINDS:
        if session.get(UserCapacityAccount, (user_id, kind)) is None:
            session.add(
                UserCapacityAccount(
                    user_id=user_id,
                    kind=kind,
                    limit_value=limits.get(kind),
                    reserved_value=0,
                )
            )


def set_capacity_limit(
    session: Session,
    *,
    actor: CurrentUser,
    user_id: UUID,
    kind: str,
    limit_value: int | None,
) -> UserCapacityAccount:
    """Replace one kind's configured limit; lowering it below the held
    reservations is refused instead of silently over-subscribing."""
    require_platform_admin(actor)
    if kind not in CAPACITY_KINDS:
        raise InvalidArgumentError(
            "The capacity kind is unknown",
            details={"kind": kind, "known_kinds": list(CAPACITY_KINDS)},
        )
    if limit_value is not None and limit_value < 0:
        raise InvalidArgumentError("A capacity limit cannot be negative")
    account = _locked_capacity_account(session, user_id=user_id, kind=kind)
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
    record_audit(
        session,
        AuditRecord(
            category="operation",
            action="user.capacity_limit_updated",
            actor=_actor(actor, user_id=user_id),
            source="platform",
            target_type="execution_capacity_account",
            target_id=f"{user_id}:{kind}",
            summary="User capacity limit updated",
            payload_json=canonical_json(
                {"kind": kind, "previous_limit_value": previous, "limit_value": limit_value}
            ),
        ),
    )
    return account


def read_capacity(session: Session, *, actor: CurrentUser, user_id: UUID) -> list[CapacityKindView]:
    """Every kind's limit and held reservations, for one authorized member."""
    if actor.user_id != user_id:
        require_platform_admin(actor)
    else:
        ensure_active_user(actor)
    return capacity_snapshot(session, user_id=user_id)


def capacity_snapshot(session: Session, *, user_id: UUID) -> list[CapacityKindView]:
    """Every kind's limit and held reservations, in a stable order."""
    accounts = list(
        session.scalars(
            select(UserCapacityAccount)
            .where(UserCapacityAccount.user_id == user_id)
            .order_by(UserCapacityAccount.kind)
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


def reserve_capacity(
    session: Session,
    *,
    user_id: UUID,
    kind: str,
    amount: int,
    idempotency_scope: str,
    idempotency_key: str,
) -> UserCapacityReservation:
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
    account = _locked_capacity_account(session, user_id=user_id, kind=kind)
    existing = session.scalar(
        select(UserCapacityReservation).where(
            UserCapacityReservation.user_id == user_id,
            UserCapacityReservation.kind == kind,
            UserCapacityReservation.idempotency_scope == idempotency_scope,
            UserCapacityReservation.idempotency_key == idempotency_key,
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
    reservation = UserCapacityReservation(
        user_id=user_id,
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


def release_capacity(
    session: Session, *, user_id: UUID, reservation_id: UUID
) -> UserCapacityReservation:
    """Release one held reservation; an already released one replays it.

    The reservation row is locked first because its kind decides which
    account to lock; a concurrent reserve only ever inserts a new row, so
    no cycle exists between the two orders.
    """
    reservation = session.scalar(
        select(UserCapacityReservation)
        .where(
            UserCapacityReservation.id == reservation_id,
            UserCapacityReservation.user_id == user_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if reservation is None:
        raise NotFoundError("The capacity reservation does not exist")
    if reservation.status == RESERVATION_RELEASED:
        return reservation
    account = _locked_capacity_account(session, user_id=user_id, kind=reservation.kind)
    account.reserved_value -= reservation.amount
    reservation.status = RESERVATION_RELEASED
    reservation.released_at = datetime.now(UTC)
    session.flush()
    return reservation


def _locked_capacity_account(session: Session, *, user_id: UUID, kind: str) -> UserCapacityAccount:
    account = session.scalar(
        select(UserCapacityAccount)
        .where(
            UserCapacityAccount.user_id == user_id,
            UserCapacityAccount.kind == kind,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if account is None:
        raise InvalidArgumentError("The user capacity account is not initialized")
    return account


def _actor(actor: CurrentUser, *, user_id: UUID) -> AuditActor:
    return AuditActor(actor_type="user", actor_id=str(actor.user_id))
