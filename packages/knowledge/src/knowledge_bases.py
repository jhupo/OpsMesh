"""Owned knowledge-base handles and whole-library rebuild coordination."""

from __future__ import annotations

import builtins
import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import delete as sql_delete
from sqlalchemy import select

from opsmesh.plugins.registry import get_current_plugin

from .errors import InvalidDocument, KnowledgeNotFound
from .models import DocumentRecord, KnowledgeBaseRecord, now
from .schemas import KnowledgeBaseInfo

MAX_DESCRIPTION_CHARS = 16_000


def metadata_value(value: dict[str, Any] | None, settings) -> dict[str, Any]:
    """Require a bounded JSON object before any file or database write."""
    value = {} if value is None else value
    try:
        if not isinstance(value, dict) or not all(
            isinstance(key, str) for key in value
        ):
            raise ValueError("Metadata must be a JSON object")
        encoded = json.dumps(value, allow_nan=False, ensure_ascii=True)
        if len(encoded.encode()) > settings.max_metadata_bytes:
            raise ValueError("Metadata is too large")
        return json.loads(encoded)
    except (ValueError, TypeError, OverflowError) as error:
        raise InvalidDocument(
            "Metadata must be a bounded JSON object"
        ) from error


def display_name(name: str) -> str:
    """Validate display-only text without interpreting it as a path."""
    if (
        not isinstance(name, str)
        or not name.strip()
        or len(name) > 200
        or "\x00" in name
    ):
        raise InvalidDocument(
            "A display name of 1 to 200 characters is required"
        )
    return name.strip()


def page_bounds(limit: int | None, offset: int, settings) -> int:
    """Resolve and validate bounded pagination before opening a session."""
    if limit is None:
        limit = min(50, settings.max_page_size)
    if not isinstance(limit, int) or not 1 <= limit <= settings.max_page_size:
        raise ValueError("Page size is outside configured bounds")
    if not isinstance(offset, int) or offset < 0:
        raise ValueError("Offset must be nonnegative")
    return limit


def caller_owner() -> str:
    """Take ownership from the framework's trusted plugin scope."""
    caller = get_current_plugin()
    if caller is None:
        raise KnowledgeNotFound(
            "Knowledge operations require a calling plugin scope"
        )
    return caller.id_


def base_info(record: KnowledgeBaseRecord, profile: dict) -> KnowledgeBaseInfo:
    """Detach metadata and report incompatibility with the active encoder."""
    values = {
        name: getattr(record, name)
        for name in KnowledgeBaseInfo.model_fields
        if name not in {"metadata", "needs_rebuild"}
    }
    return KnowledgeBaseInfo(
        **values,
        metadata=record.metadata_,
        needs_rebuild=record.active_profile != profile,
    )


@dataclass(frozen=True)
class KnowledgeBase:
    """A capability bound to one library and its original owner scope."""

    runtime: Any
    id: UUID
    owner_plugin: str

    def statement(self):
        """Restrict every handle lookup to its fixed owner and library ID."""
        return select(KnowledgeBaseRecord).where(
            KnowledgeBaseRecord.id == self.id,
            KnowledgeBaseRecord.owner_plugin == self.owner_plugin,
        )

    async def fetch(self, session) -> KnowledgeBaseRecord:
        """Read an existing owned library in the supplied short session."""
        record = await session.scalar(self.statement())
        if record is None:
            raise KnowledgeNotFound("Knowledge base was not found")
        return record

    async def locked(self, session) -> KnowledgeBaseRecord:
        """Serialize mutations before touching any library documents."""
        record = await session.scalar(self.statement().with_for_update())
        if record is None:
            raise KnowledgeNotFound("Knowledge base was not found")
        return record

    @property
    def documents(self):
        """Return the document collection bound to this library."""
        from .documents import Documents

        return Documents(self)

    @property
    def tools(self):
        """Return read-only native tools with unique library-ID names."""
        from .tools import read_tools

        return read_tools(self, f"kb_{self.id.hex}")

    @property
    def management_tools(self):
        """Return separate native tools that require SDK approval."""
        from .tools import management_tools

        return management_tools(self, f"kb_{self.id.hex}")

    def tools_with_alias(self, alias: str):
        """Bind descriptive names when an Agent uses several libraries."""
        from .tools import read_tools

        return read_tools(self, alias)

    async def info(self) -> KnowledgeBaseInfo:
        """Read detached metadata and the current rebuild state."""
        async with (
            self.runtime.access(),
            self.runtime.database.session() as session,
        ):
            return base_info(await self.fetch(session), self.runtime.profile)

    async def update(
        self,
        name: str | None = None,
        *,
        description: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> KnowledgeBaseInfo:
        """Modify descriptive metadata without changing document indexes."""
        async with (
            self.runtime.access(),
            self.runtime.database.transaction() as session,
        ):
            record = await self.locked(session)
            if name is not None:
                record.name = display_name(name)
            if description is not None:
                if (
                    len(description) > MAX_DESCRIPTION_CHARS
                    or "\x00" in description
                ):
                    raise InvalidDocument(
                        "Description exceeds configured bounds"
                    )
                record.description = description
            if metadata is not None:
                record.metadata_ = metadata_value(
                    metadata, self.runtime.settings
                )
            record.updated_at = now()
            await session.flush()
            return base_info(record, self.runtime.profile)

    async def delete(self) -> None:
        """Remove SQL visibility first, then clean the original source files."""
        async with self.runtime.access():
            async with self.runtime.database.transaction() as session:
                await self.locked(session)
                paths = builtins.list(
                    await session.scalars(
                        select(DocumentRecord.source_path).where(
                            DocumentRecord.knowledge_base_id == self.id
                        )
                    )
                )
                await session.execute(
                    sql_delete(KnowledgeBaseRecord).where(
                        KnowledgeBaseRecord.id == self.id
                    )
                )
            for path in paths:
                await self.runtime.remove_source(path)

    async def reindex(self) -> KnowledgeBaseInfo:
        """Rebuild or retry failed documents in the current target epoch."""
        from .documents import reset_index
        from .indexing import settle_base

        async with (
            self.runtime.access(),
            self.runtime.database.transaction() as session,
        ):
            record = await self.locked(session)
            profile = self.runtime.profile
            if (
                record.index_status == "rebuilding"
                and record.target_profile == profile
            ):
                return base_info(record, profile)
            retry = (
                record.index_status == "failed"
                and record.target_profile == profile
            )
            if not retry:
                record.index_epoch = uuid4()
            record.target_profile = profile
            record.index_status = "rebuilding"
            documents = await session.scalars(
                select(DocumentRecord)
                .where(DocumentRecord.knowledge_base_id == self.id)
                .with_for_update()
            )
            for document in documents:
                if not retry or document.status == "failed":
                    reset_index(document, record.index_epoch)
            record.updated_at = now()
            await session.flush()
            await settle_base(session, record)
            return base_info(record, profile)

    async def search(self, query: str, *, limit: int | None = None):
        """Find bounded source excerpts in the current compatible index."""
        from .search import search

        return await search(self, query, limit=limit)


async def create(
    name: str, *, description: str = "", metadata: dict[str, Any] | None = None
) -> KnowledgeBase:
    """Create an empty library owned by the trusted calling plugin."""
    from .lifecycle import get_runtime

    owner = caller_owner()
    runtime = get_runtime()
    if (
        len(description) > MAX_DESCRIPTION_CHARS
        or "\x00" in description
    ):
        raise InvalidDocument("Description exceeds configured bounds")
    async with runtime.access(), runtime.database.transaction() as session:
        record = KnowledgeBaseRecord(
            id=uuid4(),
            owner_plugin=owner,
            name=display_name(name),
            description=description,
            metadata_=metadata_value(metadata, runtime.settings),
            active_profile=runtime.profile,
            target_profile=runtime.profile,
            index_epoch=uuid4(),
            index_status="ready",
        )
        session.add(record)
        await session.flush()
        return KnowledgeBase(runtime, record.id, owner)


async def get(knowledge_base_id: UUID | str) -> KnowledgeBase:
    """Return an existing owned library; never implicitly create one."""
    from .lifecycle import get_runtime

    handle = KnowledgeBase(
        get_runtime(), UUID(str(knowledge_base_id)), caller_owner()
    )
    await handle.info()
    return handle


async def list(
    *, limit: int | None = None, offset: int = 0
) -> builtins.list[KnowledgeBaseInfo]:
    """List only the calling plugin's libraries with bounded pagination."""
    from .lifecycle import get_runtime

    owner = caller_owner()
    runtime = get_runtime()
    limit = page_bounds(limit, offset, runtime.settings)
    async with runtime.access(), runtime.database.session() as session:
        records = await session.scalars(
            select(KnowledgeBaseRecord)
            .where(KnowledgeBaseRecord.owner_plugin == owner)
            .order_by(KnowledgeBaseRecord.created_at, KnowledgeBaseRecord.id)
            .limit(limit)
            .offset(offset)
        )
        return [base_info(record, runtime.profile) for record in records]
