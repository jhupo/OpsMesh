"""Share conversation commands and selection across the agent examples."""

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from pydantic import ConfigDict

from opsmesh import get_driver, on_command, on_message
from opsmesh.adapters import Channel, Event
from opsmesh.matchers import Matcher
from opsmesh.messages import Message
from opsmesh.params import Command, CommandArg, Context, Depends
from opsmesh.rules import to_me

from . import channels
from .schemas import ConversationView
from .store import ConversationError, ConversationStore

MAX_QUESTION_LENGTH = 8000
COMMAND_NAMES = (
    "chat",
    "help",
    "new",
    "sessions",
    "use",
    "session",
    "history",
    "clear",
    "delete",
)


@dataclass(frozen=True)
class ConversationTurn:
    """Carry request resources only for the current handler invocation."""

    __pydantic_config__ = ConfigDict(arbitrary_types_allowed=True)

    channel: Channel
    event: Event
    caller: channels.UserContext


async def current_turn(
    channel: Channel,
    event: Event,
    caller: Annotated[channels.UserContext, Context()],
) -> ConversationTurn:
    """Resolve trusted identity and current transport from framework DI."""
    return ConversationTurn(channel, event, caller)


def command_input(event: Event) -> bool:
    """Reject every configured command prefix, including unknown commands."""
    text = event.get_plaintext().lstrip()
    return any(
        text.startswith(prefix) for prefix in get_driver().config.command_start
    )


async def addressed_message(event: Event) -> bool:
    """Accept addressed messages that have no configured command prefix."""
    return event.is_tome() and not command_input(event)


async def addressed_command(event: Event) -> bool:
    """Select command input left after registered command matchers."""
    return event.is_tome() and command_input(event)


def result_message(text: str, data: dict) -> channels.Message:
    """Pair readable replies with outbound JSON data."""
    return channels.Message(
        [
            channels.MessageSegment.text(text),
            channels.MessageSegment.structured(data),
        ]
    )


def session_id(argument: str) -> str:
    """Validate an explicit SDK session selection."""
    try:
        return str(UUID(argument))
    except ValueError:
        raise ConversationError(
            422, "Provide a valid conversation ID"
        ) from None


def no_arguments(argument: str) -> None:
    """Reject trailing input before a command mutates state."""
    if argument:
        raise ConversationError(422, "This command does not accept arguments")


class ConversationEntrance:
    """Keep SDK selection in the invoking channel conversation's scope."""

    def __init__(
        self,
        get_store: Callable[[], ConversationStore],
        answer: Callable[
            [ConversationTurn, Message, ConversationView], Awaitable[None]
        ],
    ) -> None:
        """Declare matching and selection once for the consuming plugin."""
        self.get_store = get_store
        self.answer = answer
        self.selections: dict[str, str] = {}
        self.operations = {
            "help": self.help,
            "new": self.new,
            "sessions": self.sessions,
            "use": self.use,
            "session": self.session,
            "history": self.history,
            "clear": self.clear,
            "delete": self.delete,
        }
        self.commands = tuple(
            on_command(
                name,
                force_whitespace=True,
                rule=to_me(),
                priority=priority,
                block=True,
            )
            for priority, name in enumerate(COMMAND_NAMES, 1)
        )
        for command in self.commands:
            command.handle()(self.handle_command)
        self.fallback = on_message(
            rule=addressed_command, priority=20, block=True
        )
        self.fallback.handle()(self.unavailable)
        self.message = on_message(
            rule=addressed_message, priority=30, block=True
        )
        self.message.handle()(self.handle_message)
        self.extra_commands: list[type[Matcher]] = []

    def add_command(self, name: str, operation: Callable) -> None:
        """Add a consumer-owned command before binding the entrance."""
        self.operations[name] = operation
        command = on_command(
            name, force_whitespace=True, rule=to_me(), priority=10, block=True
        )
        command.handle()(self.handle_command)
        self.extra_commands.append(command)

    def bind(self) -> None:
        """Expose only these matchers through the unified API entrance."""
        channels.api.message(
            handlers=(
                *self.commands,
                *self.extra_commands,
                self.fallback,
                self.message,
            )
        )

    def forget_selections(self) -> None:
        """Discard transient mappings when the consuming plugin stops."""
        self.selections.clear()

    async def selected(self, turn: ConversationTurn) -> ConversationView:
        """Read the current owned selection without creating a session."""
        selected = self.selections.get(turn.event.get_session_id())
        if selected is None:
            raise ConversationError(
                409, "No conversation selected; use /new or /use <id>"
            )
        conversation = await self.get_store().get(turn.caller.id, selected)
        if conversation.deleting:
            raise ConversationError(409, "Conversation is being deleted")
        return conversation

    async def handle_command(
        self,
        turn: Annotated[ConversationTurn, Depends(current_turn)],
        command: Annotated[tuple[str, ...], Command()],
        argument: Annotated[Message, CommandArg()],
    ) -> None:
        """Use framework command parsing and emit deterministic failures."""
        try:
            if command[0] == "chat":
                await self.chat(turn, argument)
            else:
                if any(not segment.is_text() for segment in argument):
                    raise ConversationError(
                        422, "This command accepts text arguments only"
                    )
                await self.operations[command[0]](
                    turn, argument.extract_plain_text().strip()
                )
        except ConversationError as error:
            await self.reject(error)
        await Matcher.finish()

    async def handle_message(
        self, turn: Annotated[ConversationTurn, Depends(current_turn)]
    ) -> None:
        """Run ordinary text through the same owned session scope."""
        try:
            await self.chat(turn, turn.event.get_message())
        except ConversationError as error:
            await self.reject(error)
        await Matcher.finish()

    async def unavailable(self) -> None:
        """Use one reply for unknown commands and failed permissions."""
        await self.reject(
            ConversationError(400, "Command unavailable; use /help")
        )

    async def reject(self, error: ConversationError) -> None:
        """Expose only a known business error and its safe status."""
        await Matcher.finish(
            result_message(
                str(error),
                {
                    "error": {
                        "code": "conversation_error",
                        "message": str(error),
                        "status": error.status,
                    }
                },
            )
        )

    async def chat(self, turn: ConversationTurn, question: Message) -> None:
        """Select or create history only after validating model input."""
        text = question.extract_plain_text()
        attachments = any(
            part.type in {"image", "file", "audio", "video"}
            for part in question
        )
        if len(text) > MAX_QUESTION_LENGTH or not (text.strip() or attachments):
            raise ConversationError(
                422, "Usage: /chat <question, 1-8000 characters>"
            )
        scope = turn.event.get_session_id()
        if scope not in self.selections:
            created = await self.get_store().create(turn.caller.id)
            self.selections[scope] = created.id
        selected = await self.selected(turn)
        await Matcher.send(
            result_message(
                "Conversation selected.",
                {"kind": "selection", "session_id": selected.id},
            )
        )
        await self.answer(turn, question, selected)

    async def help(self, turn: ConversationTurn, argument: str) -> None:
        """List available operations without touching native history."""
        no_arguments(argument)
        await Matcher.send(
            "/chat <question>\n/help\n/new [name]\n/sessions [limit] [offset]\n"
            "/use <id>\n/session\n/history [limit]\n/clear\n/delete <id>"
            + "".join(
                "\n/" + name
                for name in self.operations
                if name not in COMMAND_NAMES
            )
        )

    async def new(self, turn: ConversationTurn, argument: str) -> None:
        """Persist an empty owned session and select it in this scope."""
        name = argument or "New conversation"
        if len(name) > 120:
            raise ConversationError(
                422, "Conversation names accept at most 120 characters"
            )
        selected = await self.get_store().create(turn.caller.id, name)
        self.selections[turn.event.get_session_id()] = selected.id
        await Matcher.send(
            result_message(
                "Conversation created.",
                {
                    "session_id": selected.id,
                    "session": selected.model_dump(mode="json"),
                },
            )
        )

    async def sessions(self, turn: ConversationTurn, argument: str) -> None:
        """Return a bounded page without changing the current selection."""
        try:
            values = argument.split()
            if len(values) > 2:
                raise ValueError
            limit, offset = (
                (int(values[0]) if values else 50),
                (int(values[1]) if len(values) == 2 else 0),
            )
        except ValueError:
            raise ConversationError(
                422, "Usage: /sessions [limit] [offset]"
            ) from None
        page = await self.get_store().list(
            turn.caller.id, limit=limit, offset=offset
        )
        data = {
            "sessions": [item.model_dump(mode="json") for item in page],
            "limit": limit,
            "offset": offset,
            "next_offset": offset + limit if len(page) == limit else None,
        }
        await Matcher.send(
            result_message(
                json.dumps(data["sessions"], ensure_ascii=False, indent=2), data
            )
        )

    async def use(self, turn: ConversationTurn, argument: str) -> None:
        """Verify ownership before changing this scope's selection."""
        selected = await self.get_store().get(
            turn.caller.id, session_id(argument)
        )
        if selected.deleting:
            raise ConversationError(409, "Conversation is being deleted")
        self.selections[turn.event.get_session_id()] = selected.id
        await Matcher.send(
            result_message(
                "Conversation selected.",
                {
                    "session_id": selected.id,
                    "session": selected.model_dump(mode="json"),
                },
            )
        )

    async def session(self, turn: ConversationTurn, argument: str) -> None:
        """Return metadata for the selected SDK history."""
        no_arguments(argument)
        selected = await self.selected(turn)
        await Matcher.send(
            result_message(
                selected.model_dump_json(indent=2),
                {
                    "session_id": selected.id,
                    "session": selected.model_dump(mode="json"),
                },
            )
        )

    async def history(self, turn: ConversationTurn, argument: str) -> None:
        """Read a bounded snapshot of the selected native history."""
        try:
            limit = int(argument) if argument else 100
        except ValueError:
            raise ConversationError(422, "Usage: /history [limit]") from None
        selected = await self.selected(turn)
        items = await self.get_store().messages(
            turn.caller.id, selected.id, limit=limit
        )
        await Matcher.send(
            result_message(
                json.dumps(items, ensure_ascii=False, indent=2),
                {"session_id": selected.id, "messages": items, "limit": limit},
            )
        )

    async def clear(self, turn: ConversationTurn, argument: str) -> None:
        """Clear selected history while retaining its owned index."""
        no_arguments(argument)
        selected = await self.selected(turn)
        await self.get_store().clear(turn.caller.id, selected.id)
        await Matcher.send(
            result_message(
                "Conversation history cleared.", {"session_id": selected.id}
            )
        )

    async def delete(self, turn: ConversationTurn, argument: str) -> None:
        """Delete owned history and release this scope's selection."""
        selected = session_id(argument)
        await self.get_store().delete(turn.caller.id, selected)
        if self.selections.get(turn.event.get_session_id()) == selected:
            self.selections.pop(turn.event.get_session_id())
        await Matcher.send(
            result_message(
                "Conversation deleted.", {"deleted_session_id": selected}
            )
        )
