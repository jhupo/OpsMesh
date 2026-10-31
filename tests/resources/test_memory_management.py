"""Namespace policy changes are owner-only and take effect for future reads and writes."""

import pytest

from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.database import AsyncDatabase
from opsmesh.modules.memory.api import (
    MemoryCandidate,
    MemoryNamespace,
    MemoryNamespaceSettings,
    MemoryPolicy,
)
from opsmesh.modules.memory.internal.service import PersistentMemoryService
from opsmesh.modules.memory.management import MemoryManagement
from opsmesh.modules.workspaces.internal.service import LocalWorkspaceService

from .test_memory_lifecycle import consolidator, extractor
from .test_resource_management import Journal


async def test_memory_access_revocation_and_stale_config_update(tmp_path):
    database = AsyncDatabase(f"sqlite+aiosqlite:///{tmp_path / 'state.db'}")
    workspaces = LocalWorkspaceService(database, tmp_path / "data")
    memory = PersistentMemoryService(database, workspaces, extractor, consolidator)

    async def validate(actor):
        if actor == "disabled":
            raise OpsMeshError("AUTHORIZATION_DENIED", "Disabled")

    management = MemoryManagement(memory, Journal(), validate_actor=validate)
    await database.initialize()
    try:
        await management.create_namespace(
            MemoryNamespace(
                id="project",
                owner="alice",
                readers=("bob",),
                contributors=("editor",),
                policy=MemoryPolicy(live_update=True),
            ),
            actor_id="alice",
        )
        assert len(await management.list_namespaces(actor_id="bob")) == 1
        with pytest.raises(OpsMeshError):
            await management.configure_namespace(
                "project", MemoryNamespaceSettings(), actor_id="editor", expected_revision=1
            )
        await management.configure_namespace(
            "project", MemoryNamespaceSettings(), actor_id="alice", expected_revision=1
        )
        assert await management.list_namespaces(actor_id="bob") == ()
        with pytest.raises(OpsMeshError):
            await memory.read("project", principal_id="bob")
        with pytest.raises(OpsMeshError):
            await management.configure_namespace(
                "project",
                MemoryNamespaceSettings(readers=("bob",)),
                actor_id="alice",
                expected_revision=1,
            )
        with pytest.raises(OpsMeshError):
            await memory.update(
                "project",
                MemoryCandidate(summary="summary", index="index"),
                principal_id="editor",
                expected_revision=2,
                idempotency_key="update",
            )
        assert (
            await memory.get_namespace("project", principal_id="alice")
        ).policy.live_update is False
    finally:
        await workspaces.close()
        await database.close()
