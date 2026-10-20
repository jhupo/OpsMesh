"""User quota facts: token and storage ledgers with atomic reservations."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint
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
            "user_id",
            "idempotency_scope",
            "idempotency_key",
            name="uq_token_reservations_scope_key",
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
            "user_id",
            "idempotency_scope",
            "idempotency_key",
            name="uq_storage_reservations_scope_key",
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


CAPACITY_CONCURRENCY = "concurrency"
CAPACITY_ENVIRONMENT = "environment"
CAPACITY_KINDS = (CAPACITY_CONCURRENCY, CAPACITY_ENVIRONMENT)


class UserCapacityAccount(TimestampMixin, Base):
    """One user's limit and outstanding reservations for one kind.

    A null limit means unlimited; ``reserved_value`` holds the sum of every
    open reservation and the row lock serializes reserve and release.
    """

    __tablename__ = "user_capacity_accounts"
    __table_args__ = (
        CheckConstraint("kind IN ('concurrency', 'environment')", name="user_capacity_kind"),
        CheckConstraint(
            "limit_value IS NULL OR limit_value >= 0", name="capacity_limit_nonnegative"
        ),
        CheckConstraint("reserved_value >= 0", name="capacity_reserved_nonnegative"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), primary_key=True
    )
    kind: Mapped[str] = mapped_column(String(32), primary_key=True)
    limit_value: Mapped[int | None] = mapped_column(BigInteger)
    reserved_value: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)


class UserCapacityReservation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One held amount of one kind, replayed by its idempotency key."""

    __tablename__ = "user_capacity_reservations"
    __table_args__ = (
        CheckConstraint("amount > 0", name="user_capacity_reservation_amount"),
        CheckConstraint(
            "status IN ('reserved', 'released')", name="user_capacity_reservation_status"
        ),
        UniqueConstraint(
            "user_id",
            "kind",
            "idempotency_scope",
            "idempotency_key",
            name="uq_user_capacity_reservations_scope_key",
        ),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=RESERVATION_RESERVED)
    idempotency_scope: Mapped[str] = mapped_column(String(255), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    input_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    released_at: Mapped[datetime | None] = mapped_column(default=None)
