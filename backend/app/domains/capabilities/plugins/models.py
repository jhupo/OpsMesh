from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
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


class PluginCredential(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "plugin_credentials"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "install_id"],
            ["plugin_installs.workspace_id", "plugin_installs.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("token_hash", name="uq_plugin_credential_hash"),
        Index("ix_plugin_credentials_install", "workspace_id", "install_id"),
    )
    workspace_id: Mapped[UUID] = mapped_column()
    install_id: Mapped[UUID] = mapped_column()
    generation: Mapped[int] = mapped_column(Integer)
    token_hash: Mapped[str] = mapped_column(String(64))
    permissions: Mapped[list[str]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20), default="active")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PluginValue(TimestampMixin, Base):
    __tablename__ = "plugin_values"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "install_id"],
            ["plugin_installs.workspace_id", "plugin_installs.id"],
            ondelete="CASCADE",
        ),
    )
    workspace_id: Mapped[UUID] = mapped_column(primary_key=True)
    install_id: Mapped[UUID] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    value: Mapped[dict[str, object]] = mapped_column(JSONB)


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


class PluginSource(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "plugin_sources"
    __table_args__ = (
        UniqueConstraint("workspace_id", "name", name="uq_plugin_source_name"),
        UniqueConstraint("workspace_id", "id", name="uq_plugin_source_scope"),
        CheckConstraint("generation > 0", name="generation"),
    )
    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(120))
    url: Mapped[str] = mapped_column(String(2048))
    sha256: Mapped[str] = mapped_column(String(64))
    allowed_hosts: Mapped[list[str]] = mapped_column(JSONB)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    generation: Mapped[int] = mapped_column(Integer, default=1)
    synced_generation: Mapped[int] = mapped_column(Integer, default=0)


class PluginCandidate(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "plugin_candidates"
    __table_args__ = (
        UniqueConstraint("source_id", "plugin_key", "version", name="uq_plugin_candidate_version"),
        UniqueConstraint("workspace_id", "source_id", "id", name="uq_plugin_candidate_scope"),
        ForeignKeyConstraint(
            ["workspace_id", "source_id"],
            ["plugin_sources.workspace_id", "plugin_sources.id"],
            ondelete="CASCADE",
        ),
    )
    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    source_id: Mapped[UUID] = mapped_column()
    plugin_key: Mapped[str] = mapped_column(String(120))
    version: Mapped[str] = mapped_column(String(64))
    publisher_key_id: Mapped[str] = mapped_column(String(120))
    url: Mapped[str] = mapped_column(String(2048))
    sha256: Mapped[str] = mapped_column(String(64))
    withdrawn: Mapped[bool] = mapped_column(Boolean, default=False)
    verified_release: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)


class PluginDownload(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "plugin_downloads"
    __table_args__ = (
        UniqueConstraint("workspace_id", "request_key", name="uq_plugin_download_request"),
        ForeignKeyConstraint(
            ["workspace_id", "source_id"],
            ["plugin_sources.workspace_id", "plugin_sources.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "source_id", "candidate_id"],
            [
                "plugin_candidates.workspace_id",
                "plugin_candidates.source_id",
                "plugin_candidates.id",
            ],
            ondelete="CASCADE",
        ),
        CheckConstraint("status IN ('queued', 'fetching', 'succeeded', 'failed')", name="status"),
        Index("ix_plugin_downloads_due", "status", "lease_until", "created_at"),
    )
    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    source_id: Mapped[UUID] = mapped_column()
    candidate_id: Mapped[UUID | None] = mapped_column(nullable=True)
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    request_key: Mapped[str] = mapped_column(String(120))
    source_generation: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_token: Mapped[UUID | None] = mapped_column(nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
