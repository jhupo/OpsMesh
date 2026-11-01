"""Bind platform authorization and services to one SDK-neutral execution context."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from time import perf_counter
from typing import TYPE_CHECKING, Any

from opsmesh.framework.agents import (
    AgentExecutionContext,
    AgentSpecification,
    CodeInterpreterBinding,
    FileSearchBinding,
    HostedMCPBinding,
    HostedToolBinding,
    NativeToolBinding,
)
from opsmesh.framework.errors import OpsMeshError
from opsmesh.modules.capabilities.api import PreparedAgentResources
from opsmesh.modules.runtime.api import AgentRunView

from .run_usage import RunUsageAccounting

if TYPE_CHECKING:
    from .ports import ApplicationServices

ResourcePreparation = Callable[[AgentSpecification], Awaitable[PreparedAgentResources]]
ResourceSynchronization = Callable[[AgentExecutionContext, str, dict[str, Any]], Awaitable[None]]


class AgentContextBindings:
    def __init__(
        self,
        services: ApplicationServices,
        run: AgentRunView,
        usage: RunUsageAccounting,
        prepare_resources: ResourcePreparation,
        sync_resources: ResourceSynchronization,
    ) -> None:
        self.services, self.run, self.usage = services, run, usage
        self.prepare_resources, self.sync_resources = prepare_resources, sync_resources
        self.context = AgentExecutionContext(
            run.id, run.session_id, run.principal_id, self.invoke, self.resolve
        )

    def build(self) -> AgentExecutionContext:
        self.context.services.update(
            {
                "resolve_secret": self.services.secrets.resolve,
                "prepare_model_profile": self.profile,
                "prepare_agent_resources": self.prepare_resources,
                "observe_usage": self.observe,
                "observe_resource_usage": self.observe_resource,
                "authorize_hosted_tool": self.authorize_hosted_tool,
                "authorize_native_tool": self.authorize_native_tool,
                "authorize_model_request": self.authorize_model_request,
                "trace_context": self.run.metadata.get("trace_context", {}),
                "attempt_id": self.run.attempt_id,
                "observe_metric": self.services.metrics.observe,
                "authorize_workspace": self.authorize_workspace,
                "sync_agent_resources": self.sync_resources,
            }
        )
        return self.context

    async def invoke(self, tool_ref: str, arguments: dict[str, Any], call_id: str) -> Any:
        started = perf_counter()
        try:
            result = await self.services.tool_invoker.invoke(
                tool_ref,
                arguments,
                principal_id=self.run.principal_id,
                call_id=f"{self.run.id}:{call_id}",
            )
            await self.sync_resources(self.context, tool_ref, arguments)
            self.services.metrics.increment("tool_calls")
            return result
        except Exception:
            self.services.metrics.increment("tool_calls", "failure")
            raise
        finally:
            self.services.metrics.observe("tool_duration_seconds", perf_counter() - started)

    async def resolve(self, reference: str) -> AgentSpecification:
        await self.services.authorization.check(self.run.principal_id, "agent:run", reference)
        return await self.services.agents.get_revision(reference)

    async def profile(self, specification: AgentSpecification) -> dict[str, Any] | None:
        reference = specification.model_profile_ref
        if reference is None:
            return None
        await self.services.authorization.check(self.run.principal_id, "model:use", reference)
        return (await self.services.models.get(reference)).model_dump(mode="json")

    async def observe(self, data: dict[str, Any]) -> None:
        await self.usage.observe(self.run, data)

    async def observe_resource(self, data: dict[str, Any]) -> None:
        await self.usage.observe_resource(self.run, data)

    async def authorize_workspace(self, workspace_id: str) -> None:
        await self.services.authorization.workspace(self.run.principal_id, workspace_id, write=True)

    async def authorize_native_tool(self, binding: NativeToolBinding) -> None:
        reference = (
            getattr(binding, "executor_ref", None)
            or getattr(binding, "editor_ref", None)
            or getattr(binding, "computer_ref", None)
        )
        if not isinstance(reference, str) or not reference:
            raise OpsMeshError("CONFIG_INVALID", "Native tool requires a registered implementation")
        await self.services.authorization.check(self.run.principal_id, "native_tool:use", reference)

    async def authorize_hosted_tool(self, binding: HostedToolBinding) -> None:
        check = self.services.authorization.check
        principal_id = self.run.principal_id
        await check(principal_id, "hosted_tool:use", binding.type)
        if isinstance(binding, FileSearchBinding):
            for vector_store in binding.vector_store_ids:
                await check(principal_id, "vector_store:use", vector_store)
        if isinstance(binding, CodeInterpreterBinding):
            for file_id in binding.file_ids:
                await check(principal_id, "provider_file:use", file_id)
            if binding.container_id:
                await check(principal_id, "hosted_container:use", binding.container_id)
        if isinstance(binding, HostedMCPBinding):
            await check(
                principal_id,
                "mcp:use",
                binding.server_url or binding.connector_id or binding.server_label,
            )
            for reference in [
                binding.authorization_secret_ref,
                *binding.header_secret_refs.values(),
            ]:
                if reference:
                    await check(principal_id, "secret:use", reference)

    async def authorize_model_request(self, profile_ref: str | None) -> None:
        if profile_ref:
            await self.services.authorization.check(self.run.principal_id, "model:use", profile_ref)
            # A cached SDK model must not bypass a version withdrawn during the Run.
            await self.services.models.get(profile_ref)
        status = await self.services.usage.budget(
            self.run.principal_id, self.services.settings.budget_currency
        )
        if status.limit is not None and status.spent >= status.limit:
            raise OpsMeshError("BUDGET_EXCEEDED", "The configured token budget is exhausted")
