"""Resolve conversation identity and execute one exclusively held turn."""

import json
from typing import Annotated
from uuid import NAMESPACE_URL, uuid5

from opsmesh.adapters import Channel, Event
from opsmesh.params import Context

from . import channels
from .agent import ApprovalUnsupported, reply
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
            session_id = event.payload.get("session_id")
            if session_id:
                return await conversations.get(caller.id, session_id)
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
        raise channels.api.HTTPError(error.status, str(error)) from None


async def answer(
    message: str,
    caller: channels.UserContext,
    selected: ConversationView,
) -> ChatReply:
    """Keep the index lock until native execution and observation finish."""
    conversations = get_conversations()
    try:
        async with conversations.hold(caller.id, selected.id):
            text = await reply(
                message, caller, conversations.history(selected.id)
            )
    except ConversationError as error:
        raise channels.api.HTTPError(error.status, str(error)) from None
    except ApprovalUnsupported as error:
        raise channels.api.HTTPError(409, str(error)) from None
    return ChatReply(text=text, session_id=selected.id)


async def respond(channel: Channel, event: Event, reply: ChatReply) -> None:
    """Preserve HTTP metadata while sending plain text to bot transports."""
    await channel.send(
        event,
        reply if isinstance(event, channels.api.HTTPEvent) else reply.text,
    )
