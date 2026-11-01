"""Metered Responses compaction over the runtime's durable session history."""

import inspect
from types import SimpleNamespace
from typing import Any

from agents import OpenAIResponsesCompactionSession
from agents.items import ModelResponse
from agents.usage import _response_usage_to_usage

from opsmesh.framework.agents import AgentExecutionContext, AgentSpecification
from opsmesh.framework.errors import OpsMeshError

from .execution_state import execution_state
from .history import OpenAIHistorySession
from .usage import begin_request, publish_request_usage


class MeteredCompactionResponses:
    def __init__(
        self, responses: Any, context: AgentExecutionContext, spec: AgentSpecification
    ) -> None:
        self.responses, self.context, self.spec = responses, context, spec

    async def compact(self, **arguments: Any) -> Any:
        authorize = self.context.services.authorize_model_request
        if authorize is None:
            raise OpsMeshError("DEPENDENCY_UNAVAILABLE", "Compaction requires model admission")
        authorized = authorize(self.spec.model_profile_ref)
        if inspect.isawaitable(authorized):
            await authorized
        agent = SimpleNamespace(name=f"compaction [{self.spec.ref}]", model=arguments["model"])
        execution_state(self.context).agent_model_profiles[agent.name] = self.spec.model_profile_ref
        begin_request(self.context, agent)
        compacted = await self.responses.compact(**arguments)
        await publish_request_usage(
            self.context,
            agent,
            ModelResponse(
                output=[],
                usage=_response_usage_to_usage(compacted.usage),
                response_id=compacted.id,
            ),
        )
        return compacted


def build_session(
    spec: AgentSpecification, context: AgentExecutionContext, model: Any, client: Any = None
) -> Any:
    history = context.state.model_history
    session = OpenAIHistorySession(context.session_id, history) if history is not None else None
    configuration = spec.session_compaction
    if configuration is None:
        return session
    if session is None:
        raise OpsMeshError(
            "DEPENDENCY_UNAVAILABLE", "Session compaction requires persistent history"
        )
    client = execution_state(context).compaction_client or client
    if client is None:
        raise OpsMeshError("DEPENDENCY_UNAVAILABLE", "Responses compaction client is unavailable")
    proxy: Any = SimpleNamespace(
        responses=MeteredCompactionResponses(client.responses, context, spec)
    )
    return OpenAIResponsesCompactionSession(
        context.session_id,
        session,
        client=proxy,
        model=getattr(model, "model", None) or spec.model or "gpt-4.1",
        compaction_mode=configuration.mode,
        max_rollback_items=configuration.max_rollback_items,
        should_trigger_compaction=lambda data: (
            len(data["compaction_candidate_items"]) >= configuration.threshold
        ),
    )
