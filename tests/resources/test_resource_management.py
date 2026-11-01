"""Governance changes preserve immutable versions and revoke execution immediately."""

from datetime import UTC, datetime

import pytest

from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.database import AsyncDatabase
from opsmesh.modules.audit.api import AuditRecord
from opsmesh.modules.capabilities.api import MCPServerDefinition, ScriptToolSource, ToolDefinition
from opsmesh.modules.capabilities.internal.catalog import PersistentCapabilityCatalog
from opsmesh.modules.capabilities.internal.mcp import MCPServerConnector, MCPServerRegistry
from opsmesh.modules.capabilities.internal.scripts import PersistentScriptTools
from opsmesh.modules.capabilities.internal.skills import PersistentSkillCatalog
from opsmesh.modules.execution.api import ExecutionSpec
from opsmesh.modules.execution.internal.service import LocalExecutionProvider
from opsmesh.modules.models.api import ModelProfile
from opsmesh.modules.models.internal.service import ModelProfileCatalog
from opsmesh.modules.models.management import ModelManagement
from opsmesh.modules.workspaces.internal.service import LocalWorkspaceService


class Journal:
    def __init__(self):
        self.records = []

    async def record(
        self, action, resource, *, actor_id=None, outcome="succeeded", details=None, event_id=None
    ):
        value = AuditRecord(
            id=event_id or "audit",
            actor_id=actor_id,
            action=action,
            resource=resource,
            outcome=outcome,
            occurred_at=datetime.now(UTC),
            details=details or {},
        )
        self.records.append(value)
        return value


async def authorize(actor, action, resource):
    if actor != "owner":
        raise OpsMeshError("AUTHORIZATION_DENIED", "Denied")


async def references(actor, kind, reference):
    return ()


async def test_model_revocation_preserves_versions_and_is_audited(tmp_path):
    database = AsyncDatabase(f"sqlite+aiosqlite:///{tmp_path / 'state.db'}")
    catalog = ModelProfileCatalog(database)
    journal = Journal()
    service = ModelManagement(catalog, authorize, journal, references=references)
    await database.initialize()
    try:
        profile = ModelProfile(id="default", model="offline")
        with pytest.raises(OpsMeshError):
            await service.create(profile, actor_id="outsider")
        assert await catalog.list() == []
        await service.create(profile, actor_id="owner")
        await service.set_enabled("default@1", False, actor_id="owner", expected_revision=0)
        with pytest.raises(OpsMeshError, match="disabled"):
            await catalog.get("default")
        assert await service.get("default@1", actor_id="owner") == profile
        new = await service.update(
            profile.model_copy(update={"model": "replacement"}), 1, actor_id="owner"
        )
        assert new.revision == 2
        assert (await catalog.get("default")).model == "replacement"
        assert len(await service.versions("default", actor_id="owner")) == 2
        with pytest.raises(OpsMeshError, match="disabled"):
            await catalog.get("default@1")
        with pytest.raises(OpsMeshError):
            await service.set_enabled("default@1", True, actor_id="owner", expected_revision=0)
        assert any(
            record.action == "model.availability" and record.outcome == "succeeded"
            for record in journal.records
        )
    finally:
        await database.close()


async def test_tool_disable_does_not_mutate_definition(tmp_path):
    database = AsyncDatabase(f"sqlite+aiosqlite:///{tmp_path / 'state.db'}")
    catalog = PersistentCapabilityCatalog(database)
    await database.initialize()
    try:
        definition = ToolDefinition(
            name="inspect", version="1", code_ref="inspect", input_schema={"type": "object"}
        )
        await catalog.register_tool(definition)
        await catalog.availability.set_enabled(
            definition.ref, False, actor_id="owner", expected_revision=0
        )
        with pytest.raises(OpsMeshError, match="disabled"):
            await catalog.get_tool(definition.ref)
        assert await catalog.get_tool(definition.ref, include_disabled=True) == definition
        assert await catalog.register_tool(definition) == definition
        with pytest.raises(OpsMeshError, match="disabled"):
            await catalog.get_tool(definition.ref)
    finally:
        await database.close()


async def test_mcp_rechecks_disabled_cached_definition_before_transport(tmp_path):
    database = AsyncDatabase(f"sqlite+aiosqlite:///{tmp_path / 'state.db'}")
    registry = MCPServerRegistry(database)

    async def secret(reference):
        raise AssertionError("disabled MCP cannot resolve secrets")

    async def allow(actor, resource):
        return None

    connector = MCPServerConnector(secret, allow, registry=registry)
    await database.initialize()
    try:
        definition = MCPServerDefinition(
            name="server",
            version="1",
            transport="streamable_http",
            execution_location="remote",
            url="http://127.0.0.1:1/mcp",
            allowed_tools=("inspect",),
        )
        await registry.register(definition)
        await registry.availability.set_enabled(
            "server@1", False, actor_id="owner", expected_revision=0
        )
        with pytest.raises(OpsMeshError, match="disabled"):
            await connector.call(definition, "inspect", {}, principal_id="owner")
    finally:
        await database.close()


async def test_skill_and_script_revocation_preserves_bundles_and_blocks_execution(tmp_path):
    database = AsyncDatabase(f"sqlite+aiosqlite:///{tmp_path / 'state.db'}")
    workspaces = LocalWorkspaceService(database, tmp_path / "data")
    catalog = PersistentCapabilityCatalog(database)
    skills = PersistentSkillCatalog(database, workspaces)

    async def allow(actor, resource):
        return None

    scripts = PersistentScriptTools(
        database,
        workspaces,
        catalog,
        allow,
        execution_provider=LocalExecutionProvider(),
        execution_template=ExecutionSpec(workspace=tmp_path, trusted_local=True),
    )
    source = tmp_path / "source"
    source.mkdir()
    (source / "SKILL.md").write_text("# Skill\nInspect files", encoding="utf-8")
    (source / "main.py").write_text("raise RuntimeError('must never execute')", encoding="utf-8")
    await database.initialize()
    try:
        await workspaces.create("target")
        skill = await skills.publish("inspect", "1", source)
        script = await scripts.publish(
            source,
            ScriptToolSource(
                name="inspect_script",
                version="1",
                entrypoint="main.py",
                input_schema={"type": "object"},
            ),
        )
        await skills.availability.set_enabled(
            skill.ref, False, actor_id="owner", expected_revision=0
        )
        await scripts.availability.set_enabled(
            script.ref, False, actor_id="owner", expected_revision=0
        )
        with pytest.raises(OpsMeshError, match="disabled"):
            await skills.load(skill.ref, "target")
        with pytest.raises(OpsMeshError, match="disabled"):
            await scripts.execute(script.ref, {}, principal_id="owner")
        assert (await skills.list())[0].digest == skill.digest
        assert (await scripts.list())[0].digest == script.digest
        assert await workspaces.tree("target") == ()
    finally:
        await workspaces.close()
        await database.close()
