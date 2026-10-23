"""Durable transport records; execution owns actual instances and history."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
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
    __table_args__ = (UniqueConstraint("identity_id", "conversation_id"),)
    channel_id: Mapped[UUID] = mapped_column(ForeignKey("gateway_channels.id"), index=True)
    identity_id: Mapped[UUID] = mapped_column(ForeignKey("gateway_identities.id"))
    conversation_id: Mapped[str] = mapped_column(String(500), default="")
    target_kind: Mapped[str] = mapped_column(String(16))
    target_id: Mapped[UUID]
    allow_group: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)


class GatewaySession(TimestampMixin, Base):
    __tablename__ = "gateway_sessions"
    route_id: Mapped[UUID] = mapped_column(ForeignKey("gateway_routes.id"), primary_key=True)
    conversation_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    session_id: Mapped[UUID] = mapped_column(ForeignKey("execution_sessions.id"))


class GatewayInbox(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "gateway_inbox"
    __table_args__ = (UniqueConstraint("channel_id", "event_key"),)
    channel_id: Mapped[UUID] = mapped_column(ForeignKey("gateway_channels.id"), index=True)
    identity_id: Mapped[UUID] = mapped_column(ForeignKey("gateway_identities.id"))
    event_key: Mapped[str] = mapped_column(String(64))
    encrypted_payload: Mapped[str] = mapped_column(Text)
    key_id: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    run_id: Mapped[UUID | None]
    route_id: Mapped[UUID | None] = mapped_column(ForeignKey("gateway_routes.id"))
    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    route_revision: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(100))


class GatewayDelivery(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "gateway_deliveries"
    __table_args__ = (UniqueConstraint("inbox_id", "ordinal"),)
    channel_id: Mapped[UUID] = mapped_column(ForeignKey("gateway_channels.id"), index=True)
    inbox_id: Mapped[UUID | None] = mapped_column(ForeignKey("gateway_inbox.id"))
    ordinal: Mapped[int] = mapped_column(Integer, default=0)
    encrypted_payload: Mapped[str] = mapped_column(Text)
    key_id: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    remote_id: Mapped[str | None] = mapped_column(String(500))
    error_code: Mapped[str | None] = mapped_column(String(100))


class GatewayLease(Base):
    __tablename__ = "gateway_leases"
    channel_id: Mapped[UUID] = mapped_column(ForeignKey("gateway_channels.id"), primary_key=True)
    owner: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime]
