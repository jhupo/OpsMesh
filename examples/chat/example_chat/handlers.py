"""Adapt framework events and conversation results to their channel."""

import json
from typing import Annotated, NoReturn
from uuid import NAMESPACE_URL, uuid5

from opsmesh.adapters import Channel, Event
from opsmesh.matchers import Matcher
from opsmesh.params import Context

from . import channels
from .lifecycle import get_conversations
from .schemas import ChatReply, ConversationView
from .sessions import ConversationError


async def conversation(
    event: Event,
    channel: Channel,
    caller: Annotated[channels.UserContext, Context()],
) -> ConversationView:
    """Resolve an API selection or a caller-scoped bot conversation."""
    conversations = get_conversations()
    try:
        if isinstance(event, channels.api.HTTPEvent):
            session_id = event.payload.session_id
            if session_id:
                return await conversations.get(caller.id, str(session_id))
            return await conversations.create(caller.id)
        identity = json.dumps(
            [
                "example_chat",
                channel.type,
                channel.self_id,
                event.get_session_id(),
                caller.id,
            ]
        )
        return await conversations.create(
            caller.id,
            "Bot conversation",
            session_id=str(uuid5(NAMESPACE_URL, identity)),
        )
    except ConversationError as error:
        await reject(event, error)


async def respond(channel: Channel, event: Event, reply: ChatReply) -> None:
    """Preserve HTTP metadata while sending plain text to bot transports."""
    await channel.send(
        event,
        reply if isinstance(event, channels.api.HTTPEvent) else reply.text,
    )


async def reject(event: Event, error: ConversationError) -> NoReturn:
    """Return an HTTP failure or finish the current bot matcher with a reply."""
    if isinstance(event, channels.api.HTTPEvent):
        raise channels.api.HTTPError(error.status, str(error)) from error
    await Matcher.finish(str(error))
