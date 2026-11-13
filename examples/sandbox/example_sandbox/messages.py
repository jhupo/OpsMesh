"""Handle ordinary bot messages after deterministic commands."""

from typing import Annotated

from opsmesh.adapters import Channel, Event
from opsmesh.params import Context, Depends
from opsmesh.plugins import on_message

from . import channels
from .conversation import answer, conversation, respond
from .schemas import ChatReply, ConversationView


async def addressed_message(event: Event) -> bool:
    """Select directly addressed API, private and group-mention messages."""
    return event.is_tome()


message_handler = on_message(rule=addressed_message, priority=10, block=True)


@message_handler.handle()
async def chat(
    channel: Channel,
    event: Event,
    caller: Annotated[channels.UserContext, Context()],
    selected: Annotated[ConversationView, Depends(conversation)],
) -> None:
    """Allow private messages and group mentions without a command prefix."""
    if event.get_plaintext().lstrip().startswith("/"):
        if isinstance(event, channels.api.APIMessageEvent):
            raise channels.api.HTTPError(400, "Unknown command; use /help")
        result = ChatReply(
            text="Unknown command; use /help", session_id=selected.id
        )
    else:
        try:
            result = await answer(
                event.get_message(),
                caller,
                selected,
                attachments=event.attachments,
            )
        except channels.api.HTTPError as error:
            if isinstance(event, channels.api.HTTPEvent):
                raise
            result = ChatReply(text=str(error), session_id=selected.id)
    await respond(channel, event, result)
