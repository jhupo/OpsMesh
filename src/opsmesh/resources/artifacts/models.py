"""Run and invocation artifact references.

An artifact records that one run or one invocation produced named content,
where that content is stored and what it digests to. It never stores the
source operation's state: the run and invocation owners keep their own
facts, and this table is the reference between them and the content.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import BigInteger, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from opsmesh.shared.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

ARTIFACT_SOURCE_RUN = "run"
ARTIFACT_SOURCE_INVOCATION = "invocation"
ARTIFACT_SOURCES = (ARTIFACT_SOURCE_RUN, ARTIFACT_SOURCE_INVOCATION)

MAX_NAME_LENGTH = 260
MAX_DIGEST_LENGTH = 64
MAX_STORAGE_KEY_LENGTH = 1000
MAX_MEDIA_TYPE_LENGTH = 200


class ResourceArtifact(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One produced artifact: its source reference and its content reference."""

    __tablename__ = "resource_artifacts"

    source_kind: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    source_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    owner_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(MAX_NAME_LENGTH), nullable=False)
    content_id: Mapped[UUID] = mapped_column(
        ForeignKey("resource_file_contents.id", ondelete="RESTRICT"), nullable=False
    )
    digest: Mapped[str] = mapped_column(String(MAX_DIGEST_LENGTH), nullable=False)
    size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    media_type: Mapped[str | None] = mapped_column(String(MAX_MEDIA_TYPE_LENGTH))
    storage_key: Mapped[str] = mapped_column(String(MAX_STORAGE_KEY_LENGTH), nullable=False)
    labels: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
