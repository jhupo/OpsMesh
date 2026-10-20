"""Artifact list, metadata and content download APIs.

Recording is internal: the run and invocation producers arrive with their
own steps and record through the service under an injected authorization.
These routes serve the read side only.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.orm import Session

from opsmesh.resources.artifacts import service
from opsmesh.resources.artifacts.models import ARTIFACT_SOURCES, ResourceArtifact
from opsmesh.resources.artifacts.schemas import ArtifactView
from opsmesh.resources.files.access import ResourceReader
from opsmesh.resources.files.content import content_download_response
from opsmesh.shared.errors import InvalidArgumentError
from opsmesh.shared.http.dependencies import get_db_session

router = APIRouter(prefix="/resources/artifacts", tags=["resources"])

DbSession = Annotated[Session, Depends(get_db_session)]


def require_resource_reader() -> ResourceReader:
    """Placeholder for the assembled identity reader; bootstrap injects it."""
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Resource reading is not assembled",
    )


ReaderDep = Annotated[ResourceReader, Depends(require_resource_reader)]


def _storage_root(request: Request) -> Path:
    root = getattr(request.app.state, "storage_root", None)
    if not isinstance(root, str) or not root:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Resource storage is not assembled",
        )
    return Path(root)


def _artifact_view(artifact: ResourceArtifact) -> ArtifactView:
    return ArtifactView(
        artifact_id=artifact.id,
        source_kind=artifact.source_kind,
        source_id=artifact.source_id,
        owner_user_id=artifact.owner_user_id,
        name=artifact.name,
        digest=artifact.digest,
        size=artifact.size,
        media_type=artifact.media_type,
        labels=dict(artifact.labels),
        created_at=artifact.created_at,
    )


@router.get("", response_model=list[ArtifactView], operation_id="resources.artifacts.list")
def list_artifacts(
    session: DbSession,
    reader: ReaderDep,
    source_kind: Annotated[str | None, Query(alias="source", max_length=20)] = None,
    source_id: UUID | None = None,
) -> list[ArtifactView]:
    """One page-free listing of the reader's own artifacts."""
    if source_kind is not None and source_kind not in ARTIFACT_SOURCES:
        raise InvalidArgumentError(
            "The artifact source filter is unknown",
            details={"known_sources": list(ARTIFACT_SOURCES)},
        )
    artifacts = service.list_artifacts(
        session, reader=reader, source_kind=source_kind, source_id=source_id
    )
    return [_artifact_view(artifact) for artifact in artifacts]


@router.get("/{artifact_id}", response_model=ArtifactView, operation_id="resources.artifacts.read")
def read_artifact(artifact_id: UUID, session: DbSession, reader: ReaderDep) -> ArtifactView:
    artifact = service.get_artifact(session, artifact_id=artifact_id, reader=reader)
    return _artifact_view(artifact)


@router.get("/{artifact_id}/content", operation_id="resources.artifacts.content.read")
def read_artifact_content(
    artifact_id: UUID, session: DbSession, reader: ReaderDep, request: Request
) -> Response:
    """Download one artifact's verified bytes."""
    artifact, payload = service.read_artifact_content(
        session, artifact_id=artifact_id, reader=reader, storage_root=_storage_root(request)
    )
    return content_download_response(
        payload, filename=artifact.name, media_type=artifact.media_type
    )
