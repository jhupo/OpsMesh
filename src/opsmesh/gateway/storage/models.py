"""Durable transport records; execution owns actual instances and history."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from opsmesh.shared.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class GatewayChannel(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "gateway_channels"
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(200))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    encrypted_settings: Mapped[str] = mapped_column(Text)
    key_id: Mapped[str] = mapped_column(String(100))
    send_window_started: Mapped[datetime | None]
    send_window_count: Mapped[int] = mapped_column(Integer, default=0)


class GatewayIdentity(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "gateway_identities"
    __table_args__ = (UniqueConstraint("channel_id", "identity_key"),)
    channel_id: Mapped[UUID] = mapped_column(ForeignKey("gateway_channels.id"), index=True)
    identity_key: Mapped[str] = mapped_column(String(64))
    scope: Mapped[str] = mapped_column(String(300))
    id_kind: Mapped[str] = mapped_column(String(64))
    external_id: Mapped[str] = mapped_column(String(500))
    encrypted_profile: Mapped[str] = mapped_column(Text)
    key_id: Mapped[str] = mapped_column(String(100))


class GatewayAccountBinding(TimestampMixin, Base):
    __tablename__ = "gateway_account_bindings"
    identity_id: Mapped[UUID] = mapped_column(ForeignKey("gateway_identities.id"), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    verified_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"))


class GatewayConversation(TimestampMixin, Base):
    __tablename__ = "gateway_conversations"
    channel_id: Mapped[UUID] = mapped_column(ForeignKey("gateway_channels.id"), primary_key=True)
    conversation_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    external_id: Mapped[str] = mapped_column(String(500))
    kind: Mapped[str] = mapped_column(String(16))
    encrypted_profile: Mapped[str] = mapped_column(Text)
    key_id: Mapped[str] = mapped_column(String(100))


class GatewayMember(TimestampMixin, Base):
    __tablename__ = "gateway_members"
    identity_id: Mapped[UUID] = mapped_column(ForeignKey("gateway_identities.id"), primary_key=True)
    conversation_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    external_role: Mapped[str | None] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(32), default="message")


class GatewayRoute(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "gateway_routes"
    __table_args__ = (
        UniqueConstraint("identity_id", "conversation_id"),
        UniqueConstraint("channel_id", "conversation_key"),
        CheckConstraint(
            "(identity_id IS NOT NULL AND conversation_key IS NULL AND user_id IS NULL) OR "
            "(identity_id IS NULL AND conversation_key IS NOT NULL AND user_id IS NOT NULL "
            "AND allow_group AND conversation_id = '')",
            name="subject",
        ),
    )
    channel_id: Mapped[UUID] = mapped_column(ForeignKey("gateway_channels.id"), index=True)
    identity_id: Mapped[UUID | None] = mapped_column(ForeignKey("gateway_identities.id"))
    conversation_key: Mapped[str | None] = mapped_column(String(64))
    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    conversation_id: Mapped[str] = mapped_column(String(500), default="")
    target_kind: Mapped[str] = mapped_column(String(16))
    target_id: Mapped[UUID]
    allow_group: Mapped[bool] = mapped_column(Boolean, default=False)
    output_mode: Mapped[str] = mapped_column(String(16), default="text")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)


class GatewaySession(TimestampMixin, Base):
    __tablename__ = "gateway_sessions"
    scope_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    session_id: Mapped[UUID] = mapped_column(ForeignKey("execution_sessions.id"))


class GatewayInbox(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "gateway_inbox"
    __table_args__ = (
        UniqueConstraint("channel_id", "event_key"),
        Index(
            "ix_gateway_inbox_conversation_queue",
            "channel_id",
            "conversation_key",
            "status",
            "created_at",
            "id",
        ),
    )
    channel_id: Mapped[UUID] = mapped_column(ForeignKey("gateway_channels.id"), index=True)
    identity_id: Mapped[UUID] = mapped_column(ForeignKey("gateway_identities.id"))
    event_key: Mapped[str] = mapped_column(String(64))
    conversation_key: Mapped[str] = mapped_column(String(64), index=True)
    encrypted_payload: Mapped[str] = mapped_column(Text)
    key_id: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    run_id: Mapped[UUID | None]
    route_id: Mapped[UUID | None] = mapped_column(ForeignKey("gateway_routes.id"))
    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    route_revision: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(100))
    encrypted_context: Mapped[str | None] = mapped_column(Text)
    event_cursor: Mapped[int] = mapped_column(Integer, default=0)
    control: Mapped[str | None] = mapped_column(String(16))
    queue_key: Mapped[str | None] = mapped_column(String(64), index=True)
    filter_revision: Mapped[int | None] = mapped_column(Integer)
    filter_attempts: Mapped[int] = mapped_column(Integer, default=0)
    filter_retry_at: Mapped[datetime | None]


class GatewayDelivery(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "gateway_deliveries"
    __table_args__ = (UniqueConstraint("inbox_id", "ordinal"),)
    channel_id: Mapped[UUID] = mapped_column(ForeignKey("gateway_channels.id"), index=True)
    inbox_id: Mapped[UUID | None] = mapped_column(ForeignKey("gateway_inbox.id"))
    ordinal: Mapped[int] = mapped_column(Integer, default=0)
    kind: Mapped[str] = mapped_column(String(16), default="message")
    encrypted_payload: Mapped[str] = mapped_column(Text)
    key_id: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    remote_id: Mapped[str | None] = mapped_column(String(500))
    error_code: Mapped[str | None] = mapped_column(String(100))


class GatewayPush(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "gateway_pushes"
    channel_id: Mapped[UUID] = mapped_column(ForeignKey("gateway_channels.id"), index=True)
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    fingerprint: Mapped[str] = mapped_column(String(64))
    encrypted_result: Mapped[str] = mapped_column(Text)
    key_id: Mapped[str] = mapped_column(String(100))


class GatewayBinding(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "gateway_bindings"
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    expires_at: Mapped[datetime]
    encrypted_state: Mapped[str | None] = mapped_column(Text)
    key_id: Mapped[str] = mapped_column(String(100))
    channel_id: Mapped[UUID | None] = mapped_column(ForeignKey("gateway_channels.id"))


class GatewayLease(Base):
    __tablename__ = "gateway_leases"
    channel_id: Mapped[UUID] = mapped_column(ForeignKey("gateway_channels.id"), primary_key=True)
    owner: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime]
