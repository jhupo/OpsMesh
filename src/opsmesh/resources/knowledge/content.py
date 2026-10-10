from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.platform.settings.policy import operational_configuration
from opsmesh.resources.files.models import WorkspaceFile
from opsmesh.resources.knowledge.models import KnowledgeSource
from opsmesh.resources.memory.indexing import TextChunk, chunk_text_with_offsets
from opsmesh.resources.storage.storage import (
    ObjectStorage,
    StorageObjectReadError,
    StorageObjectTooLargeError,
)
from opsmesh.runtime.instances.url_fetch import RuntimeUrlFetchError

SUPPORTED_TEXT_TYPES = frozenset(
    {
        "text/plain",
        "text/markdown",
        "text/csv",
        "application/json",
        "application/xml",
    }
)


class KnowledgeUrlFetcher(Protocol):
    def fetch(
        self,
        *,
        workspace_id: UUID,
        runtime_id: UUID,
        url: str,
        max_bytes: int,
    ) -> bytes: ...


class KnowledgeContentError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class PreparedKnowledgeContent:
    locator: str
    filename: str
    raw: bytes
    content_sha256: str
    chunks: tuple[TextChunk, ...]


def prepare_knowledge_content(
    session: Session,
    *,
    source: KnowledgeSource,
    storage: ObjectStorage | None,
    url_fetcher: KnowledgeUrlFetcher | None,
) -> PreparedKnowledgeContent:
    raw, locator, filename, expected_checksum = _load_source_bytes(
        session,
        source=source,
        storage=storage,
        url_fetcher=url_fetcher,
    )
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise KnowledgeContentError("source_not_utf8") from exc
    content_sha256 = hashlib.sha256(raw).hexdigest()
    if expected_checksum is not None and content_sha256 != expected_checksum:
        raise KnowledgeContentError("source_checksum_mismatch")
    policy = operational_configuration(session)
    chunks = tuple(
        chunk_text_with_offsets(
            text, size=policy.memory_chunk_size, overlap=policy.memory_chunk_overlap
        )
    )
    if not chunks:
        raise KnowledgeContentError("source_empty")
    return PreparedKnowledgeContent(
        locator=locator,
        filename=filename,
        raw=raw,
        content_sha256=content_sha256,
        chunks=chunks,
    )


def fetch_runtime_id(config: dict[str, object]) -> UUID:
    value = config.get("fetch_runtime_id")
    if not isinstance(value, str):
        raise ValueError("URL ingestion requires config.fetch_runtime_id")
    try:
        return UUID(value)
    except ValueError as exc:
        raise ValueError("URL ingestion config.fetch_runtime_id must be a UUID") from exc


def _load_source_bytes(
    session: Session,
    *,
    source: KnowledgeSource,
    storage: ObjectStorage | None,
    url_fetcher: KnowledgeUrlFetcher | None,
) -> tuple[bytes, str, str, str | None]:
    if source.source_type == "url":
        if source.uri is None or url_fetcher is None:
            raise KnowledgeContentError("fetch_runtime_unavailable")
        try:
            raw = url_fetcher.fetch(
                workspace_id=source.workspace_id,
                runtime_id=fetch_runtime_id(source.source_config),
                url=source.uri,
                max_bytes=operational_configuration(session).files.knowledge_source_bytes,
            )
        except RuntimeUrlFetchError as exc:
            raise KnowledgeContentError(exc.code) from exc
        except ValueError as exc:
            raise KnowledgeContentError("fetch_runtime_invalid") from exc
        return raw, source.uri, source.name, None

    if source.workspace_file_id is None:
        raise KnowledgeContentError("workspace_file_not_found")
    workspace_file = session.scalar(
        select(WorkspaceFile).where(
            WorkspaceFile.workspace_id == source.workspace_id,
            WorkspaceFile.id == source.workspace_file_id,
            WorkspaceFile.status == "active",
        )
    )
    if workspace_file is None:
        raise KnowledgeContentError("workspace_file_not_found")
    content_type = workspace_file.content_type.split(";", 1)[0].strip().lower()
    if content_type not in SUPPORTED_TEXT_TYPES:
        raise KnowledgeContentError("unsupported_content_type")
    if storage is None:
        raise KnowledgeContentError("storage_unavailable")
    try:
        raw = storage.read_limited(
            workspace_file.storage_key,
            operational_configuration(session).files.knowledge_source_bytes,
        )
    except StorageObjectTooLargeError as exc:
        raise KnowledgeContentError("source_too_large") from exc
    except FileNotFoundError as exc:
        raise KnowledgeContentError("source_object_not_found") from exc
    except (OSError, StorageObjectReadError, ValueError) as exc:
        raise KnowledgeContentError("source_object_unreadable") from exc
    return (
        raw,
        f"workspace-file://{workspace_file.id}",
        workspace_file.filename,
        workspace_file.checksum_sha256,
    )
