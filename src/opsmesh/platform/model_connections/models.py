"""Model upstream facts; encrypted keys never appear in public schemas."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from opsmesh.shared.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

CONNECTION_STATUS_ACTIVE = "active"
CONNECTION_STATUS_DISABLED = "disabled"
PROBE_NEVER = "never"
PROBE_RUNNING = "running"
PROBE_OK = "ok"
PROBE_FAILED = "failed"
PROBE_NEEDS_ATTENTION = "needs_attention"
MAX_NAME_LENGTH = 120
MAX_MODEL_LENGTH = 200
MAX_MODELS = 256
MAX_BASE_URL_LENGTH = 500


class ModelProviderConnection(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One administrator-configured OpenAI-compatible upstream."""

    __tablename__ = "platform_model_connections"
    __table_args__ = (
        CheckConstraint("revision >= 1", name="model_connection_revision"),
        CheckConstraint("status IN ('active', 'disabled')", name="model_connection_status"),
        CheckConstraint("provider = 'openai'", name="model_connection_provider"),
    )

    name: Mapped[str] = mapped_column(String(MAX_NAME_LENGTH), unique=True, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    provider: Mapped[str] = mapped_column(String(32), default="openai", nullable=False)
    base_url: Mapped[str] = mapped_column(String(MAX_BASE_URL_LENGTH), nullable=False)
    default_model: Mapped[str] = mapped_column(String(MAX_MODEL_LENGTH), nullable=False)
    models: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    encrypted_key: Mapped[str] = mapped_column(Text, nullable=False)
    key_id: Mapped[str] = mapped_column(String(100), nullable=False)
    key_fingerprint: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), default=CONNECTION_STATUS_ACTIVE, nullable=False
    )
    last_probe_status: Mapped[str] = mapped_column(String(32), default=PROBE_NEVER, nullable=False)
    last_probe_detail: Mapped[str | None] = mapped_column(String(100))
    last_probe_at: Mapped[datetime | None]
    probe_id: Mapped[UUID | None]
    created_by_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)


class ModelConnectionGrant(TimestampMixin, Base):
    """The current model subset granted to one user, including default selection."""

    __tablename__ = "platform_model_connection_grants"

    connection_id: Mapped[UUID] = mapped_column(
        ForeignKey("platform_model_connections.id"), primary_key=True
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    models: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    granted_by_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
