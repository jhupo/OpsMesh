"""Bind platform authorization and services to one SDK-neutral execution context."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from time import perf_counter
from typing import Any

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
from opsmesh.infrastructure.secrets import DeploymentSecretResolver
from opsmesh.infrastructure.telemetry.metrics import PlatformMetrics
from opsmesh.modules.agents.api import AgentDefinitionCatalog
from opsmesh.modules.capabilities.api import PreparedAgentResources, ToolInvoker
from opsmesh.modules.models.api import ModelProfiles
from opsmesh.modules.models.credentials import ModelCredentialManagement
from opsmesh.modules.runtime.api import AgentRunView
from opsmesh.modules.usage.api import UsageService

from .authorization import PlatformAuthorization
from .conversations.validation import validate_conversation_agent
from .ports import ApplicationSettings
from .run_usage import RunUsageAccounting

ResourcePreparation = Callable[[AgentSpecification], Awaitable[PreparedAgentResources]]
ResourceSynchronization = Callable[[AgentExecutionContext, str, dict[str, Any]], Awaitable[None]]


class AgentContextBindings:
    def __init__(
        self,
        *,
        run: AgentRunView,
        usage: RunUsageAccounting,
        prepare_resources: ResourcePreparation,
        sync_resources: ResourceSynchronization,
        agents: AgentDefinitionCatalog,
        authorization: PlatformAuthorization,
        metrics: PlatformMetrics,
        model_credentials: ModelCredentialManagement,
        models: ModelProfiles,
        secrets: DeploymentSecretResolver,
        settings: ApplicationSettings,
        tool_invoker: ToolInvoker,
        ledger: UsageService,
    ) -> None:
        self.agents = agents
        self.authorization = authorization
        self.metrics = metrics
        self.model_credentials = model_credentials
        self.models = models
        self.secrets = secrets
        self.settings = settings
        self.tool_invoker = tool_invoker
        self.ledger = ledger
        self.run, self.usage = run, usage
        self.prepare_resources, self.sync_resources = prepare_resources, sync_resources
        self.context = AgentExecutionContext(
            run.id, run.session_id, run.principal_id, self.invoke, self.resolve
        )

    def build(self) -> AgentExecutionContext:
        if self.run.metadata.get("conversation_operation"):
            validate_conversation_agent(self.run.agent)
        self.context.services.resolve_secret = self.resolve_secret
        self.context.services.prepare_model_profile = self.profile
        self.context.services.prepare_agent_resources = self.prepare_resources
        self.context.services.observe_usage = self.observe
        self.context.services.observe_resource_usage = self.observe_resource
        self.context.services.authorize_hosted_tool = self.authorize_hosted_tool
        self.context.services.authorize_native_tool = self.authorize_native_tool
        self.context.services.authorize_model_request = self.authorize_model_request
        self.context.services.trace_context = self.run.metadata.get("trace_context", {})
        self.context.state.attempt_id = self.run.attempt_id
        self.context.services.observe_metric = self.metrics.observe
        self.context.services.authorize_workspace = self.authorize_workspace
        self.context.services.sync_agent_resources = self.sync_resources
        return self.context

    async def invoke(self, tool_ref: str, arguments: dict[str, Any], call_id: str) -> Any:
        started = perf_counter()
        try:
            result = await self.tool_invoker.invoke(
                tool_ref,
                arguments,
                principal_id=self.run.principal_id,
                call_id=f"{self.run.id}:{call_id}",
            )
            await self.sync_resources(self.context, tool_ref, arguments)
            self.metrics.increment("tool_calls")
            return result
        except Exception:
            self.metrics.increment("tool_calls", "failure")
            raise
        finally:
            self.metrics.observe("tool_duration_seconds", perf_counter() - started)

    async def resolve_secret(self, reference: str) -> str:
        if reference.startswith("credential:"):
            return await self.model_credentials.resolve(reference, actor_id=self.run.principal_id)
        return self.secrets.resolve(reference)

    async def resolve(self, reference: str) -> AgentSpecification:
        await self.authorization.check(self.run.principal_id, "agent:run", reference)
        return await self.agents.get_revision(reference)

    async def profile(self, specification: AgentSpecification) -> dict[str, Any] | None:
        reference = specification.model_profile_ref
        if reference is None:
            return None
        await self.authorization.check(self.run.principal_id, "model:use", reference)
        return (await self.models.get(reference)).model_dump(mode="json")

    async def observe(self, data: dict[str, Any]) -> None:
        await self.usage.observe(self.run, data)

    async def observe_resource(self, data: dict[str, Any]) -> None:
        await self.usage.observe_resource(self.run, data)

    async def authorize_workspace(self, workspace_id: str) -> None:
        await self.authorization.workspace(self.run.principal_id, workspace_id, write=True)

    async def authorize_native_tool(self, binding: NativeToolBinding) -> None:
        reference = (
            getattr(binding, "executor_ref", None)
            or getattr(binding, "editor_ref", None)
            or getattr(binding, "computer_ref", None)
        )
        if not isinstance(reference, str) or not reference:
            raise OpsMeshError("CONFIG_INVALID", "Native tool requires a registered implementation")
        await self.authorization.check(self.run.principal_id, "native_tool:use", reference)

    async def authorize_hosted_tool(self, binding: HostedToolBinding) -> None:
        check = self.authorization.check
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
            await self.authorization.check(self.run.principal_id, "model:use", profile_ref)
            # A cached SDK model must not bypass a version withdrawn during the Run.
            profile = await self.models.get(profile_ref)
            if profile.owner_id:
                await self.model_credentials.validate_reference(
                    profile.secret_ref, actor_id=self.run.principal_id
                )
        status = await self.ledger.budget(self.run.principal_id, self.settings.budget_currency)
        if status.limit is not None and status.spent >= status.limit:
            raise OpsMeshError("BUDGET_EXCEEDED", "The configured token budget is exhausted")
