"""Blocking product/MCP adapters own a Session only for one tool operation."""

import asyncio
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.agents.execution.contracts import AgentRuntimeContext, AgentRuntimeToolResult
from backend.app.agents.execution.errors import AgentRuntimeCancelledError
from backend.app.agents.execution.tools.executor import BackendToolExecutor, tool_result_payload
from backend.app.identity.authorization.execution import ExecutionIdentityService
from backend.app.identity.authorization.resource_queries import execution_resource_queries
from backend.app.orchestration.approvals.pending_tools import PendingToolInvocationService
from backend.app.orchestration.runs.models import AgentRun
from backend.app.runtime.instances.contracts import DockerRuntimeClient
from backend.app.runtime.self_hosted.models import SelfHostedMcpJob
from backend.app.shared.config import Settings
from backend.app.shared.db.operations import DatabaseOperations
from backend.app.shared.security.secrets import SecretEncryptionService


@dataclass(frozen=True)
class ScopedToolExecutor:
    database: DatabaseOperations
    settings: Settings
    docker_client: DockerRuntimeClient | None

    def _authorize(self, session: Session, context: AgentRuntimeContext) -> None:
        status = session.scalar(
            select(AgentRun.status).where(
                AgentRun.workspace_id == context.workspace_id, AgentRun.id == context.run_id
            )
        )
        if status is None or status == "cancelled":
            raise AgentRuntimeCancelledError

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

    async def review_tool_call(
        self, *, context: AgentRuntimeContext, tool_name: str, arguments: dict[str, object]
    ) -> dict[str, object]:
        def operation(session: Session) -> dict[str, object]:
            self._authorize(session, context)
            user = ExecutionIdentityService(session).for_run(context.workspace_id, context.run_id)
            with execution_resource_queries(session, context.workspace_id, user):
                return asyncio.run(
                    self._executor(session).review_tool_call(
                        context=context, tool_name=tool_name, arguments=arguments
                    )
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
        def operation(session: Session) -> AgentRuntimeToolResult:
            self._authorize(session, context)
            user = ExecutionIdentityService(session).for_run(context.workspace_id, context.run_id)
            with execution_resource_queries(session, context.workspace_id, user):
                return asyncio.run(
                    self._executor(session).execute_tool(
                        context=context,
                        tool_name=tool_name,
                        arguments=arguments,
                        tool_call_id=tool_call_id,
                        approval_granted=approval_granted,
                    )
                )

        result = await self.database.run(operation)
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
            response = await self.database.run(completed)
            if response is not None:
                return response
            await asyncio.sleep(0.5)
