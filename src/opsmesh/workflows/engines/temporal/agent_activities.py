"""Temporal I/O boundaries for persistent instances; core execution owns the records."""

import inspect
import json
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any
from uuid import UUID

from agents import FunctionTool, Model, ModelProvider, ModelResponse, RunContextWrapper
from agents.guardrail import InputGuardrail, OutputGuardrail
from agents.items import TResponseStreamEvent
from agents.mcp import MCPServer
from agents.prompts import GenerateDynamicPromptData
from agents.run_context import AgentHookContext
from agents.sandbox import SandboxAgent
from agents.tool_context import ToolContext
from agents.usage import Usage
from mcp.types import (
    CallToolResult,
    GetPromptResult,
    ListPromptsResult,
    ListResourcesResult,
    ListResourceTemplatesResult,
    ReadResourceResult,
    Tool,
)
from opsmesh_sdk.identity import Principal
from pydantic import TypeAdapter
from sqlalchemy.orm import Session, sessionmaker
from temporalio import activity

from opsmesh.agents.assembly import AssemblyBindings
from opsmesh.agents.config.mcp import MCPServerRef
from opsmesh.agents.config.model import ModelRef
from opsmesh.agents.providers.factory import create_model
from opsmesh.execution.bindings import freeze_bindings
from opsmesh.execution.context import ExecutionContext
from opsmesh.execution.contracts import HistoryCall, PreparedAgent
from opsmesh.execution.mcp import AuthorizedMCP
from opsmesh.execution.model import ExecutionModel
from opsmesh.execution.runner import prepared_agent
from opsmesh.execution.runs.access import authorize_run
from opsmesh.execution.runs.events import append_event
from opsmesh.execution.runs.executor import BindingsFactory
from opsmesh.execution.runs.models import ExecutionRun
from opsmesh.execution.runs.operations import OperationLedger
from opsmesh.execution.runs.schemas import RunCreate
from opsmesh.execution.runs.service import AdmissionPending, claim_run, finish_run, release_run
from opsmesh.execution.runtime.sandbox import AgentSandboxClient, AgentSandboxState
from opsmesh.execution.runtime.schemas import RuntimeCommand
from opsmesh.execution.runtime.service import SandboxOperations
from opsmesh.execution.sessions.storage import NativeHistory
from opsmesh.shared.errors import ConflictError, ForbiddenError
from opsmesh.shared.security.encryption import SecretCipher
from opsmesh.temporal.codec import EncryptedPayloadCodec
from opsmesh.users.quotas.service import CapacityExceededError
from opsmesh.workflows.engines.temporal.agent_bindings import MODEL_PREFIX, describe_resources
from opsmesh.workflows.engines.temporal.agent_workflow import InstanceAdmission
from opsmesh.workflows.engines.temporal.callbacks import CallbackDescription, describe_callbacks


class InstanceActivities:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        cipher: SecretCipher,
        *,
        bindings: BindingsFactory,
        history_root: Path,
        sandbox: SandboxOperations,
    ) -> None:
        self.sessions, self.cipher, self.bindings = sessions, cipher, bindings
        self.history_root, self.sandbox = history_root, sandbox

    def context(
        self, run_id: UUID, attempt_id: str | None = None
    ) -> tuple[ExecutionContext, PreparedAgent]:
        if activity.info().workflow_id != f"instance:{run_id}":
            raise ForbiddenError("The Activity does not belong to this instance run")
        with self.sessions.begin() as session:
            row = authorize_run(session, run_id)
            if (
                row.instance_id is None
                or row.attempt_id is None
                or (attempt_id is not None and row.attempt_id != attempt_id)
            ):
                raise ConflictError("The Activity refers to a different execution attempt")
            context = ExecutionContext(row.id, row.user_id, row.instance_id, row.attempt_id)
            prepared = PreparedAgent.model_validate(
                self.cipher.decrypt_payload(row.encrypted_prepared or "")
            )
            from opsmesh.execution.instances.access import current_user

            actor = current_user(session, row.user_id)
            context = ExecutionContext(
                row.id,
                row.user_id,
                row.instance_id,
                row.attempt_id,
                Principal(user_id=actor.user_id, roles=actor.platform_roles),
                prepared.source,
            )
            return context, prepared

    async def source(self, context: ExecutionContext, prepared: PreparedAgent) -> AssemblyBindings:
        source = self.bindings(context, prepared)
        return await source if inspect.isawaitable(source) else source

    @activity.defn(name="execution.instance_claim")
    async def claim(self, command: dict[str, str]) -> dict[str, Any]:
        run_id = UUID(command["execution_id"])
        if activity.info().workflow_id != f"instance:{run_id}":
            raise ForbiddenError("The dispatch does not belong to this Workflow")
        try:
            with self.sessions.begin() as session:
                row = session.get(ExecutionRun, run_id)
                if row is not None and row.status == "cancelled":
                    return {"cancelled": True}
                context, prepared, value, state, decision = claim_run(
                    session,
                    run_id=run_id,
                    driver="temporal",
                    cipher=self.cipher,
                    preserve_attempt=True,
                )
                row = session.get(ExecutionRun, run_id)
                assert row is not None
                request = RunCreate.model_validate(self.cipher.decrypt_payload(row.encrypted_input))
        except (AdmissionPending, CapacityExceededError):
            return {"waiting": True}
        except Exception:
            with self.sessions.begin() as session:
                row = session.get(ExecutionRun, run_id, with_for_update=True)
                if row is not None and row.status == "queued":
                    row.status, row.error_code = "failed", "admission_failed"
                    release_run(session, row, release_environment=True)
                    append_event(
                        session, run_id=row.id, kind="failed", payload={}, cipher=self.cipher
                    )
            raise
        try:
            source = await self.source(context, prepared)
            prepared = freeze_bindings(
                source, OperationLedger(self.sessions, self.cipher, context), prepared
            )
            resources = await describe_resources(source, prepared, context)
            result = InstanceAdmission(
                context=context,
                prepared=prepared,
                input=value,
                resources=resources,
                state=state,
                decision=decision,
                timeout_seconds=request.timeout_seconds,
            ).model_dump(mode="json")
            await EncryptedPayloadCodec(self.cipher).encode(
                activity.payload_converter().to_payloads([result])
            )
            return result
        except BaseException:
            with self.sessions.begin() as session:
                finish_run(
                    session,
                    context=context,
                    status="failed",
                    cipher=self.cipher,
                    error_code="resource_preparation_failed",
                )
            raise

    @activity.defn(name="execution.instance_finish")
    async def finish(self, command: dict[str, Any]) -> None:
        run_id = UUID(command["run_id"])
        if activity.info().workflow_id != f"instance:{run_id}":
            raise ForbiddenError("The completion belongs to another Workflow")
        with self.sessions.begin() as session:
            row = session.get(ExecutionRun, run_id)
            if row is None or row.instance_id is None:
                raise ConflictError("The run does not exist")
            context = ExecutionContext(row.id, row.user_id, row.instance_id, command["attempt_id"])
            finish_run(
                session,
                context=context,
                status=command["status"],
                cipher=self.cipher,
                output=command.get("output"),
                native_state=command.get("native_state"),
                pending=command.get("pending"),
                error_code=command.get("error_code"),
            )

    @activity.defn(name="execution.instance_decision")
    async def decision(self, run_id: str) -> dict[str, object]:
        identifier = UUID(run_id)
        if activity.info().workflow_id != f"instance:{identifier}":
            raise ForbiddenError("The approval belongs to another Workflow")
        with self.sessions.begin() as session:
            row = session.get(ExecutionRun, identifier)
            if row is None:
                return {"status": "cancelled"}
            return {
                "status": row.status,
                "decision": row.approval_decision,
                "approval_id": str(row.approval_id),
            }

    @activity.defn(name="execution.instance_abandon")
    async def abandon(self, command: dict[str, str]) -> None:
        run_id = UUID(command["run_id"])
        if activity.info().workflow_id != f"instance:{run_id}":
            raise ForbiddenError("The abandoned run belongs to another Workflow")
        if command["status"] not in {"failed", "cancelled"}:
            raise ConflictError("Only a failed or cancelled Workflow may abandon its run")
        with self.sessions.begin() as session:
            row = session.get(ExecutionRun, run_id, with_for_update=True)
            if row is None or row.driver != "temporal" or row.instance_id is None:
                raise ConflictError("The run does not belong to this execution adapter")
            if row.status not in {"queued", "running", "interrupted", "cancelling"}:
                return
            if row.attempt_id is None:
                row.status, row.error_code = command["status"], "admission_failed"
                release_run(session, row, release_environment=True)
                append_event(
                    session, run_id=row.id, kind=row.status, payload={}, cipher=self.cipher
                )
            else:
                finish_run(
                    session,
                    context=ExecutionContext(row.id, row.user_id, row.instance_id, row.attempt_id),
                    cipher=self.cipher,
                    status=command["status"],
                    error_code="workflow_failed",
                )

    @activity.defn(name="execution.instance_tool")
    async def tool(self, command: dict[str, Any]) -> object:
        context, prepared = self.context(UUID(command["run_id"]), command["attempt_id"])
        configured = [
            tool
            for config in prepared.snapshot.definitions.values()
            for tool in config.tools
            if tool.type == "tool"
            and tool.tool_id == command["tool_id"]
            and tool.config == command["config"]
        ]
        if not configured:
            raise ForbiddenError("The tool binding is not in this instance configuration")
        source = await self.source(context, prepared)
        if source.tools.get(command["tool_id"]).model_dump(mode="json") != command["expected_info"]:
            raise ConflictError("The tool schema or approval policy changed after admission")
        tool = source.tools.create(command["tool_id"], command["config"])
        if not isinstance(tool, FunctionTool):
            raise ConflictError("Workflow tool Activities require a native FunctionTool")
        wrapper = ToolContext(
            context=context,
            tool_name=tool.name,
            tool_call_id=command["call_id"],
            tool_arguments=json.dumps(command["arguments"]),
        )
        return await tool.on_invoke_tool(wrapper, json.dumps(command["arguments"]))

    @activity.defn(name="execution.instance_history")
    async def history(self, request: HistoryCall) -> object:
        context, prepared = self.context(request.identity.run_id)
        if prepared.identity != request.identity or prepared.session != request.reference:
            raise ForbiddenError("The history reference belongs to another run")
        ledger = OperationLedger(self.sessions, self.cipher, context)
        operation_id = context.operation_id("history", activity.info().activity_id)
        cached = ledger.claim(operation_id, "history", request.model_dump(mode="json"))
        if cached is not None:
            return cached["value"]

        async def authorize(call: HistoryCall) -> None:
            ledger.authorize()

        try:
            value = await NativeHistory(self.history_root, authorize=authorize).operate(request)
            ledger.complete(operation_id, {"value": value})
            return value
        except BaseException:
            ledger.uncertain(operation_id)
            raise

    @activity.defn(name="execution.instance_callback")
    async def callback(self, command: dict[str, Any]) -> object:
        context, prepared = self.context(UUID(command["run_id"]), command["attempt_id"])
        source = await self.source(context, prepared)
        expected = CallbackDescription.model_validate(command["description"])
        if expected not in describe_callbacks(source.callbacks, prepared.snapshot):
            raise ConflictError("The callback registration changed after admission")
        ledger = OperationLedger(self.sessions, self.cipher, context)
        operation_id = context.operation_id("callback", activity.info().activity_id)
        cached = ledger.claim(operation_id, "callback", command)
        if cached is not None:
            return cached["value"]
        try:
            async with AsyncExitStack() as lifespan:
                built = await lifespan.enter_async_context(prepared_agent(prepared, source))
                agent = next(
                    (item for item in built.agents.values() if item.name == command["agent"]), None
                )
                if agent is None:
                    raise ForbiddenError("The callback Agent is not in this instance configuration")
                arguments = dict(command["wrapper"])
                wrapper_type = arguments.pop("kind")
                arguments["usage"] = TypeAdapter(Usage).validate_python(arguments["usage"])
                arguments["context"] = context
                wrapper: Any
                if wrapper_type == "tool":
                    wrapper = ToolContext(**arguments)
                elif wrapper_type == "agent":
                    wrapper = AgentHookContext(**arguments)
                elif wrapper_type == "run":
                    wrapper = RunContextWrapper(**arguments)
                else:
                    raise ForbiddenError("The callback context contract is invalid")
                callback = source.callbacks.resolve(expected.kind, expected.reference)
                data = dict(command["data"])
                if expected.kind == "agent_hooks":
                    method = command["method"]
                    if method not in {
                        "on_start",
                        "on_end",
                        "on_handoff",
                        "on_tool_start",
                        "on_tool_end",
                        "on_llm_start",
                        "on_llm_end",
                    }:
                        raise ForbiddenError("The callback hook is not supported")
                    if "source" in data:
                        data["source"] = next(
                            item for item in built.agents.values() if item.name == data["source"]
                        )
                    if "response" in data:
                        data["response"] = TypeAdapter(ModelResponse).validate_python(
                            data["response"]
                        )
                    if "tool" in data:
                        tools = await agent.get_all_tools(wrapper)
                        if isinstance(agent, SandboxAgent):
                            if prepared.environment_id is None:
                                raise ConflictError("The callback sandbox has no environment")
                            borrowed = await AgentSandboxClient(
                                self.sandbox, self.sandbox.backend
                            ).resume(
                                AgentSandboxState(
                                    session_id=context.run_id,
                                    environment_id=prepared.environment_id,
                                )
                            )
                            lifespan.push_async_callback(borrowed.shutdown)
                            for capability in agent.capabilities:
                                capability = capability.clone()
                                capability.bind(borrowed)
                                tools.extend(capability.tools())
                        tool = next((tool for tool in tools if tool.name == data["tool"]), None)
                        if tool is None:
                            raise ConflictError("The callback tool is no longer available")
                        data["tool"] = tool
                    value = getattr(callback, method)(wrapper, agent, **data)
                elif command["method"] != "call":
                    raise ForbiddenError("The callback operation is invalid")
                elif isinstance(callback, InputGuardrail):
                    value = callback.guardrail_function(wrapper, agent, data["input"])
                elif isinstance(callback, OutputGuardrail):
                    value = callback.guardrail_function(wrapper, agent, data["output"])
                elif expected.kind == "prompt":
                    value = callback(GenerateDynamicPromptData(context=wrapper, agent=agent))
                else:
                    value = callback(wrapper, agent)
                value = await value if inspect.isawaitable(value) else value
                if expected.kind == "agent_hooks" and value is not None:
                    raise TypeError("Agent lifecycle hooks must return None")
                value = TypeAdapter(Any).dump_python(value, mode="json")
                await EncryptedPayloadCodec(self.cipher).encode(
                    activity.payload_converter().to_payloads([value])
                )
                ledger.complete(operation_id, {"value": value})
                return value
        except BaseException:
            ledger.uncertain(operation_id)
            raise

    @activity.defn(name="execution.instance_sandbox_operation")
    async def sandbox_operation(self, command: dict[str, str]) -> str:
        reference = RuntimeCommand(
            execution_id=UUID(command["execution_id"]),
            environment_id=UUID(command["environment_id"]),
        )
        if activity.info().workflow_id != f"instance-sandbox:{reference.execution_id}":
            raise ForbiddenError("The sandbox operation belongs to another Workflow")
        return await self.sandbox.operate(reference)

    def mcp(self, argument: dict[str, Any]) -> MCPServer:
        return InstanceMCP(self, argument)


class InstanceModelProvider(ModelProvider):
    def __init__(self, owner: InstanceActivities) -> None:
        self.owner = owner

    def get_model(self, model_name: str | None) -> Model:
        if model_name is None or not model_name.startswith(MODEL_PREFIX):
            raise ValueError("An authorized instance model reference is required")
        return InstanceModel(self.owner, json.loads(model_name[len(MODEL_PREFIX) :]))


class InstanceModel(Model):
    def __init__(self, owner: InstanceActivities, reference: dict[str, str]) -> None:
        self.owner, self.reference = owner, reference

    async def get_response(self, *args: Any, **kwargs: Any) -> ModelResponse:
        context, prepared = self.owner.context(
            UUID(self.reference["run_id"]), self.reference["attempt_id"]
        )
        reference = ModelRef(
            provider_id=self.reference["provider_id"], model_id=self.reference["model_id"]
        )
        if not any(config.model == reference for config in prepared.snapshot.definitions.values()):
            raise ForbiddenError("The model reference is not in this instance configuration")
        source = await self.owner.source(context, prepared)
        ledger = OperationLedger(self.owner.sessions, self.owner.cipher, context)
        context_tokens = None
        timeout_seconds = 300.0
        pricing = None
        max_retries = 0
        async with AsyncExitStack() as lifespan:
            if source.model_resolver is not None:
                native, _ = await source.model_resolver(reference, lifespan)
            elif source.credentials is not None:
                provider, info = source.providers.select(reference.provider_id, reference.model_id)
                context_tokens = info.context_tokens
                timeout_seconds = provider.request_timeout
                pricing, max_retries = info.pricing, provider.max_retries
                native, _ = await create_model(
                    reference,
                    providers=source.providers,
                    credentials=source.credentials,
                    lifespan=lifespan,
                )
            else:
                raise ValueError("A model resolver is required")
            if not isinstance(native, Model):
                raise ValueError("Model Activities require a native Model")
            return await ExecutionModel(
                native,
                ledger,
                context_tokens=context_tokens,
                timeout_seconds=timeout_seconds,
                reference=reference,
                pricing=pricing,
                max_retries=max_retries,
                operation_id=lambda: context.operation_id("model", activity.info().activity_id),
            ).get_response(*args, **kwargs)

    async def stream_response(
        self, *args: Any, **kwargs: Any
    ) -> AsyncIterator[TResponseStreamEvent]:
        raise ValueError("Temporal instance execution uses native non-streaming model Activities")
        yield


class InstanceMCP(MCPServer):
    def __init__(self, owner: InstanceActivities, argument: dict[str, Any]) -> None:
        super().__init__()
        self.owner, self.argument = owner, argument
        self.lifespan = AsyncExitStack()
        self.native: MCPServer | None = None

    @property
    def name(self) -> str:
        return "opsmesh.instance"

    async def connect(self) -> None:
        self.context, prepared = self.owner.context(
            UUID(self.argument["run_id"]), self.argument["attempt_id"]
        )
        reference = MCPServerRef.model_validate(self.argument["reference"])
        if not any(
            reference in config.mcp_servers for config in prepared.snapshot.definitions.values()
        ):
            raise ForbiddenError("The MCP reference is not in this instance configuration")
        source = await self.owner.source(self.context, prepared)
        if source.mcp_resolver is None:
            raise ValueError("An MCP resolver is required")
        options: dict[str, Any] = {}
        if reference.allowed_tools is not None:
            options["tool_filter"] = {"allowed_tool_names": reference.allowed_tools}
        elif reference.blocked_tools:
            options["tool_filter"] = {"blocked_tool_names": reference.blocked_tools}
        try:
            self.native = await source.mcp_resolver(reference, options, self.lifespan)
        except BaseException:
            await self.lifespan.aclose()
            raise

    async def cleanup(self) -> None:
        await self.lifespan.aclose()

    def server(self) -> MCPServer:
        OperationLedger(self.owner.sessions, self.owner.cipher, self.context).authorize()
        if self.native is None:
            raise ConflictError("The MCP server is not connected")
        return self.native

    async def list_tools(
        self, run_context: RunContextWrapper[Any] | None = None, agent: Any = None
    ) -> list[Tool]:
        return await self.server().list_tools(run_context, agent)

    async def call_tool(
        self, tool_name: str, arguments: dict[str, Any] | None, meta: dict[str, Any] | None = None
    ) -> CallToolResult:
        server = self.server()
        if isinstance(server, AuthorizedMCP):
            if not server.selected(tool_name):
                raise ForbiddenError("MCP tool is outside the Agent binding")
            await server.authorize("execute", "tools/" + tool_name, arguments or {})
        ledger = OperationLedger(self.owner.sessions, self.owner.cipher, self.context)
        operation_id = self.context.operation_id("mcp", activity.info().activity_id)
        cached = ledger.claim(
            operation_id,
            "mcp",
            {
                "reference": self.argument["reference"],
                "tool_name": tool_name,
                "arguments": arguments,
                "meta": meta,
            },
        )
        if cached is not None:
            return CallToolResult.model_validate(cached)
        try:
            result = await self.server().call_tool(tool_name, arguments, meta)
            ledger.complete(operation_id, result.model_dump(mode="json"))
            return result
        except BaseException:
            ledger.uncertain(operation_id)
            raise

    async def list_prompts(self) -> ListPromptsResult:
        return await self.server().list_prompts()

    async def get_prompt(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> GetPromptResult:
        return await self.server().get_prompt(name, arguments)

    async def list_resources(self, cursor: str | None = None) -> ListResourcesResult:
        return await self.server().list_resources(cursor)

    async def list_resource_templates(
        self, cursor: str | None = None
    ) -> ListResourceTemplatesResult:
        return await self.server().list_resource_templates(cursor)

    async def read_resource(self, uri: str) -> ReadResourceResult:
        return await self.server().read_resource(uri)


class InstanceSandboxClient(AgentSandboxClient):
    def _authorize(self, state: AgentSandboxState) -> dict[str, object]:
        if activity.info().workflow_id != f"instance:{state.session_id}":
            raise ForbiddenError("The sandbox reference belongs to another Workflow")
        return super()._authorize(state)
