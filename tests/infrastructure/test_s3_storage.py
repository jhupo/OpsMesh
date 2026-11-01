import asyncio
import hashlib
import threading
from datetime import UTC, datetime, timedelta

import pytest

from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.database import AsyncDatabase
from opsmesh.infrastructure.storage.backup import export_blobs, restore_blobs
from opsmesh.infrastructure.storage.s3 import S3BlobStore
from opsmesh.modules.workspaces.internal.service import LocalWorkspaceService

from .s3_protocol import S3ProtocolServer


@pytest.fixture
def s3_peer():
    pytest.importorskip("boto3")
    server = S3ProtocolServer()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def make_store(server, *, prefix="objects"):
    return S3BlobStore(
        "test-bucket",
        endpoint_url=f"http://127.0.0.1:{server.server_port}",
        region_name="us-east-1",
        prefix=prefix,
        access_key_id="test-only",
        secret_access_key="local-protocol-fixture",
        request_timeout_seconds=2,
    )


async def test_real_s3_http_conditional_create_pagination_and_delete(s3_peer):
    first, second = make_store(s3_peer), make_store(s3_peer)
    try:
        payload = b"wire payload"
        results = await asyncio.gather(*(store.put(payload) for store in (first, second)))
        assert results == [hashlib.sha256(payload).hexdigest()] * 2
        assert await second.get(results[0]) == payload
        info = await first.stat(results[0])
        await first.put(b"other")
        page = await first.list(limit=1)
        assert len(page.items) == 1 and page.continuation
        last = await first.list(limit=1, continuation=page.continuation)
        assert len(last.items) == 1 and last.continuation is None
        with pytest.raises(OpsMeshError) as stale:
            await first.delete(info.digest, expected_version='"stale"')
        assert stale.value.code == "CONFLICT"
        assert await first.delete(info.digest, expected_version=info.version)
        assert not await second.delete(info.digest, expected_version=info.version)
        assert any(method == "DELETE" for method, _, _ in s3_peer.requests)
    finally:
        await first.close()
        await second.close()


async def test_s3_corrupt_existing_object_is_never_overwritten(s3_peer):
    store = make_store(s3_peer)
    digest = await store.put(b"intact")
    content, modified, etag = s3_peer.objects[f"objects/{digest}"]
    s3_peer.objects[f"objects/{digest}"] = (b"broken", modified, etag)
    try:
        with pytest.raises(OpsMeshError) as failed_read:
            await store.get(digest)
        assert failed_read.value.code == "CONTENT_CORRUPT"
        with pytest.raises(OpsMeshError) as failed_put:
            await store.put(content)
        assert failed_put.value.code == "CONTENT_CORRUPT"
        assert s3_peer.objects[f"objects/{digest}"][0] == b"broken"
    finally:
        await store.close()


async def test_cancelled_s3_request_keeps_blob_fence_until_thread_completes(s3_peer, tmp_path):
    url = f"sqlite:///{(tmp_path / 'fencing.db').as_posix()}"
    first_db, second_db = AsyncDatabase(url), AsyncDatabase(url)
    first_store, second_store = make_store(s3_peer), make_store(s3_peer)
    first = LocalWorkspaceService(first_db, tmp_path / "first", blob_store=first_store)
    second = LocalWorkspaceService(second_db, tmp_path / "second", blob_store=second_store)
    await first_db.initialize()
    await second_db.verify_schema()
    entered, release = threading.Event(), threading.Event()
    original = first_store._client.put_object

    def blocked_put(**kwargs):
        entered.set()
        assert release.wait(timeout=5)
        return original(**kwargs)

    first_store._client.put_object = blocked_put
    cancelled = asyncio.create_task(first.put_blob(b"same digest"))
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        cancelled.cancel()
        replacement = asyncio.create_task(second.put_blob(b"same digest"))
        await asyncio.sleep(0.05)
        assert not replacement.done() and not cancelled.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await cancelled
        digest = await replacement
        assert await second.get_blob(digest) == b"same digest"
    finally:
        release.set()
        await asyncio.gather(cancelled, return_exceptions=True)
        await first.close()
        await second.close()
        await first_db.close()
        await second_db.close()


async def test_s3_workspace_snapshot_backup_restore_and_reference_gc(s3_peer, tmp_path):
    url = f"sqlite:///{(tmp_path / 'metadata.db').as_posix()}"
    first_db, second_db = AsyncDatabase(url), AsyncDatabase(url)
    first_store, second_store = make_store(s3_peer), make_store(s3_peer)
    first = LocalWorkspaceService(first_db, tmp_path / "active-a", blob_store=first_store)
    second = LocalWorkspaceService(second_db, tmp_path / "active-b", blob_store=second_store)
    await first_db.initialize()
    await second_db.verify_schema()
    try:
        await first.create("project")
        artifact = await first.write("project", "report.txt", b"preserved", read_only=True)
        snapshot = await first.snapshot("project")
        assert (await first.root_path("project")).is_dir()
        assert not (tmp_path / "active-a" / "objects").exists()
        await second.restore(snapshot.id, "restored")
        assert await second.read("restored", "report.txt") == b"preserved"
        orphan = await first.put_blob(b"orphan")
        old = datetime.now(UTC) - timedelta(hours=2)
        for key, (data, _, etag) in tuple(s3_peer.objects.items()):
            s3_peer.objects[key] = (data, old, etag)
        lock_key = hashlib.sha256(f"{first._content_locks.scope}:{orphan}".encode()).hexdigest()
        lock_record = await first._content_locks.store.get(lock_key)
        assert lock_record is not None
        await first._content_locks.store.update(
            lock_key,
            {**lock_record.data, "last_content_use": old.timestamp()},
            lock_record.revision,
        )
        bundle = tmp_path / "backup"
        manifest = await export_blobs(first_store, bundle, maintenance=True)
        restored_store = make_store(s3_peer, prefix="recovery")
        try:
            await restore_blobs(
                restored_store,
                bundle,
                expected_manifest_digest=manifest.manifest_digest,
                maintenance=True,
            )
            assert await restored_store.get(artifact.digest) == b"preserved"
        finally:
            await restored_store.close()
        results = await asyncio.gather(
            *(
                service.collect_garbage(
                    db.referenced_content_digests, dry_run=False, maintenance=True
                )
                for service, db in ((first, first_db), (second, second_db))
            )
        )
        assert sum(result.deleted.count(orphan) for result in results) == 1
        assert await first.get_blob(artifact.digest) == b"preserved"
    finally:
        await first.close()
        await second.close()
        await first_db.close()
        await second_db.close()
