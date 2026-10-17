"""Model provider connection facts: references, catalog and probe state.

A connection references one encrypted credential; it never stores a key
itself. The model list and the default selection are the catalog the
workspace resolves models from, and the probe fields record what the last
connection test established — including an unknown outcome, which stays
distinguishable from a failure.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from opsmesh.shared.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

PROVIDER_OPENAI = "openai"
MODEL_PROVIDERS = (PROVIDER_OPENAI,)

CONNECTION_STATUS_ACTIVE = "active"
CONNECTION_STATUS_DISABLED = "disabled"
CONNECTION_STATUSES = (CONNECTION_STATUS_ACTIVE, CONNECTION_STATUS_DISABLED)

PROBE_NEVER = "never"
PROBE_RUNNING = "running"
PROBE_OK = "ok"
PROBE_FAILED = "failed"
PROBE_NEEDS_ATTENTION = "needs_attention"
PROBE_STATUSES = (PROBE_NEVER, PROBE_RUNNING, PROBE_OK, PROBE_FAILED, PROBE_NEEDS_ATTENTION)

MAX_NAME_LENGTH = 120
MAX_MODEL_LENGTH = 200
MAX_MODELS = 256
MAX_BASE_URL_LENGTH = 500
MAX_PROBE_DETAIL_LENGTH = 500


class ModelProviderConnection(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One OpenAI-compatible connection of one workspace."""

    __tablename__ = "model_provider_connections"
    __table_args__ = (
        CheckConstraint("provider = 'openai'", name="model_provider_connection_provider"),
        CheckConstraint(
            "status IN ('active', 'disabled')", name="model_provider_connection_status"
        ),
        CheckConstraint(
            "last_probe_status IN ('never', 'running', 'ok', 'failed', 'needs_attention')",
            name="model_provider_connection_probe_status",
        ),
        Index(
            "uq_model_provider_active_name",
            "workspace_id",
            "name",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        Index(
            "uq_model_provider_default",
            "workspace_id",
            unique=True,
            postgresql_where=text("is_default"),
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    credential_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspace_credentials.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(MAX_NAME_LENGTH), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(MAX_BASE_URL_LENGTH))
    default_model: Mapped[str] = mapped_column(String(MAX_MODEL_LENGTH), nullable=False)
    models: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=CONNECTION_STATUS_ACTIVE, index=True
    )
    last_probe_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=PROBE_NEVER
    )
    last_probe_detail: Mapped[str | None] = mapped_column(String(MAX_PROBE_DETAIL_LENGTH))
    last_probe_at: Mapped[datetime | None] = mapped_column(default=None)
    created_by_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
