"""Opt-in real S3/MinIO test; writes and cleans only a fresh random prefix."""

import asyncio
import os
from uuid import uuid4

import pytest

from opsmesh.infrastructure.database import AsyncDatabase
from opsmesh.infrastructure.storage.backup import export_blobs, restore_blobs
from opsmesh.infrastructure.storage.s3 import S3BlobStore
from opsmesh.modules.workspaces.internal.service import LocalWorkspaceService

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("OPSMESH_TEST_S3_INTEGRATION") != "1",
        reason="Set OPSMESH_TEST_S3_INTEGRATION=1 with an explicit test bucket",
    ),
]


def integration_store(prefix):
    return S3BlobStore(
        os.environ["OPSMESH_TEST_S3_BUCKET"],
        endpoint_url=os.getenv("OPSMESH_TEST_S3_ENDPOINT"),
        region_name=os.getenv("OPSMESH_TEST_S3_REGION", "us-east-1"),
        access_key_id=os.getenv("OPSMESH_TEST_S3_ACCESS_KEY"),
        secret_access_key=os.getenv("OPSMESH_TEST_S3_SECRET_KEY"),
        session_token=os.getenv("OPSMESH_TEST_S3_SESSION_TOKEN"),
        prefix=prefix,
    )


async def remove_test_prefix(store):
    token = None
    while True:
        page = await store.list(continuation=token)
        for item in page.items:
            await store.delete(item.digest, expected_version=item.version)
        if page.continuation is None:
            return
        token = page.continuation


async def test_real_s3_immutable_snapshot_backup_and_restore(tmp_path):
    pytest.importorskip("boto3")
    run_id = uuid4().hex
    prefix = f"opsmesh-integration/{run_id}"
    source, target = integration_store(prefix + "/source"), integration_store(prefix + "/restore")
    database = AsyncDatabase(
        os.getenv("OPSMESH_TEST_S3_DATABASE_URL", f"sqlite:///{tmp_path / 'metadata.db'}")
    )
    workspaces = LocalWorkspaceService(database, tmp_path / "active", blob_store=source)
    restored = LocalWorkspaceService(database, tmp_path / "recovery", blob_store=target)
    await database.initialize()
    try:
        await workspaces.create(f"project-{run_id}")
        artifact = await workspaces.write(f"project-{run_id}", "report.txt", b"real S3 round trip")
        snapshot = await workspaces.snapshot(f"project-{run_id}")
        digests = await asyncio.gather(*(source.put(b"concurrent immutable") for _ in range(5)))
        assert len(set(digests)) == 1
        manifest = await export_blobs(source, tmp_path / "backup", maintenance=True)
        await restore_blobs(
            target,
            tmp_path / "backup",
            expected_manifest_digest=manifest.manifest_digest,
            maintenance=True,
        )
        await restored.restore(snapshot.id, f"restored-{run_id}")
        assert await restored.read(f"restored-{run_id}", "report.txt") == b"real S3 round trip"
        assert await target.get(artifact.digest) == b"real S3 round trip"
        collection = await workspaces.collect_garbage(
            database.referenced_content_digests,
            minimum_age_seconds=0,
            dry_run=False,
            maintenance=True,
        )
        assert digests[0] in collection.deleted and artifact.digest not in collection.deleted
        await restored.delete(f"restored-{run_id}")
        await workspaces.delete(f"project-{run_id}")
    finally:
        try:
            await remove_test_prefix(source)
            await remove_test_prefix(target)
        finally:
            await workspaces.close()
            await restored.close()
            await database.close()
