"""Workspace capacity facts: concurrency and environment accounts.

This ledger is workspace-owned and accounts once per workspace. It never
reads the user quota tables: a user's token and storage allowances stay
with identity, and the two ledgers are settled by their own owners. One
account row per kind is the serialization point of every reservation, so
concurrent admissions can never oversubscribe one workspace.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from opsmesh.shared.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

CAPACITY_CONCURRENCY = "concurrency"
CAPACITY_ENVIRONMENT = "environment"
CAPACITY_KINDS = (CAPACITY_CONCURRENCY, CAPACITY_ENVIRONMENT)

RESERVATION_RESERVED = "reserved"
RESERVATION_RELEASED = "released"
RESERVATION_STATUSES = (RESERVATION_RESERVED, RESERVATION_RELEASED)


class WorkspaceCapacityAccount(TimestampMixin, Base):
    """One workspace's limit and outstanding reservations for one kind.

    A null limit means unlimited; ``reserved_value`` holds the sum of every
    open reservation and the row lock serializes reserve and release.
    """

    __tablename__ = "workspace_capacity_accounts"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('concurrency', 'environment')", name="workspace_capacity_kind"
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), primary_key=True
    )
    kind: Mapped[str] = mapped_column(String(32), primary_key=True)
    limit_value: Mapped[int | None] = mapped_column(BigInteger)
    reserved_value: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)


class WorkspaceCapacityReservation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One held amount of one kind, replayed by its idempotency key."""

    __tablename__ = "workspace_capacity_reservations"
    __table_args__ = (
        CheckConstraint("amount > 0", name="workspace_capacity_reservation_amount"),
        CheckConstraint(
            "status IN ('reserved', 'released')", name="workspace_capacity_reservation_status"
        ),
        UniqueConstraint(
            "workspace_id",
            "kind",
            "idempotency_scope",
            "idempotency_key",
            name="uq_workspace_capacity_reservations_scope_key",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=RESERVATION_RESERVED)
    idempotency_scope: Mapped[str] = mapped_column(String(255), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    input_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    released_at: Mapped[datetime | None] = mapped_column(default=None)
