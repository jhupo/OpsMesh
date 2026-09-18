from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class PluginTrustKey(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "plugin_trust_keys"
    __table_args__ = (
        UniqueConstraint("workspace_id", "key_id", name="uq_plugin_trust_key"),
        UniqueConstraint("workspace_id", "id", name="uq_plugin_trust_scope"),
        CheckConstraint("status IN ('active', 'revoked')", name="ck_plugin_trust_status"),
    )
    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    key_id: Mapped[str] = mapped_column(String(120))
    plugin_key: Mapped[str] = mapped_column(String(120))
    public_key: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="active")


class PluginInstall(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "plugin_installs"
    __table_args__ = (
        UniqueConstraint("workspace_id", "plugin_key", name="uq_plugin_install_key"),
        UniqueConstraint("workspace_id", "id", name="uq_plugin_install_scope"),
        CheckConstraint(
            "status IN ('active', 'disabled', 'uninstalled')", name="ck_plugin_install_status"
        ),
        CheckConstraint("generation > 0", name="ck_plugin_generation"),
    )
    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    plugin_key: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(32), default="active")
    generation: Mapped[int] = mapped_column(Integer, default=1)
    current_version: Mapped[str] = mapped_column(String(64))


class PluginRelease(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "plugin_releases"
    __table_args__ = (
        UniqueConstraint("install_id", "version", name="uq_plugin_release_version"),
        UniqueConstraint("workspace_id", "install_id", "id", name="uq_plugin_release_scope"),
        CheckConstraint("status IN ('available', 'retired')", name="ck_plugin_release_status"),
        ForeignKeyConstraint(
            ["workspace_id", "install_id"],
            ["plugin_installs.workspace_id", "plugin_installs.id"],
            name="fk_plugin_release_install_scope",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "trust_key_id"],
            ["plugin_trust_keys.workspace_id", "plugin_trust_keys.id"],
            name="fk_plugin_release_trust_scope",
        ),
    )
    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    install_id: Mapped[UUID] = mapped_column()
    trust_key_id: Mapped[UUID] = mapped_column()
    version: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="available")
    package: Mapped[dict[str, object]] = mapped_column(JSONB)
    checksum: Mapped[str] = mapped_column(String(64))
    approved_permissions: Mapped[list[str]] = mapped_column(JSONB)


class PluginBinding(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "plugin_bindings"
    __table_args__ = (
        UniqueConstraint("workspace_id", "kind", "resource_id", name="uq_plugin_resource_owner"),
        UniqueConstraint("release_id", "capability_key", name="uq_plugin_release_capability"),
        Index("ix_plugin_bindings_install", "workspace_id", "install_id"),
        ForeignKeyConstraint(
            ["workspace_id", "install_id", "release_id"],
            ["plugin_releases.workspace_id", "plugin_releases.install_id", "plugin_releases.id"],
            name="fk_plugin_binding_release_scope",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "kind IN ('mcp_server', 'skill', 'message_trigger', 'reply_channel')",
            name="ck_plugin_binding_kind",
        ),
    )
    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    install_id: Mapped[UUID] = mapped_column()
    release_id: Mapped[UUID] = mapped_column()
    capability_key: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(32))
    resource_id: Mapped[UUID] = mapped_column()
    configuration: Mapped[dict[str, object]] = mapped_column(JSONB)
