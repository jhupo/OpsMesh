"""Native sandbox tool I/O with frozen commands and accountable effect receipts."""

import asyncio
import json
from typing import Any

from jsonschema import Draft202012Validator
from sqlalchemy import select
from temporalio import activity

from opsmesh.identity.quotas.schemas import NormalizedUsage
from opsmesh.orchestration.contracts import ExecutionSubject
from opsmesh.orchestration.execution.contracts import ExecutionRequest, ToolInvocation
from opsmesh.orchestration.runs.access import load_run, lock_run_facts
from opsmesh.orchestration.runs.activities import RunActivities
from opsmesh.orchestration.runs.hooks import finish_hooks
from opsmesh.orchestration.runs.models import OperationReceipt, Run
from opsmesh.orchestration.runs.usage import retain_uncertain, settle_usage
from opsmesh.orchestration.runtime.access import load_environment
from opsmesh.orchestration.runtime.models import Environment
from opsmesh.orchestration.runtime.sandbox import SandboxBackend, SandboxCommandCancelled
from opsmesh.shared.errors import ConflictError, ForbiddenError
from opsmesh.shared.policies import enforce_operation_policy
from opsmesh.shared.serialization import payload_digest


class ToolActivities:
    def __init__(self, runs: RunActivities, backend: SandboxBackend) -> None:
        self.runs = runs
        self.backend = backend

    def configuration(self, invocation: ToolInvocation) -> tuple[ExecutionRequest, Any]:
        with self.runs.sessions() as session:
            run = session.get(Run, invocation.run_id)
            if run is None:
                raise ConflictError("The Run does not exist")
            request = ExecutionRequest.model_validate(
                self.runs.cipher.decrypt_payload(run.request_ciphertext)
            )
            tool = next(
                value
                for value in request.agents[invocation.agent_key].tools
                if value.name == invocation.tool_name
            )
            return request, tool

    def claim(self, invocation: ToolInvocation) -> tuple[bool, dict[str, Any], list[str]]:
        r = self.runs
        operation_id = f"tool:{invocation.run_id}:{invocation.call_id}"
        with r.sessions() as session, session.begin():
            prior = session.get(Run, invocation.run_id)
            if prior is None:
                raise ConflictError("The Run does not exist")
            run = load_run(
                session,
                run_id=prior.id,
                subject=ExecutionSubject(prior.principal_id),
                admission=r.admission,
                lock=True,
            )
            request = ExecutionRequest.model_validate(
                r.cipher.decrypt_payload(run.request_ciphertext)
            )
            if request.configuration_source is not None or request.parent_invocation_id is not None:
                if r.bindings.sources is None:
                    raise ConflictError("Configuration source authorization is not assembled")
                r.bindings.sources.authorize(session, run_id=run.id)
            tool = next(
                (
                    tool
                    for tool in request.agents[invocation.agent_key].tools
                    if tool.name == invocation.tool_name
                ),
                None,
            )
            if (
                tool is None
                or (tool.extension is None and request.environment_id is None)
                or run.status != "running"
            ):
                raise ConflictError("The sandbox tool is not admitted")
            if run.capacity_reservation_id is None:
                raise ConflictError("The Run has no active execution capacity")
            Draft202012Validator(tool.input_schema).validate(invocation.arguments)
            fingerprint = payload_digest(invocation.model_dump(mode="json"))
            receipt = session.get(OperationReceipt, (run.scope_id, operation_id))
            if receipt is not None:
                if receipt.fingerprint != fingerprint:
                    raise ConflictError("Tool identity was reused with different input")
                if receipt.status != "completed" or not receipt.accounted:
                    raise ConflictError("The tool needs reconciliation")
                return False, r.cipher.decrypt_payload(receipt.result_ciphertext or ""), []
            enforce_operation_policy(
                session,
                action="tool.invoke",
                name=tool.name,
                approval_configured=tool.approval_required,
            )
            if tool.extension is not None:
                if r.bindings.extensions is None:
                    raise ConflictError("Installed tool execution is not assembled")
                from opsmesh.orchestration.contracts import ExecutionScope

                r.bindings.extensions.validate(
                    session,
                    principal_id=run.principal_id,
                    scope=ExecutionScope(
                        configuration_id=None
                        if run.owner_ref.startswith("user:")
                        else run.scope_id,
                    ),
                    configuration=request.model_dump(mode="json"),
                )
                session.add(
                    OperationReceipt(
                        scope_id=run.scope_id,
                        operation_id=operation_id,
                        run_id=run.id,
                        fingerprint=fingerprint,
                        kind="tool",
                        status="started",
                        accounted=False,
                    )
                )
                return True, {"extension": tool.extension.model_dump(mode="json")}, []
            assert request.environment_id is not None
            environment = load_environment(
                session,
                environment_id=request.environment_id,
                subject=ExecutionSubject(run.principal_id),
                admission=r.admission,
                lock=True,
                shared_use=True,
            )
            if environment.status != "ready" or environment.active_operation_id is not None:
                raise ConflictError("The environment is busy or needs reconciliation")
            if environment.scope_id != run.scope_id or environment.owner_ref != run.owner_ref:
                raise ForbiddenError("The environment belongs to another scope")
            session.add(
                OperationReceipt(
                    scope_id=run.scope_id,
                    operation_id=operation_id,
                    run_id=run.id,
                    fingerprint=fingerprint,
                    kind="tool",
                    environment_id=environment.id,
                    status="started",
                    accounted=False,
                )
            )
            environment.active_operation_id = operation_id
            return (
                True,
                r.cipher.decrypt_payload(environment.encrypted_state or ""),
                (tool.command + [json.dumps(invocation.arguments, ensure_ascii=True)]),
            )

    def complete(self, invocation: ToolInvocation, result: dict[str, object] | None) -> None:
        r = self.runs
        operation_id = f"tool:{invocation.run_id}:{invocation.call_id}"
        with r.sessions() as session, session.begin():
            run = lock_run_facts(session, invocation.run_id)
            receipt = session.get(OperationReceipt, (run.scope_id, operation_id))
            if receipt is None:
                raise ConflictError("The tool operation disappeared")
            if receipt.accounted:
                return
            environment = session.scalar(
                select(Environment)
                .where(Environment.id == receipt.environment_id)
                .with_for_update()
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

    @activity.defn(name="orchestration.tool")
    async def invoke(self, invocation: ToolInvocation) -> dict[str, Any]:
        request, tool = await asyncio.to_thread(self.configuration, invocation)
        bridge = self.runs.bindings.extensions
        entered: list[str] = []
        occurrence = f"tool:{invocation.run_id}:{invocation.call_id}"
        claimed = False
        failed = False
        try:
            if request.hooks:
                if bridge is None:
                    raise ConflictError("Installed tool hooks are not assembled")
                before = await bridge.hooks(
                    run_id=invocation.run_id,
                    occurrence=occurrence,
                    stage="tool.before",
                    payload={"arguments": invocation.arguments},
                )
                entered = list(before["entered"])
                arguments = before["payload"]["arguments"]
                if tool.approval_required and arguments != invocation.arguments:
                    raise ConflictError(
                        "Patched tool arguments require an explicitly approved new call"
                    )
                invocation = ToolInvocation.model_validate(
                    {**invocation.model_dump(), "arguments": arguments}
                )
            claimed, payload, command = await asyncio.to_thread(self.claim, invocation)
            if not claimed:
                result = payload
            elif "extension" in payload:
                assert self.runs.bindings.extensions is not None
                result = await self.runs.bindings.extensions.invoke(
                    run_id=invocation.run_id,
                    operation_id=f"tool:{invocation.run_id}:{invocation.call_id}",
                    reference=payload["extension"],
                    arguments=invocation.arguments,
                )
            else:
                native = await self.backend.resume(payload)
                result = await self.backend.execute(native, "command", {"command": command})
            if claimed:
                await asyncio.to_thread(self.complete, invocation, result)
            if request.hooks:
                assert bridge is not None
                after = await bridge.hooks(
                    run_id=invocation.run_id,
                    occurrence=occurrence,
                    stage="tool.after",
                    payload={"result": result},
                    entered=entered,
                )
                entered[:] = after["entered"]
                result = dict(after["payload"]["result"])
            return result
        except SandboxCommandCancelled as exc:
            failed = True
            await asyncio.shield(asyncio.to_thread(self.complete, invocation, exc.result))
            raise
        except BaseException:
            failed = True
            if claimed:
                await asyncio.shield(asyncio.to_thread(self.complete, invocation, None))
            raise
        finally:
            await finish_hooks(
                self.runs.bindings,
                run_id=invocation.run_id,
                occurrence=occurrence,
                entered=entered,
                failed=failed,
            )
