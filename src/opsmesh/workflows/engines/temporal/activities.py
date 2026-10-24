"""Authorized Temporal I/O for workflow records and independently owned Agent runs."""

from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from opsmesh_sdk.identity import InvocationSource
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from temporalio import activity

from opsmesh.execution.runs.models import ExecutionRun
from opsmesh.execution.runs.schemas import RunCreate
from opsmesh.execution.runs.service import request_cancellation, submit_run
from opsmesh.shared.errors import ConflictError, ForbiddenError
from opsmesh.shared.security.encryption import SecretCipher
from opsmesh.shared.serialization import payload_digest
from opsmesh.users.service import load_identity
from opsmesh.workflows.bindings import ToolAdapter, authorize_bindings
from opsmesh.workflows.contracts import AgentNode, PreparedWorkflow, ToolNode
from opsmesh.workflows.engines.temporal.execution_dispatch import dispatch_cancellation
from opsmesh.workflows.instances.access import get_instance
from opsmesh.workflows.runs.access import authorize_run
from opsmesh.workflows.runs.events import append_event
from opsmesh.workflows.runs.models import WorkflowNodeRun, WorkflowSignal
from opsmesh.workflows.runs.nodes import resolve_node
from opsmesh.workflows.runs.operations import OperationLedger
from opsmesh.workflows.runs.service import finish_run, get_workflow_run
from opsmesh.workflows.schema import validate_json
from opsmesh.workflows.values import validate_value


class WorkflowActivities:
    def __init__(
        self, sessions: sessionmaker[Session], cipher: SecretCipher, *, tools: ToolAdapter
    ) -> None:
        self.sessions, self.cipher, self.tools = sessions, cipher, tools

    def identity(self, value: str) -> UUID:
        run_id = UUID(value)
        if activity.info().workflow_id != f"workflow:{run_id}":
            raise ForbiddenError("The Activity does not belong to this workflow run")
        return run_id

    def node(self, session: Session, run_id: UUID, path: str) -> WorkflowNodeRun:
        node = session.scalar(
            select(WorkflowNodeRun)
            .where(WorkflowNodeRun.run_id == run_id, WorkflowNodeRun.path == path)
            .with_for_update()
        )
        if node is None:
            raise ConflictError("The workflow node has not been admitted")
        return node

    def check_arguments(self, node: WorkflowNodeRun, arguments: object) -> None:
        if payload_digest({"kind": node.kind, "arguments": arguments}) != node.fingerprint:
            raise ForbiddenError("The invocation differs from the admitted node arguments")

    @activity.defn(name="workflows.admit")
    async def admit(self, command: dict[str, Any]) -> dict[str, Any]:
        run_id = self.identity(command["run_id"])
        with self.sessions.begin() as session:
            row = get_workflow_run(session, run_id=run_id, lock=True)
            if row.status in {"cancelled", "cancelling"}:
                return {"cancelled": True}
            if row.status not in {"queued", "running"}:
                raise ConflictError("The workflow cannot be admitted")
            actor = load_identity(session, row.user_id)
            if actor is None:
                raise ConflictError("The workflow user is unavailable")
            instance = get_instance(session, actor=actor, instance_id=row.instance_id)
            if instance.status != "active":
                raise ConflictError("The workflow instance is disabled")
            prepared = PreparedWorkflow.model_validate(
                self.cipher.decrypt_payload(instance.encrypted_prepared)
            )
            authorize_bindings(session, actor=actor, prepared=prepared)
            if row.status == "queued":
                row.status = "running"
                append_event(session, run_id=row.id, kind="running", payload={}, cipher=self.cipher)
            request = self.cipher.decrypt_payload(row.encrypted_input)
            return {"prepared": prepared.model_dump(mode="json"), "input": request["input"]}

    @activity.defn(name="workflows.node_begin")
    async def node_begin(self, command: dict[str, Any]) -> None:
        run_id = self.identity(command["run_id"])
        with self.sessions.begin() as session:
            _, _, prepared = authorize_run(session, run_id, self.cipher, lock=True)
            declared = resolve_node(prepared, command["path"])
            if declared.kind != command["kind"]:
                raise ForbiddenError("The node kind differs from its frozen definition")
            fingerprint = payload_digest({"kind": declared.kind, "arguments": command["arguments"]})
            previous = session.scalar(
                select(WorkflowNodeRun).where(
                    WorkflowNodeRun.run_id == run_id, WorkflowNodeRun.path == command["path"]
                )
            )
            if previous is not None:
                if previous.fingerprint != fingerprint:
                    raise ConflictError("The node identity was reused with different input")
                return
            node = WorkflowNodeRun(
                run_id=run_id,
                path=command["path"],
                kind=declared.kind,
                fingerprint=fingerprint,
                status="running",
                encrypted_input=self.cipher.encrypt_payload(
                    {"arguments": command["arguments"]}
                ).ciphertext,
            )
            session.add(node)
            session.flush()
            append_event(
                session,
                run_id=run_id,
                kind="node_running",
                payload={"node_id": str(node.id), "path": node.path},
                cipher=self.cipher,
            )

    @activity.defn(name="workflows.node_finish")
    async def node_finish(self, command: dict[str, Any]) -> None:
        run_id = self.identity(command["run_id"])
        if command["status"] not in {
            "completed",
            "failed",
            "cancelled",
            "needs_attention",
            "skipped",
        }:
            raise ConflictError("Invalid terminal node status")
        with self.sessions.begin() as session:
            get_workflow_run(session, run_id=run_id, lock=True)
            node = self.node(session, run_id, command["path"])
            if node.status in {"completed", "skipped", "cancelled", "failed", "needs_attention"}:
                return
            node.status = command["status"]
            node.error_code = (
                "external_effect_unknown"
                if node.status == "needs_attention"
                else "node_failed"
                if node.status == "failed"
                else None
            )
            validate_json({"output": command.get("output")}, max_bytes=4_000_000)
            node.encrypted_result = self.cipher.encrypt_payload(
                {"output": command.get("output")}
            ).ciphertext
            append_event(
                session,
                run_id=run_id,
                kind="node_" + node.status,
                payload={"node_id": str(node.id), "path": node.path},
                cipher=self.cipher,
            )

    @activity.defn(name="workflows.agent_submit")
    async def agent_submit(self, command: dict[str, Any]) -> dict[str, str]:
        run_id = self.identity(command["run_id"])
        with self.sessions.begin() as session:
            parent, actor, prepared = authorize_run(session, run_id, self.cipher, lock=True)
            declared = resolve_node(prepared, command["path"])
            if not isinstance(declared, AgentNode):
                raise ForbiddenError("The node is not an Agent invocation")
            binding = prepared.bindings.agents[declared.binding]
            node = self.node(session, run_id, command["path"])
            self.check_arguments(node, command["input"])
            if node.execution_run_id is not None:
                return {"execution_id": str(node.execution_run_id)}
            from opsmesh.workflows.runs.sessions import conversation_session

            source = (
                InvocationSource.model_validate(
                    self.cipher.decrypt_payload(parent.encrypted_context)
                )
                if parent.encrypted_context
                else InvocationSource()
            )
            value = command["input"]
            if declared.input_mode == "messages":
                if set(value) != {"messages"} or not isinstance(value["messages"], list):
                    raise ConflictError("Message-mode Agent nodes require only a messages array")
                from opsmesh.execution.runner import native_input

                value = native_input(value["messages"])
            request = RunCreate(
                instance_id=binding.instance_id,
                session_id=conversation_session(
                    session,
                    actor=actor,
                    source=source,
                    workflow_instance_id=parent.instance_id,
                    binding_name=declared.binding,
                    binding=binding,
                ),
                driver="temporal",
                request_key="workflow:" + str(uuid5(NAMESPACE_URL, f"{run_id}:{node.path}")),
                input=value,
                max_turns=binding.max_turns,
                timeout_seconds=binding.timeout_seconds,
            )
            execution = submit_run(
                session,
                actor=actor,
                request=request,
                cipher=self.cipher,
                source=source,
            )
            node.execution_run_id = execution.id
            return {"execution_id": str(execution.id)}

    @activity.defn(name="workflows.agent_result")
    async def agent_result(self, command: dict[str, Any]) -> dict[str, object]:
        run_id = self.identity(command["run_id"])
        with self.sessions.begin() as session:
            get_workflow_run(session, run_id=run_id)
            node = self.node(session, run_id, command["path"])
            row = (
                session.get(ExecutionRun, node.execution_run_id) if node.execution_run_id else None
            )
            if row is None:
                raise ConflictError("The linked Agent run does not exist")
            payload = (
                self.cipher.decrypt_payload(row.encrypted_result) if row.encrypted_result else {}
            )
            return {"status": row.status, "output": payload.get("output")}

    @activity.defn(name="workflows.agent_cancel")
    async def agent_cancel(self, command: dict[str, Any]) -> None:
        run_id = self.identity(command["run_id"])
        with self.sessions.begin() as session:
            row = get_workflow_run(session, run_id=run_id)
            node = self.node(session, run_id, command["path"])
            if node.execution_run_id is None:
                return
            linked = session.get(ExecutionRun, node.execution_run_id, with_for_update=True)
            if linked is None or linked.user_id != row.user_id:
                raise ForbiddenError("The linked Agent run belongs to another user")
            cancelled = request_cancellation(session, row=linked, cipher=self.cipher)
            dispatch_cancellation(session, cancelled)

    @activity.defn(name="workflows.tool_prepare")
    async def tool_prepare(self, command: dict[str, Any]) -> dict[str, object]:
        run_id = self.identity(command["run_id"])
        with self.sessions.begin() as session:
            parent, actor, prepared = authorize_run(session, run_id, self.cipher)
            source = InvocationSource.model_validate(
                self.cipher.decrypt_payload(parent.encrypted_context)
                if parent.encrypted_context
                else {}
            )
            declared = resolve_node(prepared, command["path"])
            if not isinstance(declared, ToolNode):
                raise ForbiddenError("The node is not a tool invocation")
            binding = prepared.bindings.tools[declared.binding]
            if declared.operation not in binding.allowed_operations:
                raise ForbiddenError("The operation is not permitted by this binding")
            node = self.node(session, run_id, command["path"])
            node_id = node.id
            existing = node.encrypted_descriptor
            self.check_arguments(node, command["arguments"])
            if existing:
                descriptor = self.cipher.decrypt_payload(existing)
                requires = binding.require_approval or descriptor.get("requires_approval", True)
                return {
                    "approved": not requires or node.approval_decision is True,
                    "denied": node.approval_decision is False,
                }
        descriptor = await self.tools.describe(actor, binding, declared.operation, source=source)
        validate_json(descriptor)
        parameters = descriptor.get("parameters", {})
        if not isinstance(parameters, dict):
            raise ConflictError("The tool input schema must be an object")
        validate_value(command["arguments"], parameters)
        with self.sessions.begin() as session:
            authorize_run(session, run_id, self.cipher, lock=True)
            node = self.node(session, run_id, command["path"])
            if existing and self.cipher.decrypt_payload(existing) != descriptor:
                raise ConflictError("The tool descriptor changed after admission")
            node.encrypted_descriptor = self.cipher.encrypt_payload(descriptor).ciphertext
            requires = binding.require_approval or descriptor.get("requires_approval", True)
            if requires and node.approval_id is None:
                node.approval_id, node.status = uuid4(), "awaiting_approval"
                append_event(
                    session,
                    run_id=run_id,
                    kind="node_awaiting_approval",
                    payload={"node_id": str(node_id), "approval_id": str(node.approval_id)},
                    cipher=self.cipher,
                )
            return {
                "approved": not requires or node.approval_decision is True,
                "denied": node.approval_decision is False,
            }

    @activity.defn(name="workflows.tool")
    async def tool(self, command: dict[str, Any]) -> object:
        run_id = self.identity(command["run_id"])
        with self.sessions.begin() as session:
            parent, actor, prepared = authorize_run(session, run_id, self.cipher, lock=True)
            declared = resolve_node(prepared, command["path"])
            if not isinstance(declared, ToolNode):
                raise ForbiddenError("The node is not a tool invocation")
            binding = prepared.bindings.tools[declared.binding]
            node = self.node(session, run_id, command["path"])
            if node.encrypted_descriptor is None:
                raise ConflictError("The tool has not been prepared")
            self.check_arguments(node, command["arguments"])
            descriptor = self.cipher.decrypt_payload(node.encrypted_descriptor)
            if (
                binding.require_approval or descriptor.get("requires_approval", True)
            ) and node.approval_decision is not True:
                raise ForbiddenError("The tool call has not been approved")
            node_id = node.id
            node.status = "running"
            source = InvocationSource.model_validate(
                self.cipher.decrypt_payload(parent.encrypted_context)
                if parent.encrypted_context
                else {}
            )
        current = await self.tools.describe(actor, binding, declared.operation, source=source)
        if current != descriptor:
            raise ConflictError("The tool descriptor changed after approval")
        parameters = current.get("parameters", {})
        if not isinstance(parameters, dict):
            raise ConflictError("The tool input schema must be an object")
        validate_value(command["arguments"], parameters)
        operation_id = f"workflow:{run_id}:tool:{node_id}"
        await self.tools.authorize(actor, binding, declared.operation, command["arguments"], source)
        ledger = OperationLedger(self.sessions, self.cipher, run_id)
        cached = ledger.claim(
            operation_id,
            node_id,
            {
                "descriptor": descriptor,
                "binding": binding.model_dump(mode="json"),
                "arguments": command["arguments"],
            },
        )
        if cached is not None:
            return cached["output"]
        try:
            value = await self.tools.invoke(
                actor,
                binding,
                declared.operation,
                command["arguments"],
                uuid5(NAMESPACE_URL, operation_id),
                descriptor,
                source=source,
            )
            ledger.complete(operation_id, value)
            return value
        except BaseException:
            ledger.uncertain(operation_id)
            raise

    @activity.defn(name="workflows.signal_poll")
    async def signal_poll(self, command: dict[str, Any]) -> dict[str, object]:
        run_id = self.identity(command["run_id"])
        with self.sessions.begin() as session:
            authorize_run(session, run_id, self.cipher, lock=True)
            signal = session.scalar(
                select(WorkflowSignal)
                .where(
                    WorkflowSignal.run_id == run_id,
                    WorkflowSignal.path == command["path"],
                    WorkflowSignal.name == command["name"],
                )
                .with_for_update()
            )
            if signal is None:
                return {"ready": False}
            signal.status = "consumed"
            return {"ready": True, **self.cipher.decrypt_payload(signal.encrypted_payload)}

    @activity.defn(name="workflows.finish")
    async def finish(self, command: dict[str, Any]) -> str:
        run_id = self.identity(command["run_id"])
        with self.sessions.begin() as session:
            row = get_workflow_run(session, run_id=run_id, lock=True)
            # An Activity response can be lost after agent_submit commits. Close
            # those orphaned admissions as well as children cancelled by the graph.
            linked = session.scalars(
                select(ExecutionRun)
                .join(WorkflowNodeRun, WorkflowNodeRun.execution_run_id == ExecutionRun.id)
                .where(
                    WorkflowNodeRun.run_id == run_id,
                    ExecutionRun.status.in_(("queued", "running", "interrupted", "cancelling")),
                )
                .with_for_update(of=ExecutionRun)
            ).all()
            for execution in linked:
                if execution.user_id != row.user_id:
                    raise ForbiddenError("The linked Agent run belongs to another user")
                cancelled = request_cancellation(session, row=execution, cipher=self.cipher)
                dispatch_cancellation(session, cancelled)
            session.flush()
            return finish_run(
                session,
                row=row,
                status=command["status"],
                output=command.get("output"),
                cipher=self.cipher,
            ).status
