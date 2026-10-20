"""Run-owned accounting around native MCP tool effects."""

import asyncio
from typing import Any
from uuid import UUID

from sqlalchemy import select
from temporalio import activity

from opsmesh.identity.quotas.schemas import NormalizedUsage
from opsmesh.orchestration.contracts import ExecutionSubject
from opsmesh.orchestration.execution.contracts import ExecutionRequest
from opsmesh.orchestration.runs.access import load_run, lock_run_facts
from opsmesh.orchestration.runs.activities import RunActivities
from opsmesh.orchestration.runs.models import OperationReceipt, Run
from opsmesh.orchestration.runs.usage import retain_uncertain, settle_usage
from opsmesh.orchestration.runtime.access import load_environment
from opsmesh.orchestration.runtime.mcp import load_connection
from opsmesh.orchestration.runtime.models import Environment
from opsmesh.shared.errors import ConflictError, ForbiddenError
from opsmesh.shared.serialization import payload_digest


class RunMCPMeter:
    def __init__(self, runs: RunActivities) -> None:
        self.runs = runs

    def assert_connection_idle(self, session: Any, connection_id: UUID) -> None:
        active = session.scalar(
            select(OperationReceipt.operation_id)
            .where(
                OperationReceipt.connection_id == connection_id,
                OperationReceipt.accounted.is_(False),
                OperationReceipt.status.in_(("started", "uncertain")),
            )
            .limit(1)
        )
        if active is not None:
            raise ConflictError("The MCP connection has an unresolved run operation")

    async def claim(
        self, context: dict[str, Any], name: str, arguments: dict[str, Any] | None
    ) -> tuple[bool, dict[str, Any]]:
        info = activity.info()
        operation_id = f"mcp:{info.workflow_id}:{info.activity_id}"
        request = await asyncio.to_thread(self.configuration, UUID(context["run_id"]))
        if request.hooks:
            bridge = self.runs.bindings.extensions
            if bridge is None:
                raise ConflictError("Installed MCP tool hooks are not assembled")
            before = await bridge.hooks(
                run_id=UUID(context["run_id"]),
                occurrence=operation_id,
                stage="tool.before",
                payload={"arguments": arguments},
            )
            if before["payload"]["arguments"] != arguments:
                raise ConflictError("Changed MCP arguments require an explicitly approved new call")
            context["_hook_entered"] = before["entered"]
        claimed, result = await asyncio.to_thread(
            self._claim,
            {k: v for k, v in context.items() if not k.startswith("_")},
            name,
            arguments,
            operation_id,
        )
        if not claimed:
            result = await self.complete(context, result) or result
        return claimed, result

    def configuration(self, run_id: UUID) -> ExecutionRequest:
        with self.runs.sessions() as session:
            run = session.get(Run, run_id)
            if run is None:
                raise ConflictError("The MCP Run is unavailable")
            return ExecutionRequest.model_validate(
                self.runs.cipher.decrypt_payload(run.request_ciphertext)
            )

    def _claim(
        self,
        context: dict[str, Any],
        name: str,
        arguments: dict[str, Any] | None,
        operation_id: str,
    ) -> tuple[bool, dict[str, Any]]:
        r = self.runs
        with r.sessions() as session, session.begin():
            run = load_run(
                session,
                run_id=UUID(context["run_id"]),
                subject=ExecutionSubject(UUID(context["principal_id"])),
                admission=r.admission,
                lock=True,
            )
            frozen = ExecutionRequest.model_validate(
                r.cipher.decrypt_payload(run.request_ciphertext)
            )
            if frozen.configuration_source is not None or frozen.parent_invocation_id is not None:
                if r.bindings.sources is None:
                    raise ConflictError("Configuration source authorization is not assembled")
                r.bindings.sources.authorize(session, run_id=run.id)
            agent = frozen.agents[context["agent_key"]]
            connection_id = UUID(context["connection_id"])
            binding = next(
                (item for item in agent.mcp_connections if item.connection_id == connection_id),
                None,
            )
            if (
                binding is None
                or name not in binding.allowed_tools
                or binding.tools_digest != context["tools_digest"]
            ):
                raise ForbiddenError("The MCP connection is outside the frozen Agent configuration")
            connection = load_connection(
                session,
                connection_id=connection_id,
                principal_id=run.principal_id,
                admission=r.admission,
                shared_use=True,
            )
            # Close claims the same row before checking unresolved effects;
            # serialize tool admission with that lifecycle transition.
            session.refresh(connection, with_for_update=True)
            if connection.scope_id != run.scope_id or connection.owner_ref != run.owner_ref:
                raise ForbiddenError("The MCP connection belongs to a different execution scope")
            if connection.status != "ready":
                raise ConflictError("The MCP connection is no longer ready")
            fingerprint = payload_digest({"context": context, "tool": name, "arguments": arguments})
            previous = session.get(OperationReceipt, (run.scope_id, operation_id))
            if previous is not None:
                if (
                    previous.fingerprint != fingerprint
                    or previous.status != "completed"
                    or not previous.accounted
                ):
                    raise ConflictError("The MCP operation cannot be repeated")
                return False, r.cipher.decrypt_payload(previous.result_ciphertext or "")
            if run.status != "running" or (run.capacity_reservation_id is None):
                raise ConflictError("The Run is not admitted for MCP execution")
            environment = None
            if connection.transport == "stdio":
                if connection.environment_id is None:
                    raise ConflictError("The stdio MCP has no explicit environment")
                environment = load_environment(
                    session,
                    environment_id=connection.environment_id,
                    subject=ExecutionSubject(run.principal_id),
                    admission=r.admission,
                    lock=True,
                    shared_use=True,
                )
                if environment.status != "ready" or environment.active_operation_id is not None:
                    raise ConflictError("The stdio MCP environment is busy")
                if environment.scope_id != run.scope_id or environment.owner_ref != run.owner_ref:
                    raise ForbiddenError("The stdio MCP environment belongs to another scope")
            session.add(
                OperationReceipt(
                    scope_id=run.scope_id,
                    operation_id=operation_id,
                    run_id=run.id,
                    fingerprint=fingerprint,
                    kind="mcp",
                    connection_id=connection.id,
                    environment_id=environment.id if environment is not None else None,
                    status="started",
                    accounted=False,
                )
            )
            if environment is not None:
                environment.active_operation_id = operation_id
        return True, {}

    async def complete(
        self, context: dict[str, Any], result: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        info = activity.info()
        await asyncio.to_thread(
            self._complete,
            UUID(context["run_id"]),
            f"mcp:{info.workflow_id}:{info.activity_id}",
            result,
        )
        if "_hook_entered" in context:
            bridge = self.runs.bindings.extensions
            assert bridge is not None
            stage = "tool.after" if result is not None else "on_error"
            answer = await bridge.hooks(
                run_id=UUID(context["run_id"]),
                occurrence=f"mcp:{info.workflow_id}:{info.activity_id}",
                stage=stage,
                payload={"result": result} if result is not None else {"error_code": "mcp_failed"},
                entered=context["_hook_entered"],
            )
            await bridge.hooks(
                run_id=UUID(context["run_id"]),
                occurrence=f"mcp:{info.workflow_id}:{info.activity_id}",
                stage="finalize",
                payload={"status": "completed" if result is not None else "unknown"},
                entered=answer["entered"],
            )
            if result is not None:
                result = dict(answer["payload"]["result"])
        return result

    def _complete(self, run_id: UUID, operation_id: str, result: dict[str, Any] | None) -> None:
        r = self.runs
        with r.sessions() as session, session.begin():
            run = lock_run_facts(session, run_id)
            receipt = session.get(OperationReceipt, (run.scope_id, operation_id))
            if receipt is None:
                raise ConflictError("The MCP operation disappeared")
            if receipt.accounted:
                return
            environment = (
                session.scalar(
                    select(Environment)
                    .where(Environment.id == receipt.environment_id)
                    .with_for_update()
                )
                if receipt.environment_id
                else None
            )
            if result is None:
                retain_uncertain(session, run=run, receipt=receipt)
                if environment is not None:
                    environment.status = "needs_attention"
            else:
                receipt.result_ciphertext = r.cipher.encrypt_payload(result).ciphertext
                settle_usage(
                    session,
                    run=run,
                    receipt=receipt,
                    usage=NormalizedUsage(tool_invocations=1),
                    bindings=r.bindings,
                )
                receipt.status = "completed"
                if environment is not None:
                    environment.active_operation_id = None
