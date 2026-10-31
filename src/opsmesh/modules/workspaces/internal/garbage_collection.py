"""Reference-aware collection over any blob store, restricted to maintenance mode."""

import math
from datetime import UTC, datetime, timedelta
from functools import partial

from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.storage import BlobInfo, BlobStore
from opsmesh.modules.workspaces.api import ContentReferenceProvider, WorkspaceGarbageCollection

from .locking import WorkspaceLockManager


async def _collect_one(
    store: BlobStore, candidate: BlobInfo, cutoff: datetime, *, dry_run: bool
) -> int | None:
    try:
        current = await store.stat(candidate.digest)
    except OpsMeshError as error:
        if error.code == "NOT_FOUND":
            return None
        raise
    if current.modified_at >= cutoff or current.version != candidate.version:
        return None
    if dry_run or await store.delete(current.digest, expected_version=current.version):
        return current.size
    return None


async def collect_content(
    store: BlobStore,
    locks: WorkspaceLockManager,
    references: ContentReferenceProvider,
    *,
    minimum_age_seconds: float,
    dry_run: bool,
    maintenance: bool,
) -> WorkspaceGarbageCollection:
    if not math.isfinite(minimum_age_seconds) or minimum_age_seconds < 0:
        raise OpsMeshError("CONFIG_INVALID", "Content retention age must be finite and nonnegative")
    if not dry_run and not maintenance:
        raise OpsMeshError(
            "MAINTENANCE_REQUIRED", "Stop workers and write services before physical collection"
        )
    candidates: list[str] = []
    deleted: list[str] = []
    reclaimed = 0
    cutoff = datetime.now(UTC) - timedelta(seconds=minimum_age_seconds)
    async with locks.hold("content-garbage-collection"):
        roots = await references()
        token: str | None = None
        while True:
            page = await store.list(continuation=token)
            for info in page.items:
                if info.digest in roots or info.modified_at >= cutoff:
                    continue
                async with locks.hold(info.digest) as lease:
                    if info.digest in await references():
                        continue
                    grace_seconds = min(60, minimum_age_seconds)
                    if await lease.content_used_within(grace_seconds):
                        continue
                    size = await lease.run_async(
                        partial(_collect_one, store, info, cutoff, dry_run=dry_run)
                    )
                    if size is not None:
                        candidates.append(info.digest)
                        if not dry_run:
                            deleted.append(info.digest)
                            reclaimed += size
            if page.continuation is None:
                break
            if page.continuation == token:
                raise OpsMeshError("STORAGE_PROTOCOL_ERROR", "Blob listing did not advance")
            token = page.continuation
    return WorkspaceGarbageCollection(
        dry_run=dry_run,
        candidates=tuple(candidates),
        deleted=tuple(deleted),
        reclaimed_bytes=reclaimed,
    )
