"""Private Session ownership and native-history generation metadata."""

from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from opsmesh.shared.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class ExecutionSession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "execution_sessions"
    __table_args__ = (CheckConstraint("generation > 0", name="session_generation"),)

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    revision_id: Mapped[UUID | None]
    instance_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("execution_instances.id"), index=True
    )
    generation: Mapped[int] = mapped_column(Integer, default=1)
    next_sequence: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(32), default="active")
