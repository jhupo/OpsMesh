"""File version listing and digest difference queries."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.resources.files.access import ResourceReader, authorize_read
from opsmesh.resources.files.models import ResourceFileVersion
from opsmesh.resources.files.service import get_file
from opsmesh.shared.errors import NotFoundError


@dataclass(frozen=True)
class VersionDiff:
    """What changed between two versions of one file."""

    from_version: int
    to_version: int
    identical: bool
    from_size: int
    to_size: int
    size_delta: int


def list_versions(
    session: Session, *, file_id: UUID, reader: ResourceReader
) -> list[ResourceFileVersion]:
    """Every version of one file the reader may see, newest first."""
    resource, grant = get_file(session, file_id=file_id, reader=reader)
    authorize_read(resource, reader=reader, grant=grant)
    return list(
        session.scalars(
            select(ResourceFileVersion)
            .where(ResourceFileVersion.file_id == resource.id)
            .order_by(ResourceFileVersion.version.desc())
        )
    )


def read_version(
    session: Session, *, file_id: UUID, version_number: int, reader: ResourceReader
) -> ResourceFileVersion:
    resource, grant = get_file(session, file_id=file_id, reader=reader)
    authorize_read(resource, reader=reader, grant=grant)
    version = session.scalar(
        select(ResourceFileVersion).where(
            ResourceFileVersion.file_id == resource.id,
            ResourceFileVersion.version == version_number,
        )
    )
    if version is None:
        raise NotFoundError("The file version does not exist")
    return version


def version_diff(
    session: Session,
    *,
    file_id: UUID,
    from_version: int,
    to_version: int,
    reader: ResourceReader,
) -> VersionDiff:
    """Compare two versions by digest and size; contents are never merged here."""
    source = read_version(
        session, file_id=file_id, version_number=from_version, reader=reader
    )
    target = read_version(session, file_id=file_id, version_number=to_version, reader=reader)
    return VersionDiff(
        from_version=source.version,
        to_version=target.version,
        identical=source.digest == target.digest,
        from_size=source.size,
        to_size=target.size,
        size_delta=target.size - source.size,
    )
