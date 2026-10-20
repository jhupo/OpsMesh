"""File, directory, version, content and grant facts.

A file lives in one directory and accumulates immutable versions; each
version references one immutable content blob by identifier and digest.
Grants carry the read/write/delete/use_in_execution permissions of the
file-usage contract, and the runtime policy records what execution may
consume and write back. Nothing here stores workspace or project scope:
that arrives with its owner in a later step.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import BigInteger, Boolean, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from opsmesh.shared.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

MAX_PATH_LENGTH = 1000
MAX_NAME_LENGTH = 260
MAX_MEDIA_TYPE_LENGTH = 200
MAX_DIGEST_LENGTH = 64
MAX_GRANT_PERMISSIONS = 8

FILE_PERMISSION_READ = "read"
FILE_PERMISSION_WRITE = "write"
FILE_PERMISSION_DELETE = "delete"
FILE_PERMISSION_USE_IN_EXECUTION = "use_in_execution"
FILE_PERMISSIONS = (
    FILE_PERMISSION_READ,
    FILE_PERMISSION_WRITE,
    FILE_PERMISSION_DELETE,
    FILE_PERMISSION_USE_IN_EXECUTION,
)


class ResourceDirectory(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One user-owned directory addressed by a validated relative path."""

    __tablename__ = "resource_directories"
    __table_args__ = (
        UniqueConstraint(
            "owner_user_id", "path", name="uq_resource_directories_owner_user_id_path"
        ),
    )

    owner_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    path: Mapped[str] = mapped_column(String(MAX_PATH_LENGTH), nullable=False)
    parent_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("resource_directories.id", ondelete="RESTRICT")
    )


class ResourceFile(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One user-owned file inside one directory; content lives in versions."""

    __tablename__ = "resource_files"
    __table_args__ = (
        UniqueConstraint(
            "owner_user_id",
            "directory_id",
            "name",
            name="uq_resource_files_owner_user_id_directory_id_name",
        ),
    )

    owner_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    directory_id: Mapped[UUID] = mapped_column(
        ForeignKey("resource_directories.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(MAX_NAME_LENGTH), nullable=False)


class ResourceFileContent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One immutable content blob; the identifier addresses the stored bytes."""

    __tablename__ = "resource_file_contents"

    digest: Mapped[str] = mapped_column(String(MAX_DIGEST_LENGTH), nullable=False, index=True)
    size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    storage_key: Mapped[str] = mapped_column(String(MAX_PATH_LENGTH), nullable=False)


class ResourceFileVersion(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One immutable version of a file, referencing one content blob."""

    __tablename__ = "resource_file_versions"
    __table_args__ = (
        UniqueConstraint("file_id", "version", name="uq_resource_file_versions_file_id_version"),
    )

    file_id: Mapped[UUID] = mapped_column(
        ForeignKey("resource_files.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content_id: Mapped[UUID] = mapped_column(
        ForeignKey("resource_file_contents.id", ondelete="RESTRICT"), nullable=False
    )
    digest: Mapped[str] = mapped_column(String(MAX_DIGEST_LENGTH), nullable=False)
    size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    media_type: Mapped[str | None] = mapped_column(String(MAX_MEDIA_TYPE_LENGTH))
    created_by_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )


class ResourceFileGrant(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One grantee's permissions on one file."""

    __tablename__ = "resource_file_grants"
    __table_args__ = (
        UniqueConstraint(
            "file_id", "grantee_user_id", name="uq_resource_file_grants_file_id_grantee_user_id"
        ),
    )

    file_id: Mapped[UUID] = mapped_column(
        ForeignKey("resource_files.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    grantee_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    permissions: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    created_by_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )


class ResourceRuntimePolicy(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """What execution may consume from one file and write back to it."""

    __tablename__ = "resource_runtime_policies"

    file_id: Mapped[UUID] = mapped_column(
        ForeignKey("resource_files.id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    frozen_input_versions: Mapped[list[int]] = mapped_column(JSONB, nullable=False, default=list)
    relative_paths: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    output_declarations: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    use_in_execution: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_by_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
