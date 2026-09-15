from uuid import UUID

from sqlalchemy import ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class CapabilityResource(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "capability_resources"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "key",
            name="uq_capability_resources_workspace_key",
        ),
        Index("ix_capability_resources_workspace_type", "workspace_id", "resource_type"),
        Index("ix_capability_resources_workspace_status", "workspace_id", "status"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    key: Mapped[str] = mapped_column(String(120), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(40), nullable=False)
    description: Mapped[str] = mapped_column(String(2_000), nullable=False, default="")
    access_mode: Mapped[str] = mapped_column(String(32), nullable=False, default="read")
    locator: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    parameter_schema: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )
    default_parameters: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
