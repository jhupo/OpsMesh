"""Observe SDK model responses; never drive or replay model execution."""

from contextvars import ContextVar
from uuid import uuid4

from agents.usage import Usage
from pydantic import TypeAdapter
from temporalio import workflow

from opsmesh.agents.execution.contracts import (
    AgentModelUsageRecord,
    AgentReference,
    AgentRunRequest,
)
from opsmesh.agents.execution.usage import runtime_usage

model_usages: ContextVar[list[AgentModelUsageRecord] | None] = ContextVar(
    "opsmesh_model_usages", default=None
)


def record_model_usage(request: AgentRunRequest, usage: Usage, response_id: str | None) -> None:
    ledger = model_usages.get()
    if ledger is None:
        return
    profile = request.agent_profile
    normalized = runtime_usage(TypeAdapter(Usage).dump_python(usage, mode="json"))
    if normalized is None:
        raise ValueError("SDK model response has no usage structure")
    ledger.append(
        AgentModelUsageRecord(
            request_id=response_id
            or (workflow.uuid4().hex if workflow.in_workflow() else uuid4().hex),
            agent=AgentReference(profile.name, profile.id, profile.role),
            model=request.model or profile.model,
            provider=request.provider or "openai",
            model_api=request.model_api,
            usage=normalized,
        )
    )
