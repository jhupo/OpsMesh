"""Bind a caller authorized resource catalog to a single execution attempt."""

from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from dataclasses import replace

from agents import Model, ModelSettings

from opsmesh.agents.assembly import AssemblyBindings
from opsmesh.agents.config.model import ModelRef
from opsmesh.agents.providers.factory import create_model
from opsmesh.execution.contracts import PreparedAgent
from opsmesh.execution.model import ExecutionModel
from opsmesh.execution.runs.access import authorize_run
from opsmesh.execution.runs.operations import OperationLedger
from opsmesh.shared.errors import ConflictError


def freeze_bindings(
    source: AssemblyBindings, ledger: OperationLedger, prepared: PreparedAgent
) -> PreparedAgent:
    descriptions = {
        tool.tool_id: source.tools.get(tool.tool_id)
        for config in prepared.snapshot.definitions.values()
        for tool in config.tools
        if tool.type == "tool"
    }
    if prepared.tool_descriptions is not None:
        if prepared.tool_descriptions != descriptions:
            raise ConflictError(
                "The admitted tool version, configuration or approval policy changed"
            )
        if prepared.provider_configs is None:
            raise ConflictError("The run has no admitted provider configuration")
        return prepared
    providers = {
        config.model.provider_id: source.providers.get(config.model.provider_id)
        for config in prepared.snapshot.definitions.values()
        if config.model is not None and source.model_resolver is None
    }
    with ledger.sessions.begin() as session:
        row = authorize_run(session, ledger.context.run_id, lock=True)
        if row.attempt_id != ledger.context.attempt_id or row.encrypted_state is not None:
            raise ConflictError("An approval requires its original admitted tool descriptions")
        prepared = prepared.model_copy(
            update={"tool_descriptions": descriptions, "provider_configs": providers}
        )
        row.encrypted_prepared = ledger.cipher.encrypt_payload(
            prepared.model_dump(mode="json")
        ).ciphertext
    return prepared


def execution_bindings(source: AssemblyBindings, ledger: OperationLedger) -> AssemblyBindings:
    async def resolve(
        agent_id: str, reference: ModelRef, lifespan: AsyncExitStack
    ) -> tuple[Model, ModelSettings | None]:
        return await resolve_execution_model(source, ledger, agent_id, reference, lifespan)

    return replace(source, model_resolver=resolve)


async def resolve_execution_model(
    source: AssemblyBindings,
    ledger: OperationLedger,
    agent_id: str,
    reference: ModelRef,
    lifespan: AsyncExitStack,
    *,
    operation_id: Callable[[], str] | None = None,
    stream_events: Callable[[object], Awaitable[None]] | None = None,
) -> tuple[ExecutionModel, ModelSettings | None]:
    ledger.authorize()
    context_tokens = None
    timeout_seconds = 300.0
    pricing = None
    max_retries = 0
    if source.model_resolver is not None:
        native, settings = await source.model_resolver(agent_id, reference, lifespan)
    elif source.credentials is not None:
        provider, info = source.providers.select(reference.provider_id, reference.model_id)
        context_tokens = info.context_tokens
        timeout_seconds = provider.request_timeout
        pricing, max_retries = info.pricing, provider.max_retries
        native, settings = await create_model(
            reference, providers=source.providers, credentials=source.credentials, lifespan=lifespan
        )
        if settings.max_tokens is None and info.max_output_tokens is not None:
            settings = settings.resolve(ModelSettings(max_tokens=info.max_output_tokens))
    else:
        raise ValueError("Execution requires an authorized model resolver")
    if not isinstance(native, Model):
        raise TypeError("Execution requires a native Model from its resource resolver")
    return ExecutionModel(
        native,
        ledger,
        context_tokens=context_tokens,
        timeout_seconds=timeout_seconds,
        reference=reference,
        pricing=pricing,
        max_retries=max_retries,
        operation_id=operation_id,
        stream_events=stream_events,
    ), settings
