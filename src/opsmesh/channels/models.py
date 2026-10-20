"""Persistent channel facts; vendor protocols belong to external extension packages."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from opsmesh.shared.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class ChannelAccount(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "channel_accounts"
    __table_args__ = (UniqueConstraint("owner_id", "channel_type", "external_account_id"),)

    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    configuration_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("orchestration_configurations.id"), index=True
    )
    channel_type: Mapped[str] = mapped_column(String(50))
    external_account_id: Mapped[str] = mapped_column(String(200))
    installation_id: Mapped[UUID] = mapped_column(ForeignKey("extension_installations.id"))
    version_id: Mapped[UUID] = mapped_column(ForeignKey("capability_versions.id"))
    status: Mapped[str] = mapped_column(String(20), default="enabled", index=True)
    epoch: Mapped[int] = mapped_column(Integer, default=1)
    configuration: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict)


class ChannelIdentityMapping(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "channel_identity_mappings"
    __table_args__ = (
        UniqueConstraint("account_id", "external_identity", name="uq_channel_identity_external"),
    )

    account_id: Mapped[UUID] = mapped_column(ForeignKey("channel_accounts.id", ondelete="CASCADE"))
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    external_identity: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="active")


class ChannelRoute(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "channel_routes"
    __table_args__ = (UniqueConstraint("account_id", "conversation_key"),)

    account_id: Mapped[UUID] = mapped_column(ForeignKey("channel_accounts.id", ondelete="CASCADE"))
    conversation_key: Mapped[str] = mapped_column(String(400))
    configuration_id: Mapped[UUID] = mapped_column(ForeignKey("orchestration_configurations.id"))
    flow_revision_id: Mapped[UUID] = mapped_column(
        ForeignKey("orchestration_definition_revisions.id")
    )
    enabled: Mapped[bool] = mapped_column(default=True)
    input_mapping: Mapped[dict[str, str]] = mapped_column(JSONB, default=dict)


class ChannelInbox(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "channel_inbox"
    __table_args__ = (
        UniqueConstraint("account_id", "external_message_id", name="uq_channel_inbox_message"),
    )

    account_id: Mapped[UUID] = mapped_column(ForeignKey("channel_accounts.id"), index=True)
    connection_epoch: Mapped[int] = mapped_column(Integer)
    external_message_id: Mapped[str] = mapped_column(String(240))
    conversation_key: Mapped[str] = mapped_column(String(400))
    payload_ciphertext: Mapped[str] = mapped_column()
    payload_key_id: Mapped[str] = mapped_column(String(64))
    payload_digest: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default="received", index=True)
    flow_run_id: Mapped[UUID | None] = mapped_column(ForeignKey("flow_runs.id"))
    failure_code: Mapped[str | None] = mapped_column(String(100))
    received_at: Mapped[datetime] = mapped_column()


class ChannelDelivery(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "channel_deliveries"
    __table_args__ = (
        UniqueConstraint("account_id", "idempotency_key", name="uq_channel_delivery_key"),
    )

    account_id: Mapped[UUID] = mapped_column(ForeignKey("channel_accounts.id"), index=True)
    connection_epoch: Mapped[int] = mapped_column(Integer)
    idempotency_key: Mapped[str] = mapped_column(String(240))
    conversation_key: Mapped[str] = mapped_column(String(400))
    payload_ciphertext: Mapped[str] = mapped_column()
    payload_key_id: Mapped[str] = mapped_column(String(64))
    payload_digest: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    external_message_id: Mapped[str | None] = mapped_column(String(240))
    failure_code: Mapped[str | None] = mapped_column(String(100))
    reconciliation_note: Mapped[str | None] = mapped_column(String(2000))
    reconciled_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
