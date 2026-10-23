"""A workflow instance freezes a revision closure and caller-owned resource bindings."""

from uuid import UUID

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from opsmesh.shared.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class WorkflowInstance(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "workflow_instances"
    __table_args__ = (UniqueConstraint("owner_user_id", "request_key"),)
    owner_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    revision_id: Mapped[UUID] = mapped_column(ForeignKey("workflow_revisions.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(24), default="active")
    request_key: Mapped[str] = mapped_column(String(200))
    fingerprint: Mapped[str] = mapped_column(String(64))
    encrypted_prepared: Mapped[str] = mapped_column(Text)
    key_id: Mapped[str] = mapped_column(String(100))
