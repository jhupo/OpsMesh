"""Durable logical Agents; native SDK objects are reconstructed when used."""

from uuid import UUID

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from opsmesh.shared.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class AgentInstance(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "execution_instances"
    __table_args__ = (UniqueConstraint("owner_user_id", "request_key"),)

    owner_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    agent_id: Mapped[str] = mapped_column(String(200))
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(24), default="active")
    environment_id: Mapped[UUID | None] = mapped_column(ForeignKey("sandbox_environments.id"))
    request_key: Mapped[str] = mapped_column(String(200))
    fingerprint: Mapped[str] = mapped_column(String(64))
    encrypted_config: Mapped[str] = mapped_column(Text)
    key_id: Mapped[str] = mapped_column(String(100))
