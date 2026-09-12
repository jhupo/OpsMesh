from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class ModelProviderCredential(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "model_provider_credentials"
    __table_args__ = (
        UniqueConstraint("workspace_id", "name", name="uq_model_provider_credentials_name"),
        CheckConstraint("failure_count >= 0", name="ck_model_provider_credentials_failure_count"),
        Index("ix_model_provider_credentials_workspace_status", "workspace_id", "status"),
        Index("ix_model_provider_credentials_workspace_default", "workspace_id", "is_default"),
        Index(
            "uq_model_provider_credentials_active_default",
            "workspace_id",
            unique=True,
            postgresql_where=text("is_default IS TRUE AND status = 'active'"),
            sqlite_where=text("is_default = 1 AND status = 'active'"),
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    provider: Mapped[str] = mapped_column(String(80), nullable=False, default="openai")
    base_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    default_model: Mapped[str] = mapped_column(String(120), nullable=False)
    encrypted_api_key: Mapped[str] = mapped_column(String, nullable=False)
    api_key_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    encryption_key_id: Mapped[str] = mapped_column(String(120), nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    health_status: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_success_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_failure_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_failure_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    last_failure_message: Mapped[str | None] = mapped_column(String(1_000), nullable=True)
    budget_metadata: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )
