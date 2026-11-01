"""Public application declarations use real platform resources and managed ingress."""

import importlib.util
import json
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from tools import jev

from opsmesh import OpsMeshSettings, create_application
from opsmesh.application.conversations import ConversationGateway
from opsmesh.framework.errors import OpsMeshError
from opsmesh.modules.accounts.api import AccessRole
from opsmesh.modules.capabilities.api import KnowledgeDocument
from opsmesh.modules.capabilities.knowledge import KnowledgeCollection
from opsmesh.modules.memory.api import MemoryCandidate, MemoryNamespace, MemoryNamespaceSettings
from opsmesh.modules.models.api import ModelProfile

_ROOT = Path(__file__).parents[2] / "examples/qq_community"
_SPEC = importlib.util.spec_from_file_location("community_entrypoint_under_test", _ROOT / "main.py")
assert _SPEC is not None and _SPEC.loader is not None
main = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = main
_SPEC.loader.exec_module(main)


def test_existing_resource_ids_can_be_reused_without_changing_the_gateway_bot():
    agent = main.create_agent("model@1", resource_id="existing-assistant")
    assert agent.id == agent.workspace_id == "existing-assistant"
    assert agent.knowledge_collections == ("existing-assistant",)


@pytest.fixture
async def deployed(tmp_path, monkeypatch):
    platform = {
        "data_directory": str(tmp_path / "platform"),
        "worker_enabled": False,
        "tracing_enabled": False,
    }
    async with create_application(OpsMeshSettings(**platform)) as setup:
        await setup.initialize(admin_username="admin", admin_password="application-password-123")
        admin = (await setup.services.accounts.list_users())[0]
        await setup.services.account_management.create_role(
            AccessRole(name="qq-community"), actor_id=admin.id
        )
        principal = await setup.services.account_management.create_user(
            "community", "service-account-password-123", roles=("qq-community",), actor_id=admin.id
        )
        await setup.services.memory_management.create_namespace(
            MemoryNamespace(id="qq-community", owner=principal.id), actor_id=principal.id
        )
        await setup.services.model_management.create(
            ModelProfile(id="model", model="offline"), actor_id=admin.id
        )
    requests = []

    def provider(request):
        body = json.loads(request.content)
        requests.append(body)
        return httpx.Response(
            200,
            json={
                "model": "jev-tested",
                "answers": {
                    name: {"type": "noul", "noul": 0.99 if name == "advertising" else 0.01}
                    for name in body["questions"]
                },
                "usage": {"input_tokens": 20, "output_tokens": 10},
            },
        )

    client_class = httpx.AsyncClient

    def test_client(**options):
        options.pop("proxy", None)
        return client_class(transport=httpx.MockTransport(provider), **options)

    monkeypatch.setattr(main.httpx, "AsyncClient", test_client)
    async with main.application(
        principal_id=principal.id,
        model_profile_ref="model@1",
        qq_app_id="100",
        qq_app_secret="synthetic-bot-secret",
        group_openid="exact_openid",
        bot_member_openid="self_member",
        jev_api_key="synthetic-moderation-secret",
        platform=platform,
    ) as app:
        result = await app.publish(actor_id=admin.id)
        gateway = ConversationGateway(
            bot_id="qq-community",
            channel_id="group:exact_openid",
            principal_id=principal.id,
            knowledge_collection="qq-community",
        )
        options = await gateway.resolve(
            app.services.bots, app.services.agent_management, app.settings.data_directory
        )
        yield SimpleNamespace(
            app=app,
            admin=admin,
            options=options,
            binding=options.bindings[0],
            agent=main.create_agent("model@1"),
            result=result,
            requests=requests,
            gateway=gateway,
        )


async def test_main_apply_is_idempotent_and_preserves_minimal_authority(deployed):
    h, services = deployed, deployed.app.services
    repeated = await h.app.publish(actor_id=h.admin.id)
    assert repeated.agents == h.result.agents
    principal = await services.accounts.get_principal(h.binding.principal_id)
    assert principal.active and not principal.is_admin and principal.departments == ()
    assert len(await services.accounts.list_users()) == 2
    role = await services.account_management.get_role(principal.roles[0], actor_id=h.admin.id)
    assert all("*" not in grant.action and "*" not in grant.resource for grant in role.grants)
    assert all(not grant.action.startswith("secret:") for grant in role.grants)
    for action in ("bots.manage", "model:manage", "accounts:user:create", "secret:use"):
        with pytest.raises(OpsMeshError):
            await services.accounts.authorize(principal, action, h.binding.bot_id)
    await services.accounts.authorize(principal, "bots.receive", h.binding.bot_id)
    paths = {
        item.path
        for item in await services.workspace_management.tree(
            h.binding.workspace_id, actor_id=principal.id
        )
    }
    assert {
        ".agents/gentle-sister/SKILL.md",
        ".agents/gentle-sister/references/persona.md",
    }.issubset(paths)
    assert not any(path.startswith(("skills/", "tools/", "knowledge/")) for path in paths)
    assert "AGENTS.md" not in paths and "main.py" not in paths
    assert not any("__pycache__" in path or path.endswith(".pyc") for path in paths)
    route = await services.bots.get_route(
        h.binding.bot_id, h.binding.channel_id, actor_id=h.admin.id
    )
    assert route.agent_revision == h.binding.agent_ref and route.dispatch_mode == "managed"


async def test_one_sandbox_agent_has_one_custom_tool_and_one_native_knowledge_collection(deployed):
    h, services = deployed, deployed.app.services
    definitions = await services.agent_management.list(actor_id=h.admin.id)
    assert len(definitions) == 1
    agent = await services.agent_management.get_revision(h.binding.agent_ref, actor_id=h.admin.id)
    assert agent.sandbox == h.agent.sandbox
    assert agent.sandbox.provider == "docker" and agent.sandbox.options["network_mode"] == "none"
    assert {"shell", "filesystem", "compaction"}.issubset(agent.sandbox.capabilities)
    assert (
        agent.workspace_id == h.binding.workspace_id and agent.instructions == h.agent.instructions
    )
    assert agent.skill_refs == ("gentle-sister@1",)
    assert agent.output_schema is None
    assert agent.knowledge_collections == ("qq-community",)
    assert not agent.memory_generate and agent.memory_namespace_id == "qq-community-memory"
    assert len(agent.tools) == 1 and agent.tools[0] == jev.JEV_REVIEW_TOOL_BINDING
    definition = await services.capabilities.get_tool(jev.JEV_REVIEW_TOOL_REFERENCE)
    assert definition.code_ref == jev.JEV_REVIEW_CODE_REFERENCE
    arguments = {"text": "Join this promotional campaign", "context": ["Earlier discussion"]}
    decision = await services.tool_invoker.invoke(
        definition.ref, arguments, principal_id=h.binding.principal_id, call_id="registered-jev"
    )
    assert decision["status"] == "ok" and decision["action"] == "remove"
    assert decision["categories"] == ["advertising"]
    assert h.requests[0]["state"] == {
        "target_message": arguments["text"],
        "recent_context": arguments["context"],
    }
    assert set(h.requests[0]["questions"]) == jev.CATEGORY_NAMES | {"needs_support"}
    assert (
        await services.tool_invoker.invoke(
            definition.ref, arguments, principal_id=h.binding.principal_id, call_id="registered-jev"
        )
        == decision
    )
    assert len(h.requests) == 1


async def test_real_run_preparation_enforces_skill_and_knowledge_boundaries(deployed, monkeypatch):
    h, services, binding = deployed, deployed.app.services, deployed.binding
    from opsmesh.framework.agents import AgentExecutionResources

    allocate = AsyncMock(return_value=AgentExecutionResources())
    monkeypatch.setattr(services.sandbox_bridge, "prepare", allocate)
    await services.knowledge_management.add(
        KnowledgeDocument(
            id="public-answer",
            collection=binding.knowledge_collection,
            text="OpsMesh installation uses systemd on Linux.",
        ),
        principal_id=binding.principal_id,
    )
    await services.knowledge_management.create_collection(
        KnowledgeCollection(id="private-collection"), principal_id=h.admin.id
    )
    session = await services.conversation_management.open(
        binding.agent_ref, binding.workspace_id, actor_id=binding.principal_id
    )
    run = await services.run_management.submit(
        actor_id=binding.principal_id,
        session_id=session.id,
        agent_ref=binding.agent_ref,
        input="How to install OpsMesh?",
        idempotency_key="resource-preparation",
    )
    context = await services.agent_bridge.context(run)
    try:
        allocate.assert_awaited_once()
        assert allocate.call_args.args[0].agent.sandbox.provider == "docker"
        profile = await context.services.prepare_model_profile(run.agent)
        assert profile["id"] == "model"
        resources = await context.services.prepare_agent_resources(run.agent)
        tools = {tool.name: tool.tool_ref for tool in resources.tools}
        assert {"load_skill", "read_skill", "search_knowledge"}.issubset(tools)
        loaded = await context.invoke_tool(
            tools["load_skill"], {"skill_ref": "gentle-sister@1"}, "load-persona"
        )
        result = await context.invoke_tool(
            tools["read_skill"], loaded["read_skill_arguments"], "read-persona"
        )
        assert result["text"] == (_ROOT / "skills/gentle-sister/SKILL.md").read_text(
            encoding="utf-8"
        )
        for index, path in enumerate(("skills/gentle-sister/SKILL.md", "../AGENTS.md")):
            with pytest.raises(OpsMeshError) as error:
                await context.invoke_tool(
                    tools["read_skill"],
                    {
                        "skill_ref": "gentle-sister@1",
                        "path": path,
                    },
                    f"bad-path-{index}",
                )
            assert error.value.code == "INPUT_INVALID"
        hits = await context.invoke_tool(
            tools["search_knowledge"],
            {
                "collection": binding.knowledge_collection,
                "query": "installation",
            },
            "search-own-collection",
        )
        assert hits and hits[0]["document"]["id"] == "public-answer"
        with pytest.raises(OpsMeshError) as error:
            await context.invoke_tool(
                tools["search_knowledge"],
                {
                    "collection": "private-collection",
                    "query": "installation",
                },
                "search-private-collection",
            )
        assert error.value.code == "INPUT_INVALID"
        with pytest.raises(OpsMeshError):
            await services.knowledge_management.search(
                "private-collection", "installation", principal_id=binding.principal_id
            )
    finally:
        await context.resources.cleanup_resources()


async def test_publication_updates_route_and_authority_without_losing_knowledge(deployed):
    h, services = deployed, deployed.app.services
    await services.knowledge_management.add(
        KnowledgeDocument(id="retained-answer", collection="qq-community", text="Retained answer."),
        principal_id=h.binding.principal_id,
    )
    updated = replace(
        h.app.publication.definition,
        agents=(h.agent.model_copy(update={"instructions": "Updated trusted instructions."}),),
    )
    h.app.define(updated)
    result = await h.app.publish(actor_id=h.admin.id)
    options = await h.gateway.resolve(
        services.bots, services.agent_management, h.app.settings.data_directory
    )
    binding = options.bindings[0]
    assert binding.agent_ref == result.agents[h.agent.id] == "qq-community@2"
    assert binding.principal_id == h.binding.principal_id
    assert len(await services.accounts.list_users()) == 2
    role = await services.account_management.get_role("qq-community", actor_id=h.admin.id)
    grants = {(grant.action, grant.resource) for grant in role.grants}
    assert ("agent:run", binding.agent_ref) in grants
    assert ("agent:run", h.binding.agent_ref) not in grants
    assert (
        await services.knowledge_management.get_document(
            "retained-answer", principal_id=binding.principal_id
        )
    ).text == "Retained answer."
    assert (await h.app.publish(actor_id=h.admin.id)).agents == result.agents


async def test_gateway_resolves_platform_route_and_rejects_direct_dispatch(deployed):
    h, services = deployed, deployed.app.services
    route = await services.bots.get_route(
        h.binding.bot_id, h.binding.channel_id, actor_id=h.admin.id
    )
    await services.bots.bind_route(
        route.bot_id,
        route.channel_id,
        route.agent_revision,
        dispatch_mode="direct",
        actor_id=h.admin.id,
    )
    with pytest.raises(OpsMeshError) as error:
        await h.gateway.resolve(
            services.bots, services.agent_management, h.app.settings.data_directory
        )
    assert error.value.code == "CONFLICT"


async def test_published_memory_is_group_scoped_and_republication_retains_learned_content(deployed):
    h, services = deployed, deployed.app.services
    memory = services.memory_management
    namespace = await memory.get_namespace(
        "qq-community-memory", principal_id=h.binding.principal_id
    )
    assert namespace.owner == h.admin.id
    assert namespace.readers == namespace.contributors == (h.binding.principal_id,)
    assert namespace.policy.extract_model_profile == "model@1"
    assert h.binding.memory_namespace_id == namespace.id
    namespace = await memory.configure_namespace(
        namespace.id,
        MemoryNamespaceSettings(
            readers=namespace.readers,
            contributors=namespace.contributors,
            policy=namespace.policy.model_copy(update={"live_update": True}),
        ),
        actor_id=h.admin.id,
        expected_revision=namespace.revision,
    )
    await memory.update(
        namespace.id,
        MemoryCandidate(summary="Confirmed public preference", index="source-event"),
        principal_id=h.admin.id,
        expected_revision=namespace.revision,
        idempotency_key="verified-memory",
    )
    await h.app.publish(actor_id=h.admin.id)
    assert not (
        await memory.get_namespace(namespace.id, principal_id=h.admin.id)
    ).policy.live_update
    assert (await memory.read(namespace.id, principal_id=h.binding.principal_id)).summary == (
        "Confirmed public preference"
    )
    stranger = await services.account_management.create_user(
        "stranger", "unrelated-password-123", actor_id=h.admin.id
    )
    with pytest.raises(OpsMeshError):
        await memory.read(namespace.id, principal_id=stranger.id)


async def test_runtime_credentials_are_not_written_to_platform_or_workspace(deployed):
    h, services = deployed, deployed.app.services
    account = await services.bots.get_account(h.binding.bot_id, actor_id=h.admin.id)
    assert account.credentials == {"client_secret": "runtime:qq-client-secret"}
    assert services.secrets.resolve(account.credentials["client_secret"]) == "synthetic-bot-secret"
    record_files = [*h.app.settings.data_directory.rglob("*.json")]
    assert not (h.app.settings.data_directory / "projects").exists()
    for path in record_files:
        assert b"synthetic-bot-secret" not in path.read_bytes()
    database = h.app.settings.data_directory / "opsmesh.db"
    assert database.is_file()
    assert b"synthetic-bot-secret" not in database.read_bytes()


async def test_gateway_preparation_reads_the_published_agent_and_freezes_declarations(
    deployed, monkeypatch
):

    h = deployed
    applications = []

    def compose(options, policy, inbox):
        applications.append((options, policy, inbox))
        return SimpleNamespace(register=lambda: None)

    monkeypatch.setattr(h.app.publication, "_conversations", compose)
    local_agent = h.agent.model_copy(update={"instructions": "Unpublished local instructions."})
    h.app.define(replace(h.app.publication.definition, agents=(local_agent,)))
    await h.app.publication.prepare()
    await h.app.publication.prepare()
    assert len(applications) == 1
    options = applications[0][0]
    assert options.bindings[0].agent_ref == h.binding.agent_ref
    with pytest.raises(OpsMeshError):
        h.app.define(h.app.publication.definition)
    with pytest.raises(OpsMeshError):
        await h.app.publish(actor_id=h.admin.id)


async def test_publishing_cannot_reinterpret_a_pending_gateway_batch(deployed):
    from datetime import UTC, datetime

    from opsmesh.modules.bots.conversations import QueuedConversationMessage

    h = deployed
    inbox = h.app.services.conversation_inboxes.open(h.gateway.scope)
    await inbox.enqueue(
        QueuedConversationMessage(
            event_id="pending-publish",
            bot_id=h.binding.bot_id,
            channel_id=h.binding.channel_id,
            user_id="member",
            message_id="message",
            text="Pending conversation",
            occurred_at=datetime.now(UTC),
            to_me=True,
        )
    )
    await inbox.set_moderation(
        "pending-publish", {"status": "ok", "action": "allow"}, eligible_for_chat=True
    )
    batch = await inbox.claim_batch(h.binding.bot_id, h.binding.channel_id)
    assert batch is not None
    changed = h.agent.model_copy(update={"instructions": "New instructions."})
    h.app.define(replace(h.app.publication.definition, agents=(changed,)))
    with pytest.raises(OpsMeshError) as error:
        await h.app.publish(actor_id=h.admin.id)
    assert error.value.code == "CONVERSATION_BINDING_BUSY"
    route = await h.app.services.bots.get_route(
        h.binding.bot_id, h.binding.channel_id, actor_id=h.admin.id
    )
    assert route.agent_revision == h.binding.agent_ref
