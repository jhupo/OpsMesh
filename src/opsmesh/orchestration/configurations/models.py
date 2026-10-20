"""User compositions, immutable revisions and explicit collaboration grants."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from opsmesh.shared.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class UseConfiguration(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "orchestration_configurations"

    owner_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    definition_revision_id: Mapped[UUID] = mapped_column(
        ForeignKey("orchestration_definition_revisions.id")
    )
    root_directory_id: Mapped[UUID] = mapped_column(ForeignKey("resource_directories.id"))
    name: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(20), default="active")
    revision: Mapped[int] = mapped_column(Integer, default=1)


class ConfigurationRevision(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "orchestration_configuration_revisions"
    __table_args__ = (UniqueConstraint("configuration_id", "number"),)

    configuration_id: Mapped[UUID] = mapped_column(
        ForeignKey("orchestration_configurations.id"), index=True
    )
    number: Mapped[int] = mapped_column(Integer)
    specification: Mapped[dict[str, Any]] = mapped_column(JSONB)
    frozen_bindings: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    digest: Mapped[str] = mapped_column(String(64))
    created_by_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))


class ConfigurationMember(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "orchestration_configuration_members"
    __table_args__ = (UniqueConstraint("configuration_id", "user_id"),)

    configuration_id: Mapped[UUID] = mapped_column(ForeignKey("orchestration_configurations.id"))
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    permissions: Mapped[list[str]] = mapped_column(JSONB)
    directory_access: Mapped[bool] = mapped_column(default=False)


class ConfigurationInvitation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "orchestration_configuration_invitations"

    configuration_id: Mapped[UUID] = mapped_column(ForeignKey("orchestration_configurations.id"))
    invited_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    invited_by_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    permissions: Mapped[list[str]] = mapped_column(JSONB)
    directory_access: Mapped[bool] = mapped_column(default=False)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    expires_at: Mapped[datetime] = mapped_column()


class ConfigurationRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Trusted provenance; execution alone owns Run state."""

    __tablename__ = "orchestration_configuration_runs"

    configuration_id: Mapped[UUID] = mapped_column(ForeignKey("orchestration_configurations.id"))
    revision_id: Mapped[UUID] = mapped_column(
        ForeignKey("orchestration_configuration_revisions.id")
    )
    run_id: Mapped[UUID] = mapped_column(ForeignKey("execution_runs.id"), unique=True)
    fingerprint: Mapped[str] = mapped_column(String(64))


class ConfigurationClosure(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "orchestration_configuration_closures"

    configuration_id: Mapped[UUID] = mapped_column(
        ForeignKey("orchestration_configurations.id"), unique=True
    )
    requested_by_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(24), default="draining")
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
