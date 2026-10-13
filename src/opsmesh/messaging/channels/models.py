"""Workspace-scoped channel configuration and durable transport facts."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from opsmesh.shared.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class ChannelConnection(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A remote robot account; routing and execution targets belong to Automation."""

    __tablename__ = "channel_connections"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_channel_connection_scope"),
        UniqueConstraint("workspace_id", "install_id", "name", name="uq_channel_connection_name"),
        ForeignKeyConstraint(
            ["workspace_id", "install_id"],
            ["plugin_installs.workspace_id", "plugin_installs.id"],
        ),
        CheckConstraint("status IN ('active', 'disabled')", name="channel_connection_status"),
        CheckConstraint("configuration_revision > 0", name="channel_connection_revision"),
    )
    workspace_id: Mapped[UUID] = mapped_column()
    install_id: Mapped[UUID] = mapped_column()
    capability_key: Mapped[str] = mapped_column(String(120))
    name: Mapped[str] = mapped_column(String(160))
    account_id: Mapped[str] = mapped_column(String(160))
    endpoint_url: Mapped[str] = mapped_column(String(2048))
    configuration: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict)
    configuration_revision: Mapped[int] = mapped_column(Integer, default=1)
    encrypted_signing_secret: Mapped[str] = mapped_column(Text)
    encryption_key_id: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(20), default="active")


class ChannelIngress(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Deduplicate before evaluating mutable Automation routing."""

    __tablename__ = "channel_ingress"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "connection_id",
            "source",
            "external_id",
            name="uq_channel_ingress_event",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "connection_id"],
            ["channel_connections.workspace_id", "channel_connections.id"],
        ),
        ForeignKeyConstraint(
            ["workspace_id", "automation_id"], ["automations.workspace_id", "automations.id"]
        ),
    )
    workspace_id: Mapped[UUID] = mapped_column()
    connection_id: Mapped[UUID] = mapped_column()
    source: Mapped[str] = mapped_column(String(260))
    external_id: Mapped[str] = mapped_column(String(160))
    content_hash: Mapped[str] = mapped_column(String(64))
    automation_id: Mapped[UUID] = mapped_column()
    event_id: Mapped[UUID] = mapped_column()


class ChannelDelivery(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A stable outgoing side effect, independent of model execution."""

    __tablename__ = "channel_deliveries"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "event_id", "sequence", name="uq_channel_delivery_sequence"
        ),
        ForeignKeyConstraint(
            ["workspace_id", "connection_id"],
            ["channel_connections.workspace_id", "channel_connections.id"],
        ),
        CheckConstraint(
            "status IN ('pending','sending','delivered','failed','unknown')",
            name="channel_delivery_status",
        ),
    )
    workspace_id: Mapped[UUID] = mapped_column()
    connection_id: Mapped[UUID] = mapped_column()
    event_id: Mapped[UUID] = mapped_column()
    sequence: Mapped[int] = mapped_column(Integer)
    connection_revision: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    provider_message_id: Mapped[str | None] = mapped_column(String(260))
    error_code: Mapped[str | None] = mapped_column(String(120))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
