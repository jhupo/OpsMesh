from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.platform.settings.configuration import operational_configuration
from opsmesh.resources.files.models import WorkspaceFile
from opsmesh.resources.knowledge.models import KnowledgeSource
from opsmesh.resources.knowledge.url_fetch import KnowledgeURLFetchError
from opsmesh.resources.memory.indexing import TextChunk, chunk_text_with_offsets
from opsmesh.resources.storage.backends import (
    ObjectStorage,
    StorageObjectReadError,
    StorageObjectTooLargeError,
)

SUPPORTED_TEXT_TYPES = frozenset(
    {
        "text/plain",
        "text/markdown",
        "text/csv",
        "application/json",
        "application/xml",
    }
)


class KnowledgeURLFetcher(Protocol):
    def fetch(
        self,
        *,
        workspace_id: UUID,
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


@dataclass(frozen=True, slots=True)
class KnowledgeContentPlan:
    workspace_id: UUID
    source_type: str
    locator: str
    filename: str
    max_bytes: int
    chunk_size: int
    chunk_overlap: int
    text: str | None = None
    storage_key: str | None = None
    checksum_sha256: str | None = None


def read_knowledge_content(
    plan: KnowledgeContentPlan,
    *,
    storage: ObjectStorage | None,
    url_fetcher: KnowledgeURLFetcher | None,
) -> PreparedKnowledgeContent:
    raw = _read_source_bytes(plan, storage=storage, url_fetcher=url_fetcher)
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise KnowledgeContentError("source_not_utf8") from exc
    content_sha256 = hashlib.sha256(raw).hexdigest()
    if plan.checksum_sha256 is not None and content_sha256 != plan.checksum_sha256:
        raise KnowledgeContentError("source_checksum_mismatch")
    chunks = tuple(chunk_text_with_offsets(text, size=plan.chunk_size, overlap=plan.chunk_overlap))
    if not chunks:
        raise KnowledgeContentError("source_empty")
    return PreparedKnowledgeContent(
        locator=plan.locator,
        filename=plan.filename,
        raw=raw,
        content_sha256=content_sha256,
        chunks=chunks,
    )


def resolve_knowledge_content(
    session: Session,
    *,
    source: KnowledgeSource,
) -> KnowledgeContentPlan:
    policy = operational_configuration(session)
    plan = KnowledgeContentPlan(
        workspace_id=source.workspace_id,
        source_type=source.source_type,
        locator="",
        filename=source.name,
        max_bytes=policy.files.knowledge_source_bytes,
        chunk_size=policy.memory_chunk_size,
        chunk_overlap=policy.memory_chunk_overlap,
    )
    if source.source_type == "text":
        content = source.source_config.get("text")
        if not isinstance(content, str) or not content.strip():
            raise KnowledgeContentError("source_empty")
        return replace(
            plan,
            locator=f"knowledge-source://{source.id}/v{source.version}",
            filename=source.name,
            text=content,
        )
    if source.source_type == "url":
        if source.uri is None:
            raise KnowledgeContentError("fetch_url_invalid")
        return replace(plan, locator=source.uri)

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
    return replace(
        plan,
        locator=f"workspace-file://{workspace_file.id}",
        filename=workspace_file.filename,
        storage_key=workspace_file.storage_key,
        checksum_sha256=workspace_file.checksum_sha256,
    )


def _read_source_bytes(
    plan: KnowledgeContentPlan,
    *,
    storage: ObjectStorage | None,
    url_fetcher: KnowledgeURLFetcher | None,
) -> bytes:
    if plan.source_type == "text":
        assert plan.text is not None
        raw = plan.text.encode("utf-8")
    elif plan.source_type == "url":
        if url_fetcher is None:
            raise KnowledgeContentError("fetcher_unavailable")
        try:
            raw = url_fetcher.fetch(
                workspace_id=plan.workspace_id, url=plan.locator, max_bytes=plan.max_bytes
            )
        except KnowledgeURLFetchError as exc:
            raise KnowledgeContentError(exc.code) from exc
        except ValueError as exc:
            raise KnowledgeContentError("fetch_url_invalid") from exc
    else:
        raw = _read_workspace_file(plan, storage)
    if len(raw) > plan.max_bytes:
        raise KnowledgeContentError("source_too_large")
    return raw


def _read_workspace_file(plan: KnowledgeContentPlan, storage: ObjectStorage | None) -> bytes:
    if storage is None:
        raise KnowledgeContentError("storage_unavailable")
    assert plan.storage_key is not None
    try:
        return storage.read_limited(plan.storage_key, plan.max_bytes)
    except StorageObjectTooLargeError as exc:
        raise KnowledgeContentError("source_too_large") from exc
    except FileNotFoundError as exc:
        raise KnowledgeContentError("source_object_not_found") from exc
    except (OSError, StorageObjectReadError, ValueError) as exc:
        raise KnowledgeContentError("source_object_unreadable") from exc
