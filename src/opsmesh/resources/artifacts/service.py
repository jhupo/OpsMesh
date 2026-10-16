"""Artifact recording and content access.

Recording an artifact is authorized by an injected contract whose real
owner arrives with the run and invocation steps; until then every
recording is refused instead of trusting the caller. Reading follows the
same owner-or-grant authorization as files: an artifact is content with a
source reference, not a second content store.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.governance.audit.service import AuditActor, append_platform_event
from opsmesh.resources.artifacts.models import (
    ARTIFACT_SOURCES,
    ResourceArtifact,
)
from opsmesh.resources.files.access import ResourceReader
from opsmesh.resources.files.models import ResourceFileContent
from opsmesh.resources.storage.filesystem import (
    MAX_CONTENT_BYTES,
    content_digest,
    new_content_id,
    read_content,
    write_content,
)
from opsmesh.resources.storage.transactions import stage_content
from opsmesh.shared.errors import ForbiddenError, InvalidArgumentError, NotFoundError


class ArtifactRecordAuthorization(Protocol):
    """Whether one reader may record an artifact for one source."""

    def authorize_record(
        self, *, reader: ResourceReader, source_kind: str, source_id: UUID
    ) -> None:
        """Raise a domain error when the source may not record artifacts."""
        ...


class RejectingArtifactAuthorization:
    """The default: no producer exists before the run and invocation steps."""

    def authorize_record(
        self, *, reader: ResourceReader, source_kind: str, source_id: UUID
    ) -> None:
        _ = reader, source_id
        raise ForbiddenError(
            "Artifact recording is not assembled for this source",
            details={"source_kind": source_kind},
        )


def record_artifact(
    session: Session,
    *,
    source_kind: str,
    source_id: UUID,
    owner_id: UUID,
    name: str,
    content: bytes,
    media_type: str | None,
    labels: dict[str, object],
    reader: ResourceReader,
    actor: AuditActor,
    authorization: ArtifactRecordAuthorization,
    storage_root: Path,
) -> ResourceArtifact:
    """Record one produced artifact and store its content.

    The content blob is written and staged before the artifact row exists,
    inside the caller's transaction: a rollback deletes the blob, so a
    failed recording leaves neither a row nor an orphan.
    """
    if source_kind not in ARTIFACT_SOURCES:
        raise InvalidArgumentError(
            "The artifact source kind is unknown",
            details={"known_sources": list(ARTIFACT_SOURCES)},
        )
    if not name.strip():
        raise InvalidArgumentError("An artifact name is required")
    if not content:
        raise InvalidArgumentError("The artifact content must not be empty")
    if len(content) > MAX_CONTENT_BYTES:
        raise InvalidArgumentError("The artifact content exceeds the permitted size")
    authorization.authorize_record(reader=reader, source_kind=source_kind, source_id=source_id)
    digest = content_digest(content)
    content_id = new_content_id()
    stored_digest = write_content(storage_root, content_id=content_id, payload=content)
    stage_content(session, storage_root, content_id)
    session.add(
        ResourceFileContent(
            id=content_id,
            digest=stored_digest,
            size=len(content),
            storage_key=str(content_id),
        )
    )
    # The artifact row references this content row: flush it first so the
    # insert order never depends on autoflush being enabled.
    session.flush()
    artifact = ResourceArtifact(
        source_kind=source_kind,
        source_id=source_id,
        owner_user_id=owner_id,
        name=name,
        content_id=content_id,
        digest=stored_digest,
        size=len(content),
        media_type=media_type,
        storage_key=str(content_id),
        labels=labels,
    )
    session.add(artifact)
    session.flush()
    append_platform_event(
        session,
        actor=actor,
        action="resource.artifact_recorded",
        target_type="resource_artifact",
        target_id=str(artifact.id),
        summary=f"Recorded artifact {name}",
        details={
            "source_kind": source_kind,
            "source_id": str(source_id),
            "digest": digest,
            "size": len(content),
        },
    )
    return artifact


def list_artifacts(
    session: Session,
    *,
    reader: ResourceReader,
    source_kind: str | None = None,
    source_id: UUID | None = None,
) -> list[ResourceArtifact]:
    """The reader's own artifacts, optionally scoped to one source."""
    query = select(ResourceArtifact).where(ResourceArtifact.owner_user_id == reader.user_id)
    if source_kind is not None:
        query = query.where(ResourceArtifact.source_kind == source_kind)
    if source_id is not None:
        query = query.where(ResourceArtifact.source_id == source_id)
    return list(
        session.scalars(
            query.order_by(
                ResourceArtifact.created_at.desc(), ResourceArtifact.id.desc()
            )
        )
    )


def get_artifact(
    session: Session, *, artifact_id: UUID, reader: ResourceReader
) -> ResourceArtifact:
    artifact = session.get(ResourceArtifact, artifact_id)
    if artifact is None or artifact.owner_user_id != reader.user_id:
        raise NotFoundError("The artifact does not exist")
    return artifact


def read_artifact_content(
    session: Session, *, artifact_id: UUID, reader: ResourceReader, storage_root: Path
) -> tuple[ResourceArtifact, bytes]:
    """Read one artifact's content, verifying the blob against its digest."""
    artifact = get_artifact(session, artifact_id=artifact_id, reader=reader)
    payload = read_content(
        storage_root, content_id=artifact.content_id, expected_digest=artifact.digest
    )
    return artifact, payload


__all__ = [
    "ArtifactRecordAuthorization",
    "RejectingArtifactAuthorization",
    "get_artifact",
    "list_artifacts",
    "read_artifact_content",
    "record_artifact",
]
