"""Quota service: atomic reservation, settlement, release and rejection.

Both ledgers serialize on one account row: a reservation checks the remaining
allowance and holds its upper bound in the same locked transaction, so
concurrent callers can never oversubscribe one account.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TypeVar
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.governance.audit.service import AuditActor, append_platform_event
from opsmesh.identity.errors import QuotaExceededError, QuotaReservationError
from opsmesh.identity.quotas.models import (
    RESERVATION_RELEASED,
    RESERVATION_RESERVED,
    RESERVATION_SETTLED,
    StorageReservation,
    TokenReservation,
    UserQuotaLimit,
    UserStorageAccount,
    UserTokenAccount,
)
from opsmesh.shared.errors import ConflictError, InvalidArgumentError, NotFoundError
from opsmesh.shared.serialization import payload_digest

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
    limits = session.get(UserQuotaLimit, user_id)
    if limits is None:
        raise NotFoundError("The user quota ledger is not initialized")
    previous = (limits.token_limit, limits.storage_limit_bytes)
    limits.token_limit = token_limit
    limits.storage_limit_bytes = storage_limit_bytes
    limits.updated_at = datetime.now(UTC)
    append_platform_event(
        session,
        actor=actor,
        action="user.quota_limits_updated",
        target_type="user",
        target_id=str(user_id),
        summary="User quota limits updated",
        details={
            "previous_token_limit": previous[0],
            "previous_storage_limit_bytes": previous[1],
            "token_limit": token_limit,
            "storage_limit_bytes": storage_limit_bytes,
        },
    )
    return limits


def get_quota(session: Session, user_id: UUID) -> QuotaSnapshot:
    limits = session.get(UserQuotaLimit, user_id)
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
    """Hold one token upper bound; the same key replays the same reservation."""
    if amount <= 0:
        raise InvalidArgumentError("A token reservation must be positive")
    existing = _reservation_by_key(
        session, TokenReservation, scope=idempotency_scope, key=idempotency_key
    )
    if existing is not None:
        _require_same_input(existing.input_digest, {"amount": amount})
        return existing
    account = _locked_token_account(session, user_id)
    limits = session.get(UserQuotaLimit, user_id)
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
    reservation = _reserved_reservation(session, TokenReservation, reservation_id)
    account = _locked_token_account(session, reservation.user_id)
    account.reserved_tokens -= reservation.amount
    account.used_tokens += actual_amount
    account.updated_at = datetime.now(UTC)
    reservation.status = RESERVATION_SETTLED
    reservation.settled_amount = actual_amount
    reservation.settled_at = datetime.now(UTC)
    return reservation


def release_tokens(session: Session, *, reservation_id: UUID) -> TokenReservation:
    """Release one reservation without recording consumption."""
    reservation = _reserved_reservation(session, TokenReservation, reservation_id)
    account = _locked_token_account(session, reservation.user_id)
    account.reserved_tokens -= reservation.amount
    account.updated_at = datetime.now(UTC)
    reservation.status = RESERVATION_RELEASED
    reservation.settled_at = datetime.now(UTC)
    return reservation


def reserve_storage(
    session: Session,
    *,
    user_id: UUID,
    amount: int,
    idempotency_scope: str,
    idempotency_key: str,
) -> StorageReservation:
    """Hold one storage upper bound in bytes; the same key replays it."""
    if amount <= 0:
        raise InvalidArgumentError("A storage reservation must be positive")
    existing = _reservation_by_key(
        session, StorageReservation, scope=idempotency_scope, key=idempotency_key
    )
    if existing is not None:
        _require_same_input(existing.input_digest, {"amount": amount})
        return existing
    account = _locked_storage_account(session, user_id)
    limits = session.get(UserQuotaLimit, user_id)
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
    reservation = _reserved_reservation(session, StorageReservation, reservation_id)
    account = _locked_storage_account(session, reservation.user_id)
    account.reserved_bytes -= reservation.amount
    account.used_bytes += actual_amount
    account.updated_at = datetime.now(UTC)
    reservation.status = RESERVATION_SETTLED
    reservation.settled_amount = actual_amount
    reservation.settled_at = datetime.now(UTC)
    return reservation


def release_storage(session: Session, *, reservation_id: UUID) -> StorageReservation:
    """Release one storage reservation without recording usage."""
    reservation = _reserved_reservation(session, StorageReservation, reservation_id)
    account = _locked_storage_account(session, reservation.user_id)
    account.reserved_bytes -= reservation.amount
    account.updated_at = datetime.now(UTC)
    reservation.status = RESERVATION_RELEASED
    reservation.settled_at = datetime.now(UTC)
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
        select(UserTokenAccount).where(UserTokenAccount.user_id == user_id).with_for_update()
    )
    if account is None:
        raise InvalidArgumentError("The user quota ledger is not initialized")
    return account


def _locked_storage_account(session: Session, user_id: UUID) -> UserStorageAccount:
    account = session.scalar(
        select(UserStorageAccount).where(UserStorageAccount.user_id == user_id).with_for_update()
    )
    if account is None:
        raise InvalidArgumentError("The user quota ledger is not initialized")
    return account


def _reservation_by_key(
    session: Session,
    model: type[ReservationModel],
    *,
    scope: str,
    key: str,
) -> ReservationModel | None:
    return session.scalar(
        select(model).where(model.idempotency_scope == scope, model.idempotency_key == key)
    )


def _require_same_input(input_digest: str, declared: dict[str, object]) -> None:
    if input_digest != payload_digest(declared):
        raise ConflictError("The idempotency key was reused with a different input")


def _reserved_reservation(
    session: Session, model: type[ReservationModel], reservation_id: UUID
) -> ReservationModel:
    reservation = session.scalar(select(model).where(model.id == reservation_id).with_for_update())
    if reservation is None:
        raise NotFoundError("The quota reservation does not exist")
    if reservation.status != RESERVATION_RESERVED:
        raise QuotaReservationError(
            "The quota reservation is already closed",
            details={"status": reservation.status},
        )
    return reservation
