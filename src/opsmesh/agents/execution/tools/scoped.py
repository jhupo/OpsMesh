"""Blocking product/MCP adapters own a Session only for one tool operation."""

import asyncio
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.agents.execution.contracts import AgentRuntimeContext, AgentRuntimeToolResult
from opsmesh.agents.execution.errors import AgentRuntimeCancelledError
from opsmesh.agents.execution.tools.cancellation import request_tool_cancellation
from opsmesh.agents.execution.tools.executor import BackendToolExecutor, tool_result_payload
from opsmesh.agents.profiles.models import AgentProfile
from opsmesh.agents.providers.models import ModelProviderCredential
from opsmesh.agents.providers.policy import credential_is_selectable
from opsmesh.capabilities.mcp.execution.prepared import PreparedMcpTool, ToolPreparation
from opsmesh.governance.costs.service import CostAccountingService
from opsmesh.identity.authorization.execution import ExecutionIdentityService
from opsmesh.identity.authorization.resource_queries import execution_resource_queries
from opsmesh.orchestration.approvals.pending_tools import PendingToolInvocationService
from opsmesh.orchestration.runs.authorization.scopes import agent_execution_scope
from opsmesh.orchestration.runs.authorization.validation import RunAuthorizationService
from opsmesh.orchestration.runs.models import AgentRun
from opsmesh.orchestration.runs.queries import authorization_snapshot_for_run
from opsmesh.runtime.instances.contracts import DockerRuntimeClient
from opsmesh.runtime.queues.dispatch import QueueDispatchPublisher
from opsmesh.runtime.queues.service import RedisQueue
from opsmesh.runtime.self_hosted.models import SelfHostedMcpJob
from opsmesh.shared.config import Settings
from opsmesh.shared.db.operations import DatabaseOperations
from opsmesh.shared.security.secrets import SecretEncryptionService
from opsmesh.shared.utils import uuid_or_none


@dataclass(frozen=True)
class ScopedToolExecutor:
    database: DatabaseOperations
    settings: Settings
    docker_client: DockerRuntimeClient | None
    queue: RedisQueue
    active: set[asyncio.Task[object]] = field(default_factory=set, compare=False, repr=False)

    async def cancel_active_tools(self, *, context: AgentRuntimeContext) -> None:
        await self.database.run(lambda session: request_tool_cancellation(session, context))
        current = asyncio.current_task()
        for task in tuple(self.active):
            if task is not current:
                task.cancel()
        await asyncio.gather(
            *(task for task in tuple(self.active) if task is not current), return_exceptions=True
        )

    def _authorize(self, session: Session, context: AgentRuntimeContext) -> None:
        status = session.scalar(
            select(AgentRun.status).where(
                AgentRun.workspace_id == context.workspace_id, AgentRun.id == context.run_id
            )
        )
        if status != "running":
            raise AgentRuntimeCancelledError
        profile_id = uuid_or_none(context.metadata.get("agent_profile_id"))
        if (
            profile_id is not None
            and session.scalar(
                select(AgentProfile.id).where(
                    AgentProfile.workspace_id == context.workspace_id,
                    AgentProfile.id == profile_id,
                    AgentProfile.status == "active",
                    AgentProfile.archived_at.is_(None),
                )
            )
            is None
        ):
            raise ValueError("Execution agent is no longer active")

    def _executor(self, session: Session) -> BackendToolExecutor:
        secrets = SecretEncryptionService(
            secret=self.settings.credential_encryption_secret,
            key_id=self.settings.credential_encryption_key_id,
            previous_secrets=self.settings.credential_encryption_previous_secrets,
        )
        return BackendToolExecutor(
            session,
            settings=self.settings,
            docker_client=self.docker_client,
            secret_service=secrets,
        )

    async def authorize_agent(self, *, context: AgentRuntimeContext) -> None:
        def operation(session: Session) -> None:
            self._authorize(session, context)
            ExecutionIdentityService(session).for_run(context.workspace_id, context.run_id)
            run = session.get(AgentRun, context.run_id)
            if run is None:
                raise AgentRuntimeCancelledError
            profile_id = uuid_or_none(context.metadata.get("agent_profile_id"))
            if profile_id is None and run.agent_profile_id is not None:
                raise ValueError("Execution agent identity is missing")
            fingerprint = context.metadata.get("capability_catalog_fingerprint")
            scope = agent_execution_scope(
                authorization_snapshot_for_run(run),
                profile_id,
                fingerprint if isinstance(fingerprint, str) else None,
            )
            RunAuthorizationService(session).validate_capability_scope(context.workspace_id, scope)
            provider = context.metadata.get("model_provider_provider")
            model = context.metadata.get("run_model")
            if not isinstance(provider, str) or not isinstance(model, str):
                raise ValueError("Execution model identity is missing")
            CostAccountingService(session).assert_budget_available(
                context.workspace_id, provider=provider, model=model
            )
            credential_id = uuid_or_none(context.metadata.get("model_provider_credential_id"))
            if credential_id is not None:
                credential = session.scalar(
                    select(ModelProviderCredential).where(
                        ModelProviderCredential.workspace_id == context.workspace_id,
                        ModelProviderCredential.id == credential_id,
                    )
                )
                if not credential_is_selectable(credential):
                    raise ValueError("Execution model credential is no longer selectable")

        await self.database.run(operation)

    async def review_tool_call(
        self, *, context: AgentRuntimeContext, tool_name: str, arguments: dict[str, object]
    ) -> dict[str, object]:
        def operation(session: Session) -> dict[str, object]:
            self._authorize(session, context)
            user = ExecutionIdentityService(session).for_run(context.workspace_id, context.run_id)
            with execution_resource_queries(session, context.workspace_id, user):
                return self._executor(session).review_tool_call(
                    context=context, tool_name=tool_name, arguments=arguments
                )

        return await self.database.run(operation)

    async def execute_tool(
        self,
        *,
        context: AgentRuntimeContext,
        tool_name: str,
        arguments: dict[str, object],
        tool_call_id: str | None = None,
        approval_granted: bool = False,
    ) -> AgentRuntimeToolResult:
        def operation(session: Session) -> ToolPreparation:
            self._authorize(session, context)
            user = ExecutionIdentityService(session).for_run(context.workspace_id, context.run_id)
            with execution_resource_queries(session, context.workspace_id, user):
                prepared = self._executor(session).prepare_tool(
                    context=context,
                    tool_name=tool_name,
                    arguments=arguments,
                    tool_call_id=tool_call_id,
                    approval_granted=approval_granted,
                )
            QueueDispatchPublisher(session, self.queue).publish_pending(
                workspace_id=context.workspace_id,
            )
            return prepared

        result = await self.database.run(operation)
        if isinstance(result, PreparedMcpTool):
            prepared = result
            response, error = None, None
            try:
                operation_task = asyncio.create_task(
                    prepared.execution.operation.execute(self.database.io)
                )
                self.active.add(operation_task)
                try:
                    response = await operation_task
                finally:
                    self.active.discard(operation_task)
            except BaseException as failure:
                error = failure

            def settle(session: Session) -> AgentRuntimeToolResult:
                ExecutionIdentityService(session).for_run(context.workspace_id, context.run_id)
                return self._executor(session).complete_tool(prepared, response, error)

            result = await self.database.run(settle)
            if error is not None and not isinstance(error, Exception):
                raise error
        if result.status != "waiting_self_hosted":
            return result
        job_id = (result.output or {}).get("mcp_job_id")
        if not isinstance(job_id, str) or not tool_call_id:
            raise ValueError("Runtime RPC is missing its job or SDK tool call identity")

        def completed(session: Session) -> AgentRuntimeToolResult | None:
            self._authorize(session, context)
            ExecutionIdentityService(session).for_run(context.workspace_id, context.run_id)
            job = session.scalar(
                select(SelfHostedMcpJob).where(
                    SelfHostedMcpJob.id == UUID(job_id),
                    SelfHostedMcpJob.workspace_id == context.workspace_id,
                    SelfHostedMcpJob.agent_run_id == context.run_id,
                    SelfHostedMcpJob.tool_call_id == tool_call_id,
                    SelfHostedMcpJob.tool_name == tool_name,
                )
            )
            if job is None:
                raise ValueError("Runtime RPC job is outside the tool invocation scope")
            if job.status in {"queued", "claimed"}:
                return None
            response = AgentRuntimeToolResult(
                status="completed" if job.status == "completed" else "failed",
                output=job.response_payload,
                error=job.error_payload,
                metadata=result.metadata,
            )
            secrets = SecretEncryptionService(
                secret=self.settings.credential_encryption_secret,
                key_id=self.settings.credential_encryption_key_id,
                previous_secrets=self.settings.credential_encryption_previous_secrets,
            )
            pending = PendingToolInvocationService(session, secrets)
            invocation = pending.by_run_call(
                workspace_id=context.workspace_id, run_id=context.run_id, tool_call_id=tool_call_id
            )
            if invocation is not None:
                pending.complete_execution(invocation, tool_result_payload(response))
            return response

        # Waiting for remote RPC holds a Task, no DB connection or adapter thread.
        while True:
            completion = await self.database.run(completed)
            if completion is not None:
                return completion
            await asyncio.sleep(0.5)
