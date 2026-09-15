from sqlalchemy import Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Capability(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "capabilities"
    __table_args__ = (
        UniqueConstraint("key", name="uq_capabilities_key"),
        Index("ix_capabilities_category", "category"),
    )

    key: Mapped[str] = mapped_column(String(120), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    category: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str] = mapped_column(String, nullable=False, default="")
    default_policy: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")


class ToolGroup(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "tool_groups"
    __table_args__ = (
        UniqueConstraint("key", name="uq_tool_groups_key"),
        Index("ix_tool_groups_status", "status"),
    )

    key: Mapped[str] = mapped_column(String(120), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(String, nullable=False, default="")
    tool_names: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
