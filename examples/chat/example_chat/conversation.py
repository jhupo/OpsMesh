"""Execute a conversation turn independently of its transport."""

from conversation_support.inputs import model_input

from opsmesh.messages import Message
from opsmesh.plugins.channels.attachments import (
    AttachmentCollection,
    AttachmentError,
)

from . import channels
from .agent import ApprovalUnsupported, reply
from .lifecycle import get_conversations
from .schemas import ChatReply, ConversationView
from .sessions import ConversationError


async def answer(
    message: str | Message,
    caller: channels.UserContext,
    selected: ConversationView,
    *,
    attachments: AttachmentCollection | None = None,
) -> ChatReply:
    """Keep the index lock until native execution and observation finish."""
    conversations = get_conversations()
    try:
        async with conversations.hold(caller.id, selected.id):
            model_message = await model_input(
                message, attachments or AttachmentCollection()
            )
            text = await reply(
                model_message, caller, conversations.history(selected.id)
            )
    except AttachmentError as error:
        raise ConversationError(422, str(error)) from error
    except ApprovalUnsupported as error:
        raise ConversationError(409, str(error)) from error
    return ChatReply(text=text, session_id=selected.id)
