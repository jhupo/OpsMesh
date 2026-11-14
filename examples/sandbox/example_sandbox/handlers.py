"""Expose conversation commands and an explicit workspace reset."""

from conversation_support.entrance import (
    ConversationEntrance,
    ConversationTurn,
    no_arguments,
    result_message,
)

from opsmesh import on_shutdown
from opsmesh.matchers import Matcher

from .conversation import answer
from .lifecycle import get_conversations

entrance = ConversationEntrance(get_conversations, answer)


async def reset_workspace(turn: ConversationTurn, argument: str) -> None:
    """Remove selected workspace files while retaining native history."""
    no_arguments(argument)
    selected = await entrance.selected(turn)
    await get_conversations().reset_workspace(turn.caller.id, selected.id)
    await Matcher.send(
        result_message(
            "Sandbox workspace reset; history retained.",
            {"session_id": selected.id},
        )
    )


entrance.add_command("reset_workspace", reset_workspace)
entrance.bind()


@on_shutdown
def forget_selections() -> None:
    """Release window mappings after admitted conversations drain."""
    entrance.forget_selections()
