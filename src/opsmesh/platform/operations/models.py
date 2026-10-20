"""Durable command and result facts for platform administration."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import JSON, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column

from opsmesh.shared.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class PlatformAdmission(Base):
    """One persisted admission switch, coordinated by the platform operation lock."""

    __tablename__ = "platform_admission"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    drained: Mapped[bool] = mapped_column(default=False)
    revision: Mapped[int] = mapped_column(default=1)


class PlatformOperation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "platform_operations"

    idempotency_key: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    owner: Mapped[str] = mapped_column(String(80), nullable=False)
    command: Mapped[str] = mapped_column(String(120), nullable=False)
    target: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="accepted")
    generation: Mapped[int] = mapped_column(default=1)
    result: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    failure_code: Mapped[str | None] = mapped_column(String(120))
    requested_by_user_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    error_detail: Mapped[str | None] = mapped_column(Text)
