"""One sandbox Agent, a persona skill, a Jev tool, and an explicit QQ gateway."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from pydantic import SecretStr
from tools.jev import JEV_REVIEW_TOOL_DEFINITION as jev_tool
from tools.jev import JevConversationPolicy, JevModerationTool

from opsmesh import OpsMeshApplication, OpsMeshSettings, create_application, run
from opsmesh.application.conversations import (
    ConversationGateway,
    ConversationSettings,
)
from opsmesh.application.deployment import (
    ApplicationDefinition,
    MemoryNamespaceDefinition,
    SkillSourceDefinition,
    WorkspaceDefinition,
)
from opsmesh.framework.agents import AgentSpecification, SandboxConfiguration
from opsmesh.modules.accounts.api import AccessRole, PermissionGrant
from opsmesh.modules.bots.api import BotAccount, BotRoute
from opsmesh.modules.capabilities.knowledge import KnowledgeCollection
from opsmesh.modules.memory.api import MemoryNamespaceSettings, MemoryPolicy

ROOT = Path(__file__).parent


def create_agent(
    model_profile_ref: str, *, resource_id: str = "qq-community"
) -> AgentSpecification:
    return AgentSpecification(
        id=resource_id,
        name="十一",
        instructions=(ROOT / "AGENTS.md").read_text(encoding="utf-8"),
        model_profile_ref=model_profile_ref,
        workspace_id=resource_id,
        sandbox=SandboxConfiguration(
            provider="docker",
            capabilities=("shell", "filesystem", "compaction"),
            options={"image": "python:3.12-slim", "network_mode": "none"},
        ),
        skill_refs=("gentle-sister@1",),
        tools=(jev_tool.bind(strict_schema=True),),
        knowledge_collections=(resource_id,),
        memory_namespace_id=resource_id + "-memory",
        max_turns=8,
        timeout_seconds=150,
    )


@asynccontextmanager
async def application(
    *,
    principal_id: str,
    model_profile_ref: str,
    qq_app_id: str,
    qq_app_secret: str,
    group_openid: str,
    bot_member_openid: str,
    jev_api_key: str,
    resource_id: str = "qq-community",
    jev_proxy: str | None = None,
    platform: dict[str, Any] | None = None,
) -> AsyncIterator[OpsMeshApplication]:
    """Receive deployment parameters; publishing and serving are separate operations.

    Provision a non-admin platform principal with the qq-community role first.
    Configure the platform model profile independently of this example.
    """
    settings = OpsMeshSettings(**(platform or {}))
    if settings.run_worker_mode != "inline":
        raise ValueError("This local Python tool requires the inline Run worker")
    async with (
        httpx.AsyncClient(proxy=jev_proxy, trust_env=False, follow_redirects=False) as client,
        create_application(settings, secrets={"qq-client-secret": SecretStr(qq_app_secret)}) as app,
    ):
        jev = JevModerationTool(jev_api_key, client)
        app.register_tool(jev_tool, jev.review_tool)
        agent = create_agent(model_profile_ref, resource_id=resource_id)
        channel = f"group:{group_openid}"
        bot = BotAccount(
            id="qq-community",
            adapter="qq",
            self_id=qq_app_id,
            settings={
                "app_id": qq_app_id,
                "transport": "websocket",
                "intents": 33554432,
                "group_member_openids": {group_openid: bot_member_openid},
            },
            credentials={"client_secret": "runtime:qq-client-secret"},
            unlinked_principal_id=principal_id,
            unlinked_channels=(channel,),
        )
        grants = (
            ("bots.read", bot.id),
            ("bots.receive", bot.id),
            ("bots.send", bot.id),
            ("model:use", model_profile_ref),
            ("agent:read", agent.id),
            ("agent:run", agent.ref),
            ("workspace:read", resource_id),
            ("workspace:write", resource_id),
            ("tool:invoke", jev_tool.ref),
            ("capability:use", "skill:gentle-sister@1"),
            ("capability:use", f"knowledge:read:{resource_id}"),
            ("capability:use", f"knowledge:write:{resource_id}"),
        )
        app.define(
            ApplicationDefinition(
                agents=(agent,),
                skills=(
                    SkillSourceDefinition(
                        name="gentle-sister", source=ROOT / "skills/gentle-sister"
                    ),
                ),
                workspaces=(WorkspaceDefinition(id=resource_id),),
                knowledge_collections=(KnowledgeCollection(id=resource_id, title="群问答"),),
                memory_namespaces=(
                    MemoryNamespaceDefinition(
                        id=agent.memory_namespace_id,
                        settings=MemoryNamespaceSettings(
                            readers=(principal_id,),
                            contributors=(principal_id,),
                            policy=MemoryPolicy(
                                extract_model_profile=model_profile_ref,
                                consolidate_model_profile=model_profile_ref,
                                source_retention_seconds=30 * 86400,
                                job_retention_seconds=30 * 86400,
                            ),
                        ),
                    ),
                ),
                roles=(
                    AccessRole(
                        name=resource_id,
                        grants=tuple(
                            PermissionGrant(action=action, resource=resource)
                            for action, resource in grants
                        ),
                    ),
                ),
                bot_accounts=(bot,),
                routes=(
                    BotRoute(
                        bot_id=bot.id,
                        channel_id=channel,
                        agent_revision=agent.ref,
                        dispatch_mode="managed",
                    ),
                ),
            )
        )
        app.add_conversation(
            ConversationGateway(
                bot_id=bot.id,
                channel_id=channel,
                principal_id=principal_id,
                knowledge_collection=resource_id,
                proactive_enabled=True,
                settings=ConversationSettings(
                    images_enabled=True,
                    image_allowed_hosts=(
                        "multimedia.nt.qq.com",
                        "gchat.qpic.cn",
                        "c2cpicdw.qpic.cn",
                    ),
                    removed_notice="刚才一条消息因{reason}已撤回, 请遵守群规。",
                    unconfirmed_notice="检测到消息可能涉及{reason}, 未确认撤回成功, 请管理员核查。",
                ),
            ),
            policy=JevConversationPolicy(jev),
        )
        yield app


if __name__ == "__main__":
    run(application)
