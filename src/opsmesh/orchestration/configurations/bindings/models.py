"""Mutable user grants referencing immutable capability versions."""

from typing import Any
from uuid import UUID

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from opsmesh.shared.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class CapabilityBinding(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "capability_bindings"
    __table_args__ = (UniqueConstraint("owner_user_id", "name"),)

    owner_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    version_id: Mapped[UUID] = mapped_column(ForeignKey("capability_versions.id"))
    version_digest: Mapped[str] = mapped_column(String(64))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(16), default="enabled")
    configuration: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
