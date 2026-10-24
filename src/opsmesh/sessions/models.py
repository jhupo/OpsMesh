from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from opsmesh.infra.database import Base, Record


class AgentSession(Record, Base):
    __tablename__ = "agent_sessions"
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    caller_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), index=True)
    agent_key: Mapped[str] = mapped_column(String(36), index=True)
    binding_key: Mapped[str] = mapped_column(String(64), unique=True)
    scope: Mapped[str] = mapped_column(String(20))
    channel_id: Mapped[str | None] = mapped_column(String(36))
    conversation_id: Mapped[str | None] = mapped_column(String(200))
    active_task_id: Mapped[str | None] = mapped_column(String(36))
    revision: Mapped[int] = mapped_column(default=0)
    closed: Mapped[bool] = mapped_column(default=False)
    sandbox_backend: Mapped[str | None] = mapped_column(String(100))
    sandbox_sdk_version: Mapped[str | None] = mapped_column(String(40))
    sandbox_task_id: Mapped[str | None] = mapped_column(String(36))
    sandbox_dirty: Mapped[bool] = mapped_column(default=False)
    sandbox_generation: Mapped[int] = mapped_column(default=0)
    sandbox_access_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sandbox_plugin_id: Mapped[str | None] = mapped_column(ForeignKey("plugins.id"))
    encrypted_sandbox_config: Mapped[str | None] = mapped_column(Text)
    encrypted_sandbox_state: Mapped[str | None] = mapped_column(Text)
