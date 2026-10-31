"""Coordinate agent publication, execution resources and durable result finalization."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from opsmesh.framework.agents import AgentExecutionContext, AgentRunCompleted, AgentSpecification
from opsmesh.modules.capabilities.api import AgentResourceRequest, PreparedAgentResources
from opsmesh.modules.memory.api import MemorySource
from opsmesh.modules.runtime.api import AgentRunView

from .agent_context import AgentContextBindings
from .agent_validation import AgentPublicationValidator
from .run_resources import RunResourceScope
from .run_usage import RunUsageAccounting

if TYPE_CHECKING:
    from .ports import ApplicationServices


class AgentExecutionBridge:
    def __init__(self, services: ApplicationServices) -> None:
        self.services = services
        self._validator = AgentPublicationValidator(services)
        self._usage = RunUsageAccounting(services)

    async def validate(self, specification: AgentSpecification) -> None:
        await self._validator.validate(specification)

    async def context(self, run: AgentRunView) -> AgentExecutionContext:
        services = self.services
        principal = await services.accounts.get_principal(run.principal_id)
        if run.metadata.get("purpose") != "memory_generation":
            await services.accounts.authorize(principal, "agent:run", run.agent.ref)
        await services.agents.get_revision(run.agent.ref)

        async def resources(specification: AgentSpecification) -> PreparedAgentResources:
            return await self._prepare_resources(run, specification)

        context = AgentContextBindings(
            services, run, self._usage, resources, self._sync_skill_resources
        ).build()
        if run.has_final_outcome:
            # Persisted outcomes need finalization, without reading deleted inference resources.
            return context
        await self._usage.reserve(run)
        scope = RunResourceScope(services, run, context)
        context.services.update(await scope.open(lambda: resources(run.agent)))
        return context

    async def settle(self, run: AgentRunView) -> None:
        await self._usage.settle(run)

    async def _sync_skill_resources(
        self, context: AgentExecutionContext, tool_ref: str, arguments: dict[str, Any]
    ) -> None:
        synchronize = context.services.get("sync_resources")
        if synchronize is None:
            return
        tool = await self.services.capabilities.get_tool(tool_ref)
        if not tool.code_ref.startswith(("resource:load_skill:", "resource:read_skill:")):
            return
        skill = await self.services.skills.get(str(arguments["skill_ref"]))
        files = tuple(
            [
                (
                    f".agents/{skill.name}/{item.path}",
                    await self.services.workspaces.get_blob(item.digest),
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
        services = self.services
        session = await services.sessions.get(run.session_id)
        workspace_id = specification.workspace_id or session.workspace_id
        if workspace_id:
            await services.authorization.workspace(run.principal_id, workspace_id, write=True)
        prepared = await services.resources.prepare(
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
            memory = await services.memory_tools.prepare(run, specification.memory_namespace_id)
            prepared = prepared.model_copy(
                update={
                    "instructions": f"{prepared.instructions}\n{memory.instructions}",
                    "tools": (*prepared.tools, *memory.tools),
                }
            )
        return prepared

    async def finalize(self, run: AgentRunView, outcome: AgentRunCompleted) -> dict[str, Any]:
        services = self.services
        sandbox = await services.sandbox_bridge.finalize(run, outcome)
        if workspace_id := sandbox.get("workspace_id"):
            existing = await services.workspace_owners.get(workspace_id)
            if existing is None:
                await services.workspace_owners.create(workspace_id, {"owner_id": run.principal_id})
        output = (
            outcome.output
            if isinstance(outcome.output, str)
            else json.dumps(outcome.output, ensure_ascii=False)
        )
        await services.sessions.append(
            run.session_id, "assistant", output, message_id=f"{run.id}:assistant", run_id=run.id
        )
        rollout = json.dumps(
            {"run_id": run.id, "output": outcome.output, "history": outcome.history},
            ensure_ascii=False,
        ).encode()
        digest = await services.workspaces.put_blob(rollout)
        await self._usage.finish(run, outcome)
        if run.agent.memory_namespace_id and run.agent.memory_generate:
            await services.memory.append_source(
                MemorySource(
                    id=run.id,
                    namespace_id=run.agent.memory_namespace_id,
                    conversation_id=run.session_id,
                    run_id=run.id,
                    text=rollout.decode(),
                ),
                principal_id=run.principal_id,
            )
            await services.memory.request_generation(
                run.agent.memory_namespace_id, principal_id=run.principal_id, idempotency_key=run.id
            )
        return {**sandbox, "rollout_digest": digest}
