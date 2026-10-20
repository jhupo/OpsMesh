"""Extension-owned facts; no duplicate Run, FlowRun or subscription state."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from opsmesh.shared.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Installation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "extension_installations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('requested','building','deploying','enabled','disabled',"
            "'draining','removed','failed','needs_attention')",
            name="valid_status",
        ),
    )

    version_id: Mapped[UUID] = mapped_column(ForeignKey("capability_versions.id"))
    requested_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    package_digest: Mapped[str] = mapped_column(String(64))
    namespace: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), default="requested", index=True)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    manifest: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict)
    permission_ceiling: Mapped[list[str]] = mapped_column(JSONB, default=list)
    artifact: Mapped[str | None] = mapped_column(String(240))
    error_code: Mapped[str | None] = mapped_column(String(100))


class ExtensionInstance(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "extension_instances"

    installation_id: Mapped[UUID] = mapped_column(ForeignKey("extension_installations.id"))
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    environment_id: Mapped[UUID | None]
    configuration_id: Mapped[UUID | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="enabled")
    revision: Mapped[int] = mapped_column(Integer, default=0)
    configuration: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict)
    permissions: Mapped[list[str]] = mapped_column(JSONB, default=list)
    model_connections: Mapped[list[str]] = mapped_column(JSONB, default=list)
    state_limit_bytes: Mapped[int] = mapped_column(BigInteger, default=10485760)


class ManagedDeployment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Explicit isolated process identity; invocation effects remain Invocation-owned."""

    __tablename__ = "extension_managed_deployments"
    __table_args__ = (
        CheckConstraint(
            "status IN ('starting','ready','stopping','stopped','needs_attention')",
            name="valid_status",
        ),
    )

    instance_id: Mapped[UUID] = mapped_column(ForeignKey("extension_instances.id"), index=True)
    instance_revision: Mapped[int]
    status: Mapped[str] = mapped_column(String(20), default="starting")
    revision: Mapped[int] = mapped_column(default=0)
    artifact: Mapped[str] = mapped_column(String(240))
    container_id: Mapped[str | None] = mapped_column(String(100))
    capacity_reservation_id: Mapped[UUID | None]
    error_code: Mapped[str | None] = mapped_column(String(100))


class Invocation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "extension_invocations"
    __table_args__ = (
        UniqueConstraint("instance_id", "principal_id", "operation_id"),
        CheckConstraint(
            "status IN ('queued','started','completed','failed','cancelled','needs_attention')",
            name="valid_status",
        ),
    )

    instance_id: Mapped[UUID] = mapped_column(ForeignKey("extension_instances.id"), index=True)
    installation_id: Mapped[UUID] = mapped_column(ForeignKey("extension_installations.id"))
    principal_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    parent_id: Mapped[UUID | None] = mapped_column(ForeignKey("extension_invocations.id"))
    namespace_id: Mapped[UUID] = mapped_column(ForeignKey("extension_namespaces.id"))
    export_id: Mapped[str] = mapped_column(String(100))
    operation_id: Mapped[str] = mapped_column(String(240))
    kind: Mapped[str] = mapped_column(String(20), default="operation")
    fingerprint: Mapped[str] = mapped_column(String(64))
    instance_revision: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    snapshot: Mapped[dict[str, object]] = mapped_column(JSONB)
    input_ciphertext: Mapped[str] = mapped_column()
    result_ciphertext: Mapped[str | None] = mapped_column(nullable=True)
    effects: Mapped[list[str]] = mapped_column(JSONB, default=list)
    error_code: Mapped[str | None] = mapped_column(String(100))
    cancel_requested: Mapped[bool] = mapped_column(default=False)
    deadline: Mapped[datetime] = mapped_column()
    reconciliation: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)


class ExtensionNamespace(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "extension_namespaces"
    __table_args__ = (UniqueConstraint("instance_id", "principal_id", "scope_key"),)

    instance_id: Mapped[UUID] = mapped_column(ForeignKey("extension_instances.id"))
    principal_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    scope_key: Mapped[str] = mapped_column(String(100))
    used_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    revision: Mapped[int] = mapped_column(Integer, default=0)


class ExtensionState(Base):
    __tablename__ = "extension_state"

    namespace_id: Mapped[UUID] = mapped_column(
        ForeignKey("extension_namespaces.id"), primary_key=True
    )
    key: Mapped[str] = mapped_column(String(512), primary_key=True)
    value: Mapped[object] = mapped_column(JSONB)
    version: Mapped[int] = mapped_column(Integer)
    size: Mapped[int] = mapped_column(BigInteger)
    storage_reservation_id: Mapped[UUID | None]


class ExtensionEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Published event fact, not a subscription or a parallel dispatch queue."""

    __tablename__ = "extension_events"
    __table_args__ = (UniqueConstraint("invocation_id", "operation_id"),)

    invocation_id: Mapped[UUID] = mapped_column(ForeignKey("extension_invocations.id"))
    operation_id: Mapped[str] = mapped_column(String(240))
    event_type: Mapped[str] = mapped_column(String(200))
    payload: Mapped[dict[str, object]] = mapped_column(JSONB)
    fingerprint: Mapped[str] = mapped_column(String(64))


class HostCallReceipt(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Stable RPC occurrence receipt, including unknown remote effects."""

    __tablename__ = "extension_host_calls"
    __table_args__ = (UniqueConstraint("invocation_id", "operation_id"),)

    invocation_id: Mapped[UUID] = mapped_column(ForeignKey("extension_invocations.id"))
    operation_id: Mapped[str] = mapped_column(String(240))
    fingerprint: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20))
    result_ciphertext: Mapped[str | None] = mapped_column(nullable=True)
    request_ciphertext: Mapped[str] = mapped_column()
    reconciliation: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict)
    revision: Mapped[int] = mapped_column(default=0)
