"""Capability facts: definitions, immutable versions and package content.

One definition carries the stable identity (type, key, visibility, status)
and its author; every content change creates a new version. A published
version is frozen forever: publishing computes one digest over the version
content and refuses to overwrite anything. Capability definitions never
store installation, binding or execution state — those belong to later
owners (extensions, orchestration, execution).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from opsmesh.shared.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

CAPABILITY_TYPE_MCP = "mcp"
CAPABILITY_TYPE_SKILL = "skill"
CAPABILITY_TYPE_TOOL = "tool"
CAPABILITY_TYPE_CHANNEL = "channel"
CAPABILITY_TYPE_EXTENSION = "extension"
CAPABILITY_TYPES = (
    CAPABILITY_TYPE_MCP,
    CAPABILITY_TYPE_SKILL,
    CAPABILITY_TYPE_TOOL,
    CAPABILITY_TYPE_CHANNEL,
    CAPABILITY_TYPE_EXTENSION,
)

VISIBILITY_PRIVATE = "private"
VISIBILITY_PUBLIC = "public"
VISIBILITIES = (VISIBILITY_PRIVATE, VISIBILITY_PUBLIC)

DEFINITION_STATUS_ACTIVE = "active"
DEFINITION_STATUS_DISABLED = "disabled"
DEFINITION_STATUS_DELETED = "deleted"
DEFINITION_STATUSES = (
    DEFINITION_STATUS_ACTIVE,
    DEFINITION_STATUS_DISABLED,
    DEFINITION_STATUS_DELETED,
)

VERSION_STATUS_DRAFT = "draft"
VERSION_STATUS_PUBLISHED = "published"
VERSION_STATUSES = (VERSION_STATUS_DRAFT, VERSION_STATUS_PUBLISHED)

SOURCE_SYSTEM = "system"
SOURCE_USER = "user"
SOURCES = (SOURCE_SYSTEM, SOURCE_USER)

MAX_KEY_LENGTH = 100
MAX_NAME_LENGTH = 160
MAX_DESCRIPTION_LENGTH = 2000
MAX_DIGEST_LENGTH = 64
MAX_STORAGE_KEY_LENGTH = 500
MAX_FILENAME_LENGTH = 260


class CapabilityDefinition(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One capability definition; the type-specific content lives in versions.

    ``key`` is the stable machine name inside its type and is unique across
    sources: a user definition cannot shadow an administrator-published definition.
    """

    __tablename__ = "capability_definitions"
    __table_args__ = (UniqueConstraint("type", "key", name="uq_capability_definitions_type_key"),)

    type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    key: Mapped[str] = mapped_column(String(MAX_KEY_LENGTH), nullable=False)
    name: Mapped[str] = mapped_column(String(MAX_NAME_LENGTH), nullable=False)
    description: Mapped[str] = mapped_column(
        String(MAX_DESCRIPTION_LENGTH), nullable=False, default=""
    )
    visibility: Mapped[str] = mapped_column(String(16), nullable=False, default=VISIBILITY_PRIVATE)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=DEFINITION_STATUS_ACTIVE, index=True
    )
    source: Mapped[str] = mapped_column(String(16), nullable=False, default=SOURCE_USER)
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )


class CapabilityVersion(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One immutable-or-draft version of a definition.

    ``configuration`` is the type-specific payload validated by the owning
    sub-package service; ``resource_manifest`` lists every packaged resource
    with its digest; ``dependencies`` and ``flow_templates`` are the fixed
    references a consumer may resolve. ``digest`` is computed once, at
    publish, and never changes afterwards.
    """

    __tablename__ = "capability_versions"
    __table_args__ = (
        UniqueConstraint(
            "definition_id", "version", name="uq_capability_versions_definition_id_version"
        ),
    )

    definition_id: Mapped[UUID] = mapped_column(
        ForeignKey("capability_definitions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=VERSION_STATUS_DRAFT, index=True
    )
    configuration: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    resource_manifest: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    dependencies: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    flow_templates: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    digest: Mapped[str | None] = mapped_column(String(MAX_DIGEST_LENGTH))
    package_digest: Mapped[str | None] = mapped_column(String(MAX_DIGEST_LENGTH))
    package_size: Mapped[int | None] = mapped_column(BigInteger)
    published_at: Mapped[datetime | None] = mapped_column(nullable=True)
    published_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )


class CapabilityPackage(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One immutable package blob attached to a version.

    The bytes live under the storage root at ``storage_key`` and are named
    by their digest, so a stored package cannot be replaced by different
    content; the row is the index, not a second copy of the content.
    """

    __tablename__ = "capability_packages"

    version_id: Mapped[UUID] = mapped_column(
        ForeignKey("capability_versions.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    filename: Mapped[str] = mapped_column(String(MAX_FILENAME_LENGTH), nullable=False)
    size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    digest: Mapped[str] = mapped_column(String(MAX_DIGEST_LENGTH), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(MAX_STORAGE_KEY_LENGTH), nullable=False)
