"""Knowledge-owned model, filesystem space, and indexing worker resources."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import select, text

from opsmesh import on_shutdown, on_started, on_startup, on_stopping
from opsmesh.plugins import require
from opsmesh.plugins.config import get_plugin_config
from opsmesh.plugins.invocation import plugin_call
from opsmesh.plugins.registry import get_current_plugin, plugin_scope
from opsmesh.utils import run_sync

from .config import Settings
from .embeddings import Encoder, load_model
from .errors import KnowledgeClosed, ModelConfigurationError
from .models import DocumentRecord, KnowledgeBaseRecord

logger = logging.getLogger(__name__)


class KnowledgeRuntime:
    """Own shared resources without keeping database sessions open."""

    def __init__(self, settings: Settings, owner: Any):
        """Bind configuration and ownership before acquiring resources."""
        self.settings = settings
        self.owner = owner
        self.database = None
        self.filesystem = None
        self.data: Path | None = None
        self.encoder: Encoder | None = None
        self.accepting = False
        self.worker = None
        self.worker_task: asyncio.Task | None = None

    @property
    def profile(self) -> dict[str, Any]:
        """Return the complete identity of the currently loaded encoder."""
        return self.encoder.profile.persisted()

    @asynccontextmanager
    async def access(self):
        """Retain knowledge and its dependencies for the entire public call."""
        if not self.accepting:
            raise KnowledgeClosed("The knowledge plugin is not accepting work")
        async with plugin_call(self.owner.id_):
            yield self

    async def startup(self) -> None:
        """Capture knowledge file space and verify model and database state."""
        database = require("database")
        self.filesystem = require("filesystem")
        self.database = database.get_database()
        with plugin_scope(self.owner):
            self.data = self.filesystem.data_dir()
            cache = self.filesystem.cache_dir()
        model_path = (
            self.settings.model_path
            or cache / "models" / self.settings.model_revision
        )
        model, profile = await run_sync(load_model)(model_path, self.settings)
        self.encoder = Encoder(model, profile, self.settings)
        async with self.database.session() as session:
            version = await session.scalar(
                text(
                    "SELECT extversion FROM pg_extension "
                    "WHERE extname = 'vector'"
                )
            )
            if version is None:
                raise ModelConfigurationError(
                    "Install PostgreSQL's vector extension before migration"
                )
            if self.settings.search_mode == "hnsw" and tuple(
                int(part) for part in version.split(".")[:2]
            ) < (0, 8):
                raise ModelConfigurationError(
                    "HNSW iterative scans require pgvector 0.8 or newer"
                )
            incompatible = await session.scalar(
                select(KnowledgeBaseRecord.id)
                .where(KnowledgeBaseRecord.active_profile != self.profile)
                .limit(1)
            )
            if incompatible is not None:
                logger.warning(
                    "Existing indexes need a rebuild for the current profile"
                )
            missing_target = await session.scalar(
                select(KnowledgeBaseRecord.id)
                .where(
                    KnowledgeBaseRecord.index_status.in_(
                        ("rebuilding", "failed")
                    ),
                    KnowledgeBaseRecord.target_profile != self.profile,
                )
                .limit(1)
            )
            if missing_target is not None:
                raise ModelConfigurationError(
                    "An unfinished rebuild requires its original target encoder"
                )
        await self.clean_orphans()
        self.accepting = True

    async def write_source(self, relative: str, content: bytes) -> None:
        """Publish a unique original file under knowledge's captured owner."""
        with plugin_scope(self.owner):
            await self.filesystem.write_file(
                self.data / relative, content, replace=False
            )

    async def remove_source(self, relative: str) -> None:
        """Deletion is best effort after SQL visibility has already changed."""
        try:
            with plugin_scope(self.owner):
                await self.filesystem.delete(self.data / relative)
        except Exception:
            logger.warning(
                "Knowledge source cleanup deferred to orphan maintenance",
                exc_info=True,
            )

    async def discard_source(self, relative: str) -> None:
        """Avoid deleting committed bytes when a commit outcome is uncertain."""
        try:
            async with self.database.session() as session:
                reference = await session.scalar(
                    select(DocumentRecord.id)
                    .where(DocumentRecord.source_path == relative)
                    .limit(1)
                )
            if reference is None:
                await self.remove_source(relative)
        except Exception:
            logger.warning("Unconfirmed source cleanup deferred to maintenance")

    async def clean_orphans(self) -> None:
        """Clean only old unreferenced files in the captured knowledge space."""
        import time

        async with self.database.session() as session:
            referenced = set(
                await session.scalars(select(DocumentRecord.source_path))
            )
        cutoff = time.time() - self.settings.orphan_grace_seconds

        def candidates():
            result = []
            for root in (self.data / "sources", self.data / "staging"):
                if root.exists() and not root.is_symlink():
                    for path in root.rglob("*"):
                        if (
                            path.is_file()
                            and not path.is_symlink()
                            and path.resolve().is_relative_to(
                                self.data.resolve()
                            )
                        ):
                            relative = path.relative_to(self.data).as_posix()
                            if (
                                relative not in referenced
                                and path.stat().st_mtime < cutoff
                            ):
                                result.append(relative)
            return result

        for relative in await run_sync(candidates)():
            await self.remove_source(relative)

    async def start_worker(self) -> None:
        """Start claims only after all plugin resources become ready."""
        from .indexing import IndexWorker

        self.worker = IndexWorker(self)
        self.worker_task = asyncio.create_task(
            self.worker.run(), name="knowledge-indexer"
        )

    async def stop(self) -> None:
        """Close admission and wait for the current indexing claim to finish."""
        self.accepting = False
        if self.worker is not None:
            self.worker.stopping = True
            self.worker.wake.set()
        if self.worker_task is not None:
            task = self.worker_task
            try:
                await asyncio.shield(task)
            finally:
                if task.done():
                    self.worker_task = None

    async def shutdown(self) -> None:
        """Release the model only after worker and encoding completion."""
        await self.stop()
        if self.encoder is not None:
            await self.encoder.shutdown()
            self.encoder = None


_runtime: KnowledgeRuntime | None = None


def get_runtime() -> KnowledgeRuntime:
    """Return only an activation that still accepts independent calls."""
    if _runtime is None or not _runtime.accepting:
        raise KnowledgeClosed("The knowledge plugin is not running")
    return _runtime


@on_startup
async def startup() -> None:
    """Retain partial startup resources so framework cleanup can retry."""
    global _runtime
    if _runtime is not None:
        raise KnowledgeClosed("Previous knowledge resources still need cleanup")
    owner = get_current_plugin()
    if owner is None:
        raise KnowledgeClosed("Knowledge must start in its own plugin scope")
    runtime = KnowledgeRuntime(get_plugin_config(Settings), owner)
    _runtime = runtime
    await runtime.startup()


@on_started
async def started() -> None:
    """Activate the background worker after the readiness barrier."""
    await get_runtime().start_worker()


@on_stopping
async def stopping() -> None:
    """Stop new claims before the framework drains accepted calls."""
    if _runtime is not None:
        await _runtime.stop()


@on_shutdown
async def shutdown() -> None:
    """Forget the activation only after its resources finish closing."""
    global _runtime
    if _runtime is not None:
        await _runtime.shutdown()
        _runtime = None
