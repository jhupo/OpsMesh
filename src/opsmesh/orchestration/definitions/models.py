"""User compositions, immutable revisions and explicit collaboration grants."""

from typing import Any
from uuid import UUID

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from opsmesh.shared.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Definition(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "orchestration_definitions"

    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(20), default="flow")
    visibility: Mapped[str] = mapped_column(String(16), default="private")
    status: Mapped[str] = mapped_column(String(16), default="active")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    published_number: Mapped[int] = mapped_column(Integer, default=0)
    draft: Mapped[dict[str, Any]] = mapped_column(JSONB)


class DefinitionRevision(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "orchestration_definition_revisions"
    __table_args__ = (UniqueConstraint("definition_id", "number"),)

    definition_id: Mapped[UUID] = mapped_column(
        ForeignKey("orchestration_definitions.id"), index=True
    )
    number: Mapped[int] = mapped_column(Integer)
    digest: Mapped[str] = mapped_column(String(64))
    spec: Mapped[dict[str, Any]] = mapped_column(JSONB)
    published_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"))


class DefinitionGrant(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "orchestration_definition_grants"
    __table_args__ = (UniqueConstraint("definition_id", "user_id"),)

    definition_id: Mapped[UUID] = mapped_column(ForeignKey("orchestration_definitions.id"))
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    permissions: Mapped[list[str]] = mapped_column(JSONB)
