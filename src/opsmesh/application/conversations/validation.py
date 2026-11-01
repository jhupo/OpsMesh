"""Agent constraints required by reviewed conversation delivery."""

from opsmesh.framework.agents import AgentSpecification
from opsmesh.framework.errors import OpsMeshError


def validate_conversation_agent(agent: AgentSpecification) -> None:
    if agent.memory_generate:
        raise OpsMeshError(
            "CONFIG_INVALID",
            "Reviewed conversations capture memory after confirmed delivery; "
            "disable automatic Agent memory generation",
        )
    if agent.output_schema is not None or agent.output_type_ref is not None:
        raise OpsMeshError("CONFIG_INVALID", "Conversation Agent must use plain text output")
