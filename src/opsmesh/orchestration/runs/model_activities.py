"""Official model-provider extension: product admission and accounting around native I/O."""

import asyncio
import inspect
from dataclasses import replace
from typing import Any
from uuid import UUID

from agents import Model, ModelResponse
from agents.items import TResponseInputItem, TResponseStreamEvent
from pydantic import TypeAdapter
from sqlalchemy.orm import Session, sessionmaker

from opsmesh.identity.quotas.schemas import NormalizedUsage
from opsmesh.orchestration.contracts import (
    ExecutionBindings,
    ExecutionSubject,
    ModelConnections,
    ScopeAdmission,
)
from opsmesh.orchestration.execution.contracts import (
    ExecutionRequest,
    ModelConnection,
    UncertainOperationError,
)
from opsmesh.orchestration.execution.model_provider import NativeModelProvider
from opsmesh.orchestration.runs.access import load_run, lock_run_facts
from opsmesh.orchestration.runs.hooks import finish_hooks
from opsmesh.orchestration.runs.models import OperationReceipt, Run
from opsmesh.orchestration.runs.usage import retain_uncertain, settle_usage
from opsmesh.orchestration.sessions.access import record_scope
from opsmesh.orchestration.sessions.models import SessionRecord
from opsmesh.shared.errors import ConflictError
from opsmesh.shared.security.encryption import SecretCipher
from opsmesh.shared.serialization import payload_digest

RESPONSE = TypeAdapter(ModelResponse)
STREAM = TypeAdapter(list[TResponseStreamEvent])
MODEL_INPUT: TypeAdapter[str | list[TResponseInputItem]] = TypeAdapter(
    str | list[TResponseInputItem]
)


class ExecutionModelProvider(NativeModelProvider):
    def __init__(
        self,
        sessions: sessionmaker[Session],
        bindings: ExecutionBindings,
        admission: ScopeAdmission,
        connections: ModelConnections,
        cipher: SecretCipher,
    ) -> None:
        self.sessions = sessions
        self.bindings = bindings
        self.admission = admission
        self.connections = connections
        self.cipher = cipher
        super().__init__(lambda run_id, agent_key: ModelActivities(self, run_id, agent_key))


class ModelActivities:
    def __init__(self, provider: ExecutionModelProvider, run_id: UUID, agent_key: str) -> None:
        self.provider = provider
        self.run_id = run_id
        self.agent_key = agent_key

    def has_hooks(self) -> bool:
        with self.provider.sessions() as session:
            run = session.get(Run, self.run_id)
            if run is None:
                raise ConflictError("The execution Run does not exist")
            return bool(
                ExecutionRequest.model_validate(
                    self.provider.cipher.decrypt_payload(run.request_ciphertext)
                ).hooks
            )

    async def before_hooks(
        self, args: tuple[Any, ...], kwargs: dict[str, Any], operation_id: str
    ) -> tuple[tuple[Any, ...], dict[str, Any], list[str]]:
        parameters = dict(
            inspect.signature(Model.get_response).bind(self, *args, **kwargs).arguments
        )
        parameters.pop("self", None)
        # The SDK converter may validate message content as a lazy iterable.
        # Materialize once before hooks, fingerprinting and native model I/O share it.
        parameters["input"] = MODEL_INPUT.dump_python(parameters["input"], mode="json")
        if not await asyncio.to_thread(self.has_hooks):
            return (), parameters, []
        bridge = self.provider.bindings.extensions
        if bridge is None:
            raise ConflictError("Installed model hooks are not assembled")
        settings = parameters["model_settings"]
        answer = await bridge.hooks(
            run_id=self.run_id,
            occurrence=operation_id,
            stage="model.before",
            payload={"input": parameters["input"], "temperature": settings.temperature},
        )
        patched_input = answer["payload"]["input"]
        if patched_input != parameters["input"]:
            parameters["input"] = MODEL_INPUT.dump_python(
                MODEL_INPUT.validate_python(patched_input), mode="json"
            )
        temperature = answer["payload"].get("temperature")
        if temperature is not None and (
            isinstance(temperature, bool)
            or not isinstance(temperature, int | float)
            or not 0 <= temperature <= 2
        ):
            raise ConflictError("The model hook returned an invalid temperature")
        parameters["model_settings"] = replace(settings, temperature=temperature)
        return (), parameters, list(answer["entered"])

    async def after_hooks(
        self, result: ModelResponse, operation_id: str, entered: list[str]
    ) -> ModelResponse:
        if not await asyncio.to_thread(self.has_hooks):
            return result
        bridge = self.provider.bindings.extensions
        assert bridge is not None
        serialized = RESPONSE.dump_python(result, mode="json")
        answer = await bridge.hooks(
            run_id=self.run_id,
            occurrence=operation_id,
            stage="model.after",
            payload={"output": serialized["output"]},
            entered=entered,
        )
        serialized["output"] = answer["payload"]["output"]
        entered[:] = answer["entered"]
        return RESPONSE.validate_python(serialized)

    def claim(
        self,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        operation_id: str,
        streaming: bool,
    ) -> tuple[bool, Any, ModelConnection | None, str]:
        parameters = inspect.signature(Model.get_response).bind(self, *args, **kwargs).arguments
        parameters.pop("self", None)
        if any(
            parameters.get(key) is not None
            for key in ("previous_response_id", "conversation_id", "prompt")
        ):
            raise ConflictError(
                "Execution uses its native Session rather than hosted history or prompts"
            )
        schema = parameters["output_schema"]
        fingerprint = payload_digest(
            {
                "agent": self.agent_key,
                "streaming": streaming,
                "input": parameters["input"],
                "instructions": parameters["system_instructions"],
                "settings": TypeAdapter(type(parameters["model_settings"])).dump_python(
                    parameters["model_settings"], mode="json"
                ),
                "schema": schema.json_schema() if schema is not None else None,
                "tools": [
                    {"name": tool.name, "schema": getattr(tool, "params_json_schema", None)}
                    for tool in parameters["tools"]
                ],
                "handoffs": [
                    {"name": handoff.tool_name, "schema": handoff.input_json_schema}
                    for handoff in parameters["handoffs"]
                ],
            }
        )
        p = self.provider
        with p.sessions() as session, session.begin():
            unverified = session.get(Run, self.run_id)
            if unverified is None:
                raise ConflictError("The execution Run does not exist")
            run = load_run(
                session,
                run_id=self.run_id,
                subject=ExecutionSubject(unverified.principal_id),
                admission=p.admission,
                lock=True,
            )
            receipt = session.get(OperationReceipt, (run.scope_id, operation_id))
            if receipt is not None:
                if receipt.fingerprint != fingerprint or receipt.run_id != run.id:
                    raise ConflictError("Model operation identity was reused")
                if receipt.status != "completed" or not receipt.accounted:
                    raise UncertainOperationError("The model operation needs reconciliation")
                return (
                    False,
                    p.cipher.decrypt_payload(receipt.result_ciphertext or "")["result"],
                    None,
                    "",
                )
            if run.status != "running" or (run.capacity_reservation_id is None):
                raise ConflictError("The Run is not admitted for model execution")
            request = ExecutionRequest.model_validate(
                p.cipher.decrypt_payload(run.request_ciphertext)
            )
            model = request.agents[self.agent_key].model
            if request.configuration_source is not None or request.parent_invocation_id is not None:
                if p.bindings.sources is None:
                    raise ConflictError("Configuration source authorization is not assembled")
                p.bindings.sources.authorize(session, run_id=run.id)
            model_session = session.get(SessionRecord, run.session_id)
            if model_session is None:
                raise ConflictError("The model Session does not exist")
            connection = p.connections.resolve(
                session,
                subject=ExecutionSubject(run.principal_id),
                scope=record_scope(model_session, principal_id=run.principal_id),
                connection_id=model.connection_id,
                model=model.name,
            )
            if parameters["model_settings"].max_tokens != model.max_output_tokens:
                raise ConflictError("The model output bound differs from the frozen request")
            if model.max_output_tokens > connection.context_tokens:
                raise ConflictError("The model output bound exceeds the verified context window")
            # Cached and uncached input share one verified context window.
            upper = NormalizedUsage(
                input_tokens=connection.context_tokens,
                cached_input_tokens=connection.context_tokens,
                output_tokens=model.max_output_tokens,
            )
            if p.bindings.limit_operation is not None:
                p.bindings.limit_operation(
                    session,
                    run_ref=str(run.id),
                    operation_id=operation_id,
                    tokens=connection.context_tokens + upper.output_tokens,
                )
            elif (
                request.configuration_source is not None or request.parent_invocation_id is not None
            ):
                raise ConflictError("Source Token admission is not assembled")
            tokens = p.bindings.tokens.reserve_tokens(
                session,
                user_id=run.principal_id,
                amount=connection.context_tokens + upper.output_tokens,
                idempotency_scope="orchestration.model",
                idempotency_key=operation_id,
            )
            session.add(
                OperationReceipt(
                    scope_id=run.scope_id,
                    operation_id=operation_id,
                    run_id=run.id,
                    fingerprint=fingerprint,
                    kind="model",
                    status="started",
                    accounted=False,
                    token_reservation_id=tokens.id,
                )
            )
            return True, None, connection, model.name

    def complete(
        self,
        operation_id: str,
        result: Any,
        usage: dict[str, int] | None,
    ) -> None:
        p = self.provider
        with p.sessions() as session, session.begin():
            previous = session.get(Run, self.run_id)
            if previous is None:
                raise ConflictError("The Run disappeared")
            run = lock_run_facts(session, self.run_id)
            receipt = session.get(OperationReceipt, (run.scope_id, operation_id))
            if receipt is None:
                raise ConflictError("The model operation disappeared")
            if receipt.accounted:
                return
            if usage is None:
                retain_uncertain(session, run=run, receipt=receipt)
                return
            receipt.result_ciphertext = p.cipher.encrypt_payload({"result": result}).ciphertext
            settle_usage(
                session,
                run=run,
                receipt=receipt,
                usage=NormalizedUsage.model_validate(usage),
                bindings=p.bindings,
            )
            receipt.status = "completed"

    async def finish_hooks(self, operation_id: str, entered: list[str], failed: bool) -> None:
        await finish_hooks(
            self.provider.bindings,
            run_id=self.run_id,
            occurrence=operation_id,
            entered=entered,
            failed=failed,
        )
