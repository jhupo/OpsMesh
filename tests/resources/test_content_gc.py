"""Physical GC is conservative, fenced, and explicitly requires maintenance."""

import asyncio
import hashlib
import os
import time

import pytest

from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.database import AsyncDatabase
from opsmesh.modules.workspaces.internal.service import LocalWorkspaceService


async def age_blob(service, digest, timestamp):
    key = hashlib.sha256(f"{service._content_locks.scope}:{digest}".encode()).hexdigest()
    record = await service._content_locks.store.get(key)
    assert record is not None
    await service._content_locks.store.update(
        key, {**record.data, "last_content_use": timestamp}, record.revision
    )


@pytest.mark.asyncio
async def test_content_collection_retains_references_and_requires_maintenance(tmp_path):
    database = AsyncDatabase(f"sqlite+aiosqlite:///{tmp_path / 'gc.db'}")
    root = tmp_path / "content"
    service = LocalWorkspaceService(database, root)
    await database.initialize()
    try:
        referenced = await service.put_blob(b"shared content")
        orphan = await service.put_blob(b"orphan")
        recent = await service.put_blob(b"recent")
        for digest in (referenced, orphan):
            os.utime(root / "objects" / digest, (time.time() - 7200,) * 2)
            await age_blob(service, digest, time.time() - 7200)

        async def roots():
            return {referenced}

        preview = await service.collect_garbage(roots)
        assert preview.candidates == (orphan,) and preview.deleted == ()
        with pytest.raises(OpsMeshError) as error:
            await service.collect_garbage(roots, dry_run=False)
        assert error.value.code == "MAINTENANCE_REQUIRED"
        result = await service.collect_garbage(roots, dry_run=False, maintenance=True)
        assert result.deleted == (orphan,) and result.reclaimed_bytes == len(b"orphan")
        assert await service.get_blob(referenced) == b"shared content"
        assert await service.get_blob(recent) == b"recent"
        with pytest.raises(OpsMeshError):
            await service.get_blob(orphan)
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_second_reference_scan_and_cross_instance_gc(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'gc.db'}"
    first_db, second_db = AsyncDatabase(url), AsyncDatabase(url)
    root = tmp_path / "content"
    first, second = LocalWorkspaceService(first_db, root), LocalWorkspaceService(second_db, root)
    await first_db.initialize()
    await second_db.initialize()
    try:
        digest = await first.put_blob(b"newly referenced")
        os.utime(root / "objects" / digest, (time.time() - 7200,) * 2)
        await age_blob(first, digest, time.time() - 7200)
        calls = 0

        async def changing_roots():
            nonlocal calls
            calls += 1
            return set() if calls == 1 else {digest}

        result = await first.collect_garbage(changing_roots, dry_run=False, maintenance=True)
        assert result.deleted == ()

        async def empty():
            return set()

        results = await asyncio.gather(
            *[
                service.collect_garbage(empty, dry_run=False, maintenance=True)
                for service in (first, second)
            ]
        )
        assert sum(len(item.deleted) for item in results) == 1
    finally:
        await first_db.close()
        await second_db.close()


@pytest.mark.asyncio
async def test_recent_blob_reuse_has_grace_period_before_reference_commit(tmp_path):
    database = AsyncDatabase(f"sqlite+aiosqlite:///{tmp_path / 'gc-reuse.db'}")
    root = tmp_path / "content"
    service = LocalWorkspaceService(database, root)
    await database.initialize()
    try:
        data = b"an older immutable object reused for a new pending reference"
        digest = await service.put_blob(data)
        os.utime(root / "objects" / digest, (time.time() - 7200,) * 2)
        assert await service.put_blob(data) == digest

        async def uncommitted_reference():
            return set()

        collected = await service.collect_garbage(
            uncommitted_reference,
            minimum_age_seconds=3600,
            dry_run=False,
            maintenance=True,
        )
        assert digest not in collected.deleted
        assert await service.get_blob(digest) == data
    finally:
        await database.close()
