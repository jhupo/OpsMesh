"""User quota facts: token and storage ledgers with atomic reservations."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column

from opsmesh.shared.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

RESERVATION_RESERVED = "reserved"
RESERVATION_SETTLED = "settled"
RESERVATION_RELEASED = "released"
RESERVATION_STATUSES = (RESERVATION_RESERVED, RESERVATION_SETTLED, RESERVATION_RELEASED)


class UserQuotaLimit(Base):
    """One account's configured allowances; a null limit means unlimited."""

    __tablename__ = "user_quota_limits"

    user_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True), ForeignKey("users.id"), primary_key=True
    )
    token_limit: Mapped[int | None] = mapped_column(BigInteger)
    storage_limit_bytes: Mapped[int | None] = mapped_column(BigInteger)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class UserTokenAccount(Base):
    """The single ledger row of one account's token usage.

    ``reserved_tokens`` holds outstanding reservation upper bounds and
    ``used_tokens`` the settled consumption; the row lock serializes every
    reservation, settlement and release of one account.
    """

    __tablename__ = "user_token_accounts"

    user_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True), ForeignKey("users.id"), primary_key=True
    )
    used_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    reserved_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class UserStorageAccount(Base):
    """The single ledger row of one account's stored bytes."""

    __tablename__ = "user_storage_accounts"

    user_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True), ForeignKey("users.id"), primary_key=True
    )
    used_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    reserved_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class TokenReservation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One token reservation: an upper bound held until it settles or releases."""

    __tablename__ = "token_reservations"
    __table_args__ = (
        UniqueConstraint(
            "idempotency_scope", "idempotency_key", name="uq_token_reservations_scope_key"
        ),
    )

    user_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=RESERVATION_RESERVED)
    settled_amount: Mapped[int | None] = mapped_column(BigInteger)
    idempotency_scope: Mapped[str] = mapped_column(String(255), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    input_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class StorageReservation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One storage reservation in bytes with the same lifecycle as tokens."""

    __tablename__ = "storage_reservations"
    __table_args__ = (
        UniqueConstraint(
            "idempotency_scope", "idempotency_key", name="uq_storage_reservations_scope_key"
        ),
    )

    user_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=RESERVATION_RESERVED)
    settled_amount: Mapped[int | None] = mapped_column(BigInteger)
    idempotency_scope: Mapped[str] = mapped_column(String(255), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    input_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
