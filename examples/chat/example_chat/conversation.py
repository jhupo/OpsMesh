"""Stream native SDK turns while retaining the owned history lock."""

from conversation_support.entrance import ConversationTurn
from conversation_support.inputs import model_input
from conversation_support.streaming import text_deltas

from opsmesh.messages import Message
from opsmesh.plugins.channels.attachments import AttachmentError

from . import agent as definition
from . import execution
from .lifecycle import get_conversations
from .schemas import ConversationView
from .sessions import ConversationError


async def answer(
    turn: ConversationTurn, message: Message, selected: ConversationView
) -> None:
    """Send text deltas before releasing native execution and history."""
    conversations = get_conversations()
    async with conversations.hold(turn.caller.id, selected.id):
        try:
            model_message = await model_input(message, turn.event.attachments)
        except AttachmentError as error:
            raise ConversationError(422, str(error)) from error
        async with execution.stream(
            definition.agent,
            model_message,
            context=turn.caller,
            session=conversations.history(selected.id),
            max_turns=definition.settings.max_turns,
            plugin_id="example_chat",
            trace_context=execution.TraceContext(
                user_id=turn.caller.id, session_id=selected.id
            ),
        ) as result:
            await turn.channel.call(
                "stream", event=turn.event, source=text_deltas(result)
            )
