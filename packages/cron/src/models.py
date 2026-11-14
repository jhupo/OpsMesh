"""The cron plugin's persistent JSON schedule definitions."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from opsmesh.plugins.database import Base


class ScheduleRecord(Base):
    """Persist definitions independently of APScheduler runtime objects."""

    __tablename__ = "cron_schedules"
    __table_args__ = (UniqueConstraint("namespace", "owner_plugin", "name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    namespace: Mapped[str] = mapped_column(String(100), index=True)
    owner_plugin: Mapped[str] = mapped_column(String(200))
    name: Mapped[str] = mapped_column(String(120))
    source: Mapped[str] = mapped_column(String(16))
    definition: Mapped[dict] = mapped_column(JSONB)
    revision: Mapped[str] = mapped_column(String(36))
    completed: Mapped[bool] = mapped_column(Boolean, default=False)
    expired: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
