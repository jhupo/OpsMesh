"""Persistent environments and independently owned operation receipts."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from opsmesh.shared.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class SandboxEnvironment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "sandbox_environments"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','ready','busy','closed','needs_attention')", name="status"
        ),
    )

    owner_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    directory_id: Mapped[UUID] = mapped_column(ForeignKey("resource_directories.id"), index=True)
    image: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(24), default="pending")
    active_operation_id: Mapped[UUID | None]
    active_run_id: Mapped[UUID | None]
    capacity_reservation_id: Mapped[UUID]
    encrypted_state: Mapped[str | None] = mapped_column(Text)
    state_key_id: Mapped[str | None] = mapped_column(String(100))


class SandboxOperation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "sandbox_operations"
    __table_args__ = (
        UniqueConstraint("environment_id", "request_key"),
        CheckConstraint(
            "action IN ('create','resume','close','command','read','write')", name="action"
        ),
        CheckConstraint(
            "status IN ('pending','started','completed','uncertain','rejected')", name="status"
        ),
    )

    environment_id: Mapped[UUID] = mapped_column(ForeignKey("sandbox_environments.id"), index=True)
    requested_by_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    request_key: Mapped[str] = mapped_column(String(200))
    action: Mapped[str] = mapped_column(String(16))
    fingerprint: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    # Arguments and native state can contain private code, file data or output.
    encrypted_arguments: Mapped[str] = mapped_column(Text)
    arguments_key_id: Mapped[str] = mapped_column(String(100))
    encrypted_result: Mapped[str | None] = mapped_column(Text)
    result_key_id: Mapped[str | None] = mapped_column(String(100))
    error_code: Mapped[str | None] = mapped_column(String(100))
