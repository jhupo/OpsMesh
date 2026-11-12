"""Define the native agent and execute native conversation turns."""

from agents import Agent, ModelSettings
from agents.extensions.memory import SQLAlchemySession

from opsmesh.framework.plugins.config import get_plugin_config

from . import channels, execution
from .config import ChatSettings

settings = get_plugin_config(ChatSettings, namespace="example_chat")
agent = Agent[channels.UserContext](
    name=settings.name,
    instructions=settings.instructions,
    model=settings.model,
    model_settings=ModelSettings(reasoning=settings.reasoning),
)


class ApprovalUnsupported(RuntimeError):
    """The example has no approval continuation endpoint."""


async def reply(
    message: str,
    caller: channels.UserContext,
    session: SQLAlchemySession,
) -> str:
    """Run the declared agent with the authenticated caller context.

    Args:
        message: Validated question from the HTTP or command entrance.
        caller: Identity supplied by the channel, never by model input.
        session: Native history for the owned, exclusively held conversation.

    Returns:
        The native agent's plain-text answer.

    Raises:
        ApprovalUnsupported: A tool requested approval continuation.
        TypeError: The agent returned a non-text result.
    """
    result = await execution.run(
        agent,
        message,
        context=caller,
        session=session,
        max_turns=settings.max_turns,
        plugin_id="example_chat",
        trace_context=execution.TraceContext(
            user_id=caller.id,
            session_id=session.session_id,
        ),
    )
    if result.interruptions:
        raise ApprovalUnsupported(
            "Approval continuation is not supported by this example"
        )
    if not isinstance(result.final_output, str):
        raise TypeError("The conversation must return plain text")
    return result.final_output
