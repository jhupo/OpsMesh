"""Bounded document writes, source publication, and stale citation reads."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import PurePath
from typing import Any, BinaryIO
from uuid import UUID, uuid4

import anyio
from sqlalchemy import delete as sql_delete
from sqlalchemy import select

from opsmesh.utils import run_sync

from .errors import (
    IndexUnavailable,
    InvalidDocument,
    KnowledgeNotFound,
    StaleReference,
)
from .knowledge_bases import display_name, metadata_value, page_bounds
from .models import DocumentRecord, now
from .schemas import DocumentInfo, DocumentText
from .text import encode_text, normalize


def reset_index(record: DocumentRecord, epoch: UUID) -> None:
    """Invalidate old workers and citations before making an update visible."""
    record.index_key = uuid4()
    record.index_epoch = epoch
    record.status = "pending"
    record.indexed_profile = None
    record.claim_id = None
    record.lease_until = None
    record.attempts = 0
    record.retry_at = None
    record.error = None
    record.updated_at = now()


def document_info(record: DocumentRecord) -> DocumentInfo:
    """Detach document state without exposing source paths or SQL objects."""
    values = {
        name: getattr(record, name)
        for name in DocumentInfo.model_fields
        if name != "metadata"
    }
    return DocumentInfo(**values, metadata=record.metadata_)


@dataclass(frozen=True)
class Documents:
    """All document operations stay within the selected knowledge base."""

    knowledge_base: Any

    @property
    def runtime(self):
        """Use the same resource activation as the selected library."""
        return self.knowledge_base.runtime

    async def _record(self, session, document_id) -> DocumentRecord:
        record = await session.scalar(
            select(DocumentRecord).where(
                DocumentRecord.id == UUID(str(document_id)),
                DocumentRecord.knowledge_base_id == self.knowledge_base.id,
            )
        )
        if record is None:
            raise KnowledgeNotFound(
                "Document was not found in this knowledge base"
            )
        return record

    async def get(self, document_id: UUID | str) -> DocumentInfo:
        """Read one document's metadata and asynchronous indexing state."""
        async with (
            self.runtime.access(),
            self.runtime.database.session() as session,
        ):
            await self.knowledge_base.fetch(session)
            return document_info(await self._record(session, document_id))

    async def list(
        self, *, limit: int | None = None, offset: int = 0
    ) -> list[DocumentInfo]:
        """Read a bounded page of this library's document metadata."""
        limit = page_bounds(limit, offset, self.runtime.settings)
        async with (
            self.runtime.access(),
            self.runtime.database.session() as session,
        ):
            await self.knowledge_base.fetch(session)
            records = await session.scalars(
                select(DocumentRecord)
                .where(
                    DocumentRecord.knowledge_base_id == self.knowledge_base.id
                )
                .order_by(DocumentRecord.created_at, DocumentRecord.id)
                .limit(limit)
                .offset(offset)
            )
            return [document_info(record) for record in records]

    async def add(
        self, text: str, *, name: str, metadata: dict[str, Any] | None = None
    ) -> DocumentInfo:
        """Accept new UTF-8 text without waiting for indexing completion."""
        return await self._write(
            encode_text(text, self.runtime.settings),
            name=name,
            metadata=metadata,
        )

    async def import_file(
        self,
        source: bytes | BinaryIO,
        *,
        name: str,
        metadata: dict[str, Any] | None = None,
    ) -> DocumentInfo:
        """Import bounded source bytes or a caller-opened binary stream."""
        if PurePath(name).suffix.lower() not in {".txt", ".md"}:
            raise InvalidDocument(
                "Only .txt and .md source files are supported"
            )
        if isinstance(source, bytes):
            content = source
        elif hasattr(source, "read"):
            content = await run_sync(source.read)(
                self.runtime.settings.max_document_bytes + 1
            )
        else:
            raise InvalidDocument(
                "Import requires bytes or an opened binary source"
            )
        return await self._write(content, name=name, metadata=metadata)

    async def update(
        self,
        document_id: UUID | str,
        text: str,
        *,
        name: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> DocumentInfo:
        """Replace text and immediately invalidate previous search citations."""
        return await self._write(
            encode_text(text, self.runtime.settings),
            document_id=UUID(str(document_id)),
            name=name,
            metadata=metadata,
        )

    async def _write(
        self,
        content: bytes,
        *,
        name: str | None = None,
        document_id: UUID | None = None,
        metadata: dict | None = None,
    ) -> DocumentInfo:
        text = normalize(content, self.runtime.settings)
        values = (
            metadata_value(metadata, self.runtime.settings)
            if metadata is not None
            else None
        )
        if name is not None:
            name = display_name(name)
        old_path = None
        published = None
        async with self.runtime.access():
            try:
                async with self.runtime.database.transaction() as session:
                    base = await self.knowledge_base.locked(session)
                    if base.target_profile != self.runtime.profile:
                        raise IndexUnavailable(
                            "Rebuild for the current encoder before writing"
                        )
                    if document_id is None:
                        if name is None:
                            raise InvalidDocument(
                                "A document display name is required"
                            )
                        record = DocumentRecord(
                            id=uuid4(),
                            knowledge_base_id=base.id,
                            name=name,
                            metadata_=values or {},
                            index_epoch=base.index_epoch,
                        )
                        session.add(record)
                    else:
                        record = await self._record(session, document_id)
                        old_path = record.source_path
                        if name is not None:
                            record.name = name
                        if values is not None:
                            record.metadata_ = values
                    reset_index(record, base.index_epoch)
                    published = (
                        f"sources/{base.id.hex}/{record.id.hex}/"
                        f"{record.index_key.hex}.txt"
                    )
                    await self.runtime.write_source(published, content)
                    record.source_path = published
                    record.content_hash = hashlib.sha256(content).hexdigest()
                    record.text = text
                    record.media_type = (
                        "text/markdown"
                        if record.name.lower().endswith(".md")
                        else "text/plain"
                    )
                    await session.flush()
                    result = document_info(record)
            except BaseException:
                if published is not None:
                    with anyio.CancelScope(shield=True):
                        await self.runtime.discard_source(published)
                raise
            if old_path is not None:
                await self.runtime.remove_source(old_path)
            return result

    async def delete(self, document_id: UUID | str) -> None:
        """Remove visibility and claims before attempting file cleanup."""
        from .indexing import settle_base

        async with self.runtime.access():
            async with self.runtime.database.transaction() as session:
                base = await self.knowledge_base.locked(session)
                record = await self._record(session, document_id)
                path = record.source_path
                await session.execute(
                    sql_delete(DocumentRecord).where(
                        DocumentRecord.id == record.id
                    )
                )
                await settle_base(session, base)
            await self.runtime.remove_source(path)

    async def reindex(self, document_id: UUID | str) -> DocumentInfo:
        """Invalidate old claims and reset the document's retry allowance."""
        async with (
            self.runtime.access(),
            self.runtime.database.transaction() as session,
        ):
            base = await self.knowledge_base.locked(session)
            if base.target_profile != self.runtime.profile:
                raise IndexUnavailable(
                    "Rebuild the library before indexing with a changed encoder"
                )
            record = await self._record(session, document_id)
            reset_index(record, base.index_epoch)
            if base.index_status == "failed":
                base.index_status = "rebuilding"
            await session.flush()
            return document_info(record)

    async def read(
        self,
        document_id: UUID | str,
        *,
        start: int = 0,
        end: int | None = None,
        index_key: UUID | str | None = None,
    ) -> DocumentText:
        """Read bounded normalized text and optionally verify a citation key."""
        if not isinstance(start, int) or start < 0:
            raise ValueError("Read start must be nonnegative")
        if end is None:
            end = start + self.runtime.settings.max_read_chars
        if (
            not isinstance(end, int)
            or end < start
            or end - start > self.runtime.settings.max_read_chars
        ):
            raise ValueError("Read range exceeds configured bounds")
        async with (
            self.runtime.access(),
            self.runtime.database.session() as session,
        ):
            await self.knowledge_base.fetch(session)
            record = await self._record(session, document_id)
            if index_key is not None and (
                record.index_key != UUID(str(index_key))
                or record.status != "ready"
            ):
                raise StaleReference(
                    "The cited document index is no longer current"
                )
            if start > len(record.text):
                raise ValueError("Read start exceeds document length")
            end = min(end, len(record.text))
            return DocumentText(
                document_id=record.id,
                name=record.name,
                text=record.text[start:end],
                start_offset=start,
                end_offset=end,
                total_chars=len(record.text),
                index_key=record.index_key,
            )
