"""Application publication upgrades real platform resources without replacing learned data."""

import asyncio
import hashlib
from dataclasses import replace

import pytest

from opsmesh.application.deployment import (
    ApplicationDefinition,
    DeploymentServices,
    OpsMeshApplicationDeployment,
    SkillSourceDefinition,
    WorkspaceDefinition,
)
from opsmesh.bootstrap.application import OpsMeshApplication
from opsmesh.bootstrap.settings import OpsMeshSettings
from opsmesh.framework.agents import AgentSpecification, SandboxConfiguration, ToolBinding
from opsmesh.framework.errors import OpsMeshError
from opsmesh.modules.accounts.api import AccessRole, PermissionGrant
from opsmesh.modules.bots.api import BotAccount, BotRoute
from opsmesh.modules.capabilities.api import ToolDefinition
from opsmesh.modules.models.api import ModelProfile


@pytest.fixture
async def deployment(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_APP_SECRET", "test-credential-not-for-network-use")
    app = OpsMeshApplication(
        OpsMeshSettings(
            data_directory=tmp_path / "data", tracing_enabled=False, worker_enabled=False
        )
    )
    await app.initialize(admin_username="owner", admin_password="deployment-password-123")
    owner = (await app.services.accounts.list_users())[0]
    service = app.services
    await service.account_management.create_role(AccessRole(name="assistant"), actor_id=owner.id)
    principal = await service.account_management.create_user(
        "assistant", "service-password-123", roles=("assistant",), actor_id=owner.id
    )

    async def inspect(arguments):
        return {"ok": True}

    service.code_tools.register("application:inspect@1", inspect)
    definition = ToolDefinition(
        name="inspect",
        version="1",
        code_ref="application:inspect@1",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
    )
    workspace = tmp_path / "source"
    (workspace / "skills" / "inspect").mkdir(parents=True)
    (workspace / "tools" / "__pycache__").mkdir(parents=True)
    (workspace / "AGENTS.md").write_text("You are {{name}}.", encoding="utf-8")
    (workspace / "skills" / "inspect" / "SKILL.md").write_text(
        "---\nname: inspect\ndescription: Inspect public information.\n---\nBe accurate.",
        encoding="utf-8",
    )
    (workspace / "tools" / "inspect.py").write_text("# Tool declarations only.\n", encoding="utf-8")
    (workspace / "tools" / "__pycache__" / "inspect.pyc").write_bytes(b"\xff\x00cache")
    application = ApplicationDefinition(
        models=(ModelProfile(id="model", model="offline"),),
        tools=(definition,),
        workspaces=(
            WorkspaceDefinition(
                id="assistant-workspace",
                source=workspace,
                managed_paths=("AGENTS.md", "skills", "tools"),
                protected_paths=(
                    "knowledge/faq",
                    "memory/records",
                    "memory/summary.md",
                    "memory/index.md",
                ),
                text_substitutions={"{{name}}": "Assistant"},
            ),
        ),
        skills=(SkillSourceDefinition(name="inspect", source=workspace / "skills" / "inspect"),),
        agents=(
            AgentSpecification(
                id="assistant",
                name="Assistant",
                instructions="Inspect public information.",
                model_profile_ref="model@1",
                workspace_id="assistant-workspace",
                skill_refs=("inspect@1",),
                tools=(
                    ToolBinding(
                        name="inspect",
                        tool_ref=definition.ref,
                        input_schema=definition.input_schema,
                    ),
                ),
                sandbox=SandboxConfiguration(
                    provider="docker",
                    capabilities=("shell", "filesystem", "compaction"),
                    options={
                        "image": "python:3.13-slim",
                        "network": "none",
                        "cpu": 1,
                        "memory_mb": 512,
                        "pids_limit": 128,
                    },
                ),
            ),
        ),
        roles=(
            AccessRole(
                name="assistant",
                grants=tuple(
                    PermissionGrant(action=action, resource=resource)
                    for action, resource in (
                        ("agent:run", "assistant@1"),
                        ("agent:read", "assistant"),
                        ("model:use", "model@1"),
                        ("capability:use", "skill:inspect@1"),
                        ("workspace:read", "assistant-workspace"),
                        ("workspace:write", "assistant-workspace"),
                        ("bots.receive", "bot"),
                        ("bots.send", "bot"),
                        ("tool:invoke", "inspect@1"),
                    )
                ),
            ),
        ),
        bot_accounts=(
            BotAccount(
                id="bot",
                self_id="123456",
                adapter="qq",
                settings={"app_id": "123456", "transport": "websocket"},
                credentials={"client_secret": "env:TEST_APP_SECRET"},
                unlinked_principal_id=principal.id,
                unlinked_channels=("group:test-group",),
            ),
        ),
        routes=(
            BotRoute(bot_id="bot", channel_id="group:test-group", agent_revision="assistant@1"),
        ),
    )
    manager = OpsMeshApplicationDeployment(
        DeploymentServices(
            accounts=service.account_management,
            agents=service.agent_management,
            models=service.model_management,
            capabilities=service.capability_management,
            skills=service.skill_management,
            workspaces=service.workspace_management,
            bots=service.bots,
            knowledge=service.knowledge_management,
            memory=service.memory_management,
        )
    )
    try:
        yield app, manager, application, owner, principal
    finally:
        await app.close()


async def test_apply_is_idempotent_and_keeps_explicit_sandbox_and_identity(deployment):
    app, manager, definition, owner, principal = deployment
    result = await manager.apply(definition, actor_id=owner.id)
    agent = await app.services.agent_management.get("assistant", actor_id=owner.id)
    role = await app.services.account_management.get_role("assistant", actor_id=owner.id)
    bot = await app.services.bots.get_account("bot", actor_id=owner.id)
    assert await manager.apply(definition, actor_id=owner.id) == result
    assert await app.services.agent_management.get("assistant", actor_id=owner.id) == agent
    assert await app.services.account_management.get_role("assistant", actor_id=owner.id) == role
    assert await app.services.bots.get_account("bot", actor_id=owner.id) == bot
    assert result.agents == {"assistant": "assistant@1"}
    assert result.models == {"model": "model@1"}
    assert result.skills == {"inspect@1": "inspect@1"}
    assert agent.draft.sandbox == definition.agents[0].sandbox
    assert not principal.is_admin and len(await app.services.accounts.list_users()) == 2
    assert all("*" not in item.resource for item in role.grants)
    workspace = app.services.workspace_management
    assert (
        await workspace.read("assistant-workspace", "AGENTS.md", actor_id=owner.id)
        == b"You are Assistant."
    )
    assert (await workspace.stat("assistant-workspace", "AGENTS.md", actor_id=owner.id)).read_only
    assert not any(
        "__pycache__" in item.path
        for item in await workspace.tree("assistant-workspace", actor_id=owner.id)
    )


async def test_upgrade_updates_versions_grants_route_and_preserves_learning(deployment):
    app, manager, definition, owner, principal = deployment
    await manager.apply(definition, actor_id=owner.id)
    workspace = app.services.workspace_management
    await workspace.write(
        "assistant-workspace", "memory/records/learned.md", b"retained", actor_id=principal.id
    )
    await workspace.write(
        "assistant-workspace", "knowledge/faq/answer.md", b"public answer", actor_id=principal.id
    )
    source = definition.workspaces[0].source
    (source / "AGENTS.md").write_text("New trusted instructions.", encoding="utf-8")
    (source / "skills" / "inspect" / "SKILL.md").write_text(
        "New skill instructions.", encoding="utf-8"
    )
    updated = replace(
        definition,
        models=(definition.models[0].model_copy(update={"model": "offline-new"}),),
        agents=(definition.agents[0].model_copy(update={"max_turns": 9}),),
    )
    result = await manager.apply(updated, actor_id=owner.id)
    assert result.agents["assistant"] == "assistant@2"
    assert result.models["model"] == "model@2"
    skill_ref = result.skills["inspect@1"]
    assert skill_ref.startswith("inspect@1-")
    published = await app.services.agent_management.get_revision("assistant@2", actor_id=owner.id)
    assert published.skill_refs == (skill_ref,) and published.model_profile_ref == "model@2"
    role = await app.services.account_management.get_role("assistant", actor_id=owner.id)
    grants = {(item.action, item.resource) for item in role.grants}
    assert ("agent:run", "assistant@2") in grants and ("agent:run", "assistant@1") not in grants
    assert ("model:use", "model@2") in grants and ("capability:use", f"skill:{skill_ref}") in grants
    route = await app.services.bots.get_route("bot", "group:test-group", actor_id=owner.id)
    assert route.agent_revision == "assistant@2"
    assert (
        await workspace.read("assistant-workspace", "memory/records/learned.md", actor_id=owner.id)
        == b"retained"
    )
    assert (
        await workspace.read("assistant-workspace", "knowledge/faq/answer.md", actor_id=owner.id)
        == b"public answer"
    )
    assert (
        await workspace.read("assistant-workspace", "AGENTS.md", actor_id=owner.id)
        == b"New trusted instructions."
    )
    # Real skill loading must accept the refreshed materialization on the existing workspace.
    await app.services.skill_management.load(
        skill_ref, "assistant-workspace", actor_id=principal.id
    )
    assert await manager.apply(updated, actor_id=owner.id) == result
    assert (
        await app.services.agent_management.get_revision("assistant@1", actor_id=owner.id)
    ).max_turns == 10


async def test_protected_learning_and_invalid_source_fail_before_publication(deployment):
    app, manager, definition, owner, _principal = deployment
    source = definition.workspaces[0].source
    (source / "memory" / "records").mkdir(parents=True)
    (source / "memory" / "records" / "learned.md").write_text("do not overwrite", encoding="utf-8")
    unsafe = replace(
        definition, workspaces=(replace(definition.workspaces[0], managed_paths=("memory",)),)
    )
    with pytest.raises(OpsMeshError, match="protected"):
        await manager.apply(unsafe, actor_id=owner.id)
    assert await app.services.model_management.list(actor_id=owner.id) == []
    assert await app.services.workspace_management.list(actor_id=owner.id) == ()
    unsafe = replace(
        definition, workspaces=(replace(definition.workspaces[0], managed_paths=("../escape",)),)
    )
    with pytest.raises(OpsMeshError) as error:
        await manager.apply(unsafe, actor_id=owner.id)
    assert error.value.code == "PATH_INVALID"


async def test_workspace_writer_cannot_replace_trusted_files_and_cas_rejects_stale_content(
    deployment,
):
    app, manager, definition, owner, principal = deployment
    await manager.apply(definition, actor_id=owner.id)
    workspace = app.services.workspace_management
    original = await workspace.read("assistant-workspace", "AGENTS.md", actor_id=owner.id)
    digest = hashlib.sha256(original).hexdigest()
    with pytest.raises(OpsMeshError) as denied:
        await workspace.replace_managed_file(
            "assistant-workspace",
            "AGENTS.md",
            b"attack",
            actor_id=principal.id,
            expected_digest=digest,
        )
    assert denied.value.code == "FORBIDDEN"
    await workspace.replace_managed_file(
        "assistant-workspace",
        "AGENTS.md",
        b"owner update",
        actor_id=owner.id,
        expected_digest=digest,
    )
    with pytest.raises(OpsMeshError) as conflict:
        await workspace.replace_managed_file(
            "assistant-workspace", "AGENTS.md", b"stale", actor_id=owner.id, expected_digest=digest
        )
    assert conflict.value.code == "CONFLICT"
    assert (
        await workspace.read("assistant-workspace", "AGENTS.md", actor_id=owner.id)
        == b"owner update"
    )
    assert (await workspace.stat("assistant-workspace", "AGENTS.md", actor_id=owner.id)).read_only
    with pytest.raises(OpsMeshError):
        await workspace.write(
            "assistant-workspace", "AGENTS.md", b"ordinary writer", actor_id=owner.id
        )


async def test_nonprivileged_actor_cannot_deploy_or_create_users(deployment):
    app, manager, definition, _owner, principal = deployment
    with pytest.raises(OpsMeshError) as denied:
        await manager.apply(definition, actor_id=principal.id)
    assert denied.value.code == "FORBIDDEN"
    assert len(await app.services.accounts.list_users()) == 2
    assert await app.services.models.list() == []


async def test_managed_file_compare_and_replace_has_only_one_concurrent_winner(deployment):
    app, manager, definition, owner, _principal = deployment
    await manager.apply(definition, actor_id=owner.id)
    workspace = app.services.workspace_management
    initial = await workspace.read("assistant-workspace", "AGENTS.md", actor_id=owner.id)
    digest = hashlib.sha256(initial).hexdigest()
    results = await asyncio.gather(
        *(
            workspace.replace_managed_file(
                "assistant-workspace",
                "AGENTS.md",
                content,
                actor_id=owner.id,
                expected_digest=digest,
            )
            for content in (b"first", b"second")
        ),
        return_exceptions=True,
    )
    errors = [result for result in results if isinstance(result, Exception)]
    assert len(errors) == 1 and isinstance(errors[0], OpsMeshError)
    assert errors[0].code == "CONFLICT"
    assert (await workspace.stat("assistant-workspace", "AGENTS.md", actor_id=owner.id)).read_only
