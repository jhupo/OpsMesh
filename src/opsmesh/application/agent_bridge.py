"""Prepare authorized execution contexts and their Agent resources."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from opsmesh.framework.agents import AgentExecutionContext, AgentSpecification
from opsmesh.modules.accounts.api import AccountDirectory
from opsmesh.modules.agents.api import AgentDefinitionCatalog
from opsmesh.modules.capabilities.api import (
    AgentResourceRequest,
    AgentResources,
    CapabilityCatalog,
    PreparedAgentResources,
    SkillCatalog,
)
from opsmesh.modules.runtime.api import AgentRunView
from opsmesh.modules.sessions.api import Conversations
from opsmesh.modules.workspaces.api import WorkspaceService

from .agent_context import ResourcePreparation, ResourceSynchronization
from .authorization import PlatformAuthorization
from .memory_tools import MemoryResourceBindings
from .run_resources import RunResourceScope
from .run_usage import RunUsageAccounting


class AgentExecutionBridge:
    def __init__(
        self,
        *,
        accounts: AccountDirectory,
        agents: AgentDefinitionCatalog,
        authorization: PlatformAuthorization,
        resources: AgentResources,
        capabilities: CapabilityCatalog,
        skills: SkillCatalog,
        sessions: Conversations,
        workspaces: WorkspaceService,
        memory_tools: MemoryResourceBindings,
        usage: RunUsageAccounting,
        contexts: Callable[
            [AgentRunView, ResourcePreparation, ResourceSynchronization], AgentExecutionContext
        ],
        scopes: Callable[[AgentRunView, AgentExecutionContext], RunResourceScope],
    ) -> None:
        self.accounts, self.agents, self.authorization = accounts, agents, authorization
        self.resources, self.capabilities, self.skills = resources, capabilities, skills
        self.sessions, self.workspaces, self.memory_tools = sessions, workspaces, memory_tools
        self._usage, self._contexts, self._scopes = usage, contexts, scopes

    async def context(self, run: AgentRunView) -> AgentExecutionContext:
        principal = await self.accounts.get_principal(run.principal_id)
        if run.metadata.get("purpose") != "memory_generation":
            await self.accounts.authorize(principal, "agent:run", run.agent.ref)
        await self.agents.get_revision(run.agent.ref)

        async def resources(specification: AgentSpecification) -> PreparedAgentResources:
            return await self._prepare_resources(run, specification)

        context = self._contexts(run, resources, self._sync_skill_resources)
        if run.has_final_outcome:
            # Persisted outcomes need finalization, without reading deleted inference resources.
            return context
        await self._usage.reserve(run)
        scope = self._scopes(run, context)
        context.resources = await scope.open(lambda: resources(run.agent))
        return context

    async def _sync_skill_resources(
        self, context: AgentExecutionContext, tool_ref: str, arguments: dict[str, Any]
    ) -> None:
        synchronize = context.resources.sync_resources
        if synchronize is None:
            return
        tool = await self.capabilities.get_tool(tool_ref)
        if not tool.code_ref.startswith(("resource:load_skill:", "resource:read_skill:")):
            return
        skill = await self.skills.get(str(arguments["skill_ref"]))
        files = tuple(
            [
                (
                    f".agents/{skill.name}/{item.path}",
                    await self.workspaces.get_blob(item.digest),
                    True,
                )
                for item in skill.files
            ]
        )
        await synchronize(files)

    async def _prepare_resources(
        self,
        run: AgentRunView,
        specification: AgentSpecification,
    ) -> PreparedAgentResources:
        session = await self.sessions.get(run.session_id)
        workspace_id = specification.workspace_id or session.workspace_id
        if workspace_id:
            await self.authorization.workspace(run.principal_id, workspace_id, write=True)
        prepared = await self.resources.prepare(
            AgentResourceRequest(
                skill_refs=specification.skill_refs,
                skill_loading=specification.skill_loading,
                mcp_refs=specification.mcp_refs,
                knowledge_collections=specification.knowledge_collections,
                workspace_id=workspace_id,
            ),
            principal_id=run.principal_id,
            run_id=run.id,
            lifecycle_id=run.attempt_id,
        )
        if specification.memory_namespace_id:
            memory = await self.memory_tools.prepare(run, specification.memory_namespace_id)
            prepared = prepared.model_copy(
                update={
                    "instructions": f"{prepared.instructions}\n{memory.instructions}",
                    "tools": (*prepared.tools, *memory.tools),
                }
            )
        return prepared
