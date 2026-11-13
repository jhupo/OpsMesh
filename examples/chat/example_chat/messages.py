"""Handle ordinary bot messages after deterministic commands."""

from typing import Annotated

from opsmesh.adapters import Channel, Event
from opsmesh.params import Context, Depends
from opsmesh.plugins import on_message

from . import channels
from .conversation import answer
from .handlers import conversation, reject, respond
from .schemas import ConversationView
from .sessions import ConversationError


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
        await reject(
            event, ConversationError(400, "Unknown command; use /help")
        )
    else:
        try:
            result = await answer(
                event.get_message(),
                caller,
                selected,
                attachments=event.attachments,
            )
        except ConversationError as error:
            await reject(event, error)
    await respond(channel, event, result)
