"""Encrypted drafts and append-only configuration releases."""

from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, ForeignKeyConstraint, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from opsmesh.shared.db.base import Base, TimestampMixin


class CatalogEntry(TimestampMixin, Base):
    __tablename__ = "agent_catalog_entries"
    __table_args__ = (
        CheckConstraint("kind IN ('agent', 'provider')", name="catalog_kind"),
        CheckConstraint("revision > 0 AND published_number >= 0", name="catalog_revision"),
        CheckConstraint("visibility IN ('private', 'public')", name="catalog_visibility"),
    )

    kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    id: Mapped[str] = mapped_column(String(200), primary_key=True)
    owner_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"), index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    published_number: Mapped[int] = mapped_column(Integer, default=0)
    visibility: Mapped[str] = mapped_column(String(16), default="private")
    encrypted_draft: Mapped[str] = mapped_column(Text)
    key_id: Mapped[str] = mapped_column(String(100))


class CatalogVersion(TimestampMixin, Base):
    __tablename__ = "agent_catalog_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["kind", "entry_id"], ["agent_catalog_entries.kind", "agent_catalog_entries.id"]
        ),
        CheckConstraint("kind IN ('agent', 'provider')", name="catalog_version_kind"),
        CheckConstraint("number > 0", name="catalog_version_number"),
    )

    kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    entry_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    number: Mapped[int] = mapped_column(Integer, primary_key=True)
    encrypted_payload: Mapped[str] = mapped_column(Text)
    key_id: Mapped[str] = mapped_column(String(100))
    digest: Mapped[str] = mapped_column(String(64))


class CatalogCredential(TimestampMixin, Base):
    __tablename__ = "agent_catalog_credentials"

    id: Mapped[str] = mapped_column(String(200), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    encrypted_secret: Mapped[str] = mapped_column(Text)
    key_id: Mapped[str] = mapped_column(String(100))
