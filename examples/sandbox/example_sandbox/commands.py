"""Run deterministic conversation commands through framework matchers."""

import json
from typing import Annotated

from pydantic import ValidationError

from opsmesh.adapters import Channel, Event
from opsmesh.messages import Message
from opsmesh.params import Command, CommandArg, Context, Depends
from opsmesh.plugins import on_command

from . import channels
from .conversation import answer, conversation, respond
from .lifecycle import get_conversations
from .schemas import ChatReply, ChatRequest, ConversationView
from .sessions import ConversationError

chat_command = on_command("chat", force_whitespace=True, block=True)
help_command = on_command("help", force_whitespace=True, block=True)
clear_command = on_command("clear", force_whitespace=True, block=True)
session_command = on_command("session", force_whitespace=True, block=True)
history_command = on_command("history", force_whitespace=True, block=True)
reset_command = on_command("reset_workspace", force_whitespace=True, block=True)
COMMAND_MATCHERS = (
    reset_command,
    chat_command,
    help_command,
    clear_command,
    session_command,
    history_command,
)


@chat_command.handle()
async def chat(
    channel: Channel,
    event: Event,
    question: Annotated[Message, CommandArg()],
    caller: Annotated[channels.UserContext, Context()],
    selected: Annotated[ConversationView, Depends(conversation)],
) -> None:
    """Run a question in the selected persistent conversation."""
    try:
        request = ChatRequest(message=question.extract_plain_text())
    except ValidationError:
        result = ChatReply(
            text="Usage: /chat <question, 1-8000 characters>",
            session_id=selected.id,
        )
    else:
        try:
            result = await answer(request.message, caller, selected)
        except channels.api.HTTPError as error:
            if isinstance(event, channels.api.HTTPEvent):
                raise
            result = ChatReply(text=str(error), session_id=selected.id)
    await respond(channel, event, result)


@help_command.handle()
async def help_chat(
    channel: Channel,
    event: Event,
    selected: Annotated[ConversationView, Depends(conversation)],
) -> None:
    """Describe server commands without invoking the model."""
    await respond(
        channel,
        event,
        ChatReply(
            text="/chat <question> - Ask the assistant.\n"
            "/help - Show commands.\n"
            "/session - Current conversation.\n/history - Recent history.\n"
            "/clear - Clear history (keep files).\n"
            "/reset_workspace - Delete files (keep history).\n"
            "Terminal: /new [name], /sessions, /use <id>, /delete <id>, /exit.",
            session_id=selected.id,
        ),
    )


@reset_command.handle()
@clear_command.handle()
@session_command.handle()
@history_command.handle()
async def manage_conversation(
    channel: Channel,
    event: Event,
    caller: Annotated[channels.UserContext, Context()],
    selected: Annotated[ConversationView, Depends(conversation)],
    argument: Annotated[Message, CommandArg()],
    command: Annotated[tuple[str, ...], Command()],
) -> None:
    """Run a deterministic command on the selected conversation."""
    operation = command[0]
    if argument.extract_plain_text().strip():
        result = "This command does not accept arguments"
    else:
        conversations = get_conversations()
        try:
            if operation == "clear":
                await conversations.clear(caller.id, selected.id)
                result = "Conversation history cleared"
            elif operation == "reset_workspace":
                await conversations.reset_workspace(caller.id, selected.id)
                result = (
                    "Sandbox workspace reset; conversation history retained"
                )
            elif operation == "history":
                items = await conversations.messages(caller.id, selected.id)
                result = json.dumps(items, ensure_ascii=False, indent=2)
            else:
                result = selected.model_dump_json(indent=2)
        except ConversationError as error:
            if isinstance(event, channels.api.HTTPEvent):
                raise channels.api.HTTPError(error.status, str(error)) from None
            result = str(error)
    await respond(
        channel, event, ChatReply(text=result, session_id=selected.id)
    )
