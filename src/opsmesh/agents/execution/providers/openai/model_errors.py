"""Preserve SDK retries while attributing failures to the executing graph model."""

from contextvars import ContextVar

from opsmesh.agents.execution.contracts import AgentRunRequest
from opsmesh.agents.execution.errors import AgentExecutionPolicyError

active_model_request: ContextVar[list[AgentRunRequest] | None] = ContextVar(
    "active_model_request", default=None
)


def graph_model_error(exc: Exception, request: AgentRunRequest) -> AgentExecutionPolicyError:
    active = active_model_request.get()
    failed = active[-1] if active else request
    if not isinstance(failed, AgentRunRequest):
        raise TypeError("Graph model failure attribution is invalid")
    profile = failed.agent_profile
    return AgentExecutionPolicyError(
        code="agent_graph_model_failed",
        message="An agent model request failed; the graph was stopped without replay",
        event_type="agent.model.failed",
        metadata={
            "agent_profile_id": str(profile.id) if profile.id else None,
            "model": failed.model or profile.model,
            "provider": failed.provider,
            "model_provider_credential_id": (
                str(failed.model_provider_credential_id)
                if failed.model_provider_credential_id
                else None
            ),
            "status_code": getattr(exc, "status_code", None),
            "replay_allowed": False,
        },
    )
