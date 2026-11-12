"""Resolve conversation identity and execute one exclusively held turn."""

import json
from typing import Annotated
from uuid import NAMESPACE_URL, uuid5

from agents import RunConfig
from agents.sandbox import SandboxRunConfig

from opsmesh.adapters import Channel, Event
from opsmesh.params import Context

from . import channels, execution
from .agent import agent, settings
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
                "example_sandbox",
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
            async with conversations.workspaces.open(selected.id) as sandbox:
                result = await execution.run(
                    agent,
                    message,
                    context=caller,
                    session=conversations.history(selected.id),
                    run_config=RunConfig(
                        sandbox=SandboxRunConfig(session=sandbox)
                    ),
                    max_turns=settings.max_turns,
                    plugin_id="example_sandbox",
                    trace_context=execution.TraceContext(
                        user_id=caller.id,
                        session_id=selected.id,
                    ),
                )
                if result.interruptions:
                    raise channels.api.HTTPError(
                        409,
                        "This example does not support approval continuation",
                    )
                if not isinstance(result.final_output, str):
                    raise TypeError("The sandbox conversation must return text")
                text = result.final_output
    except ConversationError as error:
        raise channels.api.HTTPError(error.status, str(error)) from None
    return ChatReply(text=text, session_id=selected.id)


async def respond(channel: Channel, event: Event, reply: ChatReply) -> None:
    """Preserve HTTP metadata while sending plain text to bot transports."""
    await channel.send(
        event,
        reply if isinstance(event, channels.api.HTTPEvent) else reply.text,
    )
