"""Encrypted workspace credential facts.

This table is the single storage owner of space-level secrets: model
provider keys and capability secret slots. Only the ciphertext, its
fingerprint and the key id are stored for reading; the plaintext exists
only inside the one resolve function, at the boundary that needs it, and
never reaches a route, an event, a log or a response. Rotation keeps one
row and advances its revision; revocation closes it for every future
resolution.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from opsmesh.shared.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

CREDENTIAL_KIND_MODEL_PROVIDER = "model_provider"
CREDENTIAL_KIND_CAPABILITY = "capability"
CREDENTIAL_KINDS = (CREDENTIAL_KIND_MODEL_PROVIDER, CREDENTIAL_KIND_CAPABILITY)

CREDENTIAL_STATUS_ACTIVE = "active"
CREDENTIAL_STATUS_REVOKED = "revoked"
CREDENTIAL_STATUSES = (CREDENTIAL_STATUS_ACTIVE, CREDENTIAL_STATUS_REVOKED)

MAX_NAME_LENGTH = 120
MAX_PAYLOAD_ENTRIES = 16
MAX_PAYLOAD_KEY_LENGTH = 100
MAX_PAYLOAD_VALUE_LENGTH = 4096
# A fingerprint is "sha256:" plus the 64 hex characters of the digest.
FINGERPRINT_LENGTH = 80


class WorkspaceCredential(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One encrypted workspace secret with its revision and revocation facts."""

    __tablename__ = "workspace_credentials"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('model_provider', 'capability')", name="workspace_credential_kind"
        ),
        CheckConstraint(
            "status IN ('active', 'revoked')", name="workspace_credential_status"
        ),
        CheckConstraint("revision >= 1", name="workspace_credential_revision"),
        Index(
            "uq_workspace_active_credential_name",
            "workspace_id",
            "name",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(MAX_NAME_LENGTH), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(FINGERPRINT_LENGTH), nullable=False)
    key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=CREDENTIAL_STATUS_ACTIVE, index=True
    )
    created_by_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(default=None)
