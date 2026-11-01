import asyncio
import hashlib

import pytest

from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.storage import LocalBlobStore
from opsmesh.infrastructure.storage.backup import export_blobs, restore_blobs, verify_blob_backup


async def test_local_concurrent_create_is_immutable_and_paged(tmp_path):
    stores = [LocalBlobStore(tmp_path / "objects") for _ in range(2)]
    data = b"same complete payload" * 100
    digests = await asyncio.gather(*(stores[i % 2].put(data) for i in range(20)))
    assert len(set(digests)) == 1
    digest = digests[0]
    assert await stores[0].get(digest) == data
    before = await stores[0].stat(digest)
    assert await stores[1].put(data) == digest
    assert (await stores[0].stat(digest)).version == before.version
    await stores[0].put(b"second")
    page = await stores[0].list(limit=1)
    assert len(page.items) == 1 and page.continuation
    last = await stores[0].list(continuation=page.continuation, limit=1)
    assert len(last.items) == 1 and last.continuation is None
    assert last.items[0].digest != page.items[0].digest


async def test_local_corruption_cannot_be_overwritten_or_deleted_with_old_revision(tmp_path):
    store = LocalBlobStore(tmp_path / "objects")
    digest = await store.put(b"original")
    before = await store.stat(digest)
    (store.root / digest).write_bytes(b"corrupted")
    for operation in (store.get(digest), store.put(b"original")):
        with pytest.raises(OpsMeshError) as corrupt:
            await operation
        assert corrupt.value.code == "CONTENT_CORRUPT"
    with pytest.raises(OpsMeshError) as stale:
        await store.delete(digest, expected_version=before.version)
    assert stale.value.code == "CONFLICT"
    current = await store.stat(digest)
    assert await store.delete(digest, expected_version=current.version)
    assert not await store.delete(digest, expected_version=current.version)
    with pytest.raises(OpsMeshError, match="digest"):
        await store.get("../../secret")


async def test_local_size_limit_rejects_before_write_and_read(tmp_path):
    store = LocalBlobStore(tmp_path / "objects", max_blob_bytes=4)
    with pytest.raises(OpsMeshError) as oversized:
        await store.put(b"large")
    assert oversized.value.code == "RESOURCE_EXHAUSTED"
    digest = hashlib.sha256(b"large").hexdigest()
    (store.root / digest).write_bytes(b"large")
    with pytest.raises(OpsMeshError) as corrupt:
        await store.get(digest)
    assert corrupt.value.code == "CONTENT_CORRUPT"


async def test_backup_restore_validates_manifest_and_all_blobs_before_upload(tmp_path):
    source = LocalBlobStore(tmp_path / "source")
    expected = {await source.put(value): value for value in (b"one", b"two")}
    bundle = tmp_path / "backup"
    with pytest.raises(OpsMeshError) as denied:
        await export_blobs(source, bundle)
    assert denied.value.code == "MAINTENANCE_REQUIRED"
    manifest = await export_blobs(source, bundle, maintenance=True)
    assert {entry.digest for entry in manifest.objects} == set(expected)
    target = LocalBlobStore(tmp_path / "restored")
    for _ in range(2):
        await restore_blobs(
            target, bundle, expected_manifest_digest=manifest.manifest_digest, maintenance=True
        )
    assert {item.digest for item in (await target.list()).items} == set(expected)
    for digest, data in expected.items():
        assert await target.get(digest) == data
    (bundle / "objects" / manifest.objects[-1].digest).write_bytes(b"bad")
    empty_target = LocalBlobStore(tmp_path / "empty")
    with pytest.raises(OpsMeshError) as corrupt:
        await restore_blobs(
            empty_target,
            bundle,
            expected_manifest_digest=manifest.manifest_digest,
            maintenance=True,
        )
    assert corrupt.value.code == "CONTENT_CORRUPT"
    assert not (await empty_target.list()).items
    with pytest.raises(OpsMeshError, match="manifest digest"):
        await verify_blob_backup(bundle, expected_manifest_digest="0" * 64)


async def test_backup_missing_referenced_blob_never_creates_completion_manifest(tmp_path):
    store = LocalBlobStore(tmp_path / "source")
    bundle = tmp_path / "incomplete"
    with pytest.raises(OpsMeshError) as missing:
        await export_blobs(store, bundle, digests={"0" * 64}, maintenance=True)
    assert missing.value.code == "NOT_FOUND"
    assert not (bundle / "manifest.json").exists()
