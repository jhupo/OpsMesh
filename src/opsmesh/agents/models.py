from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from opsmesh.infra.database import Base, Record


class AgentInstance(Record, Base):
    __tablename__ = "agent_instances"
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    plugin_id: Mapped[str] = mapped_column(ForeignKey("plugins.id"))
    entry: Mapped[str] = mapped_column(String(64))
    encrypted_config: Mapped[str] = mapped_column(Text)
    dependencies: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    workspace_id: Mapped[str | None] = mapped_column(ForeignKey("workspaces.id"))
    enabled: Mapped[bool] = mapped_column(default=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
