"""Expose conversation commands and an explicit workspace reset."""

from conversation_support.deliveries import ConversationDeliveries
from conversation_support.entrance import (
    ConversationEntrance,
    ConversationTurn,
    no_arguments,
    result_message,
)
from conversation_support.files import Deliverables

from opsmesh import on_shutdown
from opsmesh.matchers import Matcher

from . import filesystem
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


async def files(turn: ConversationTurn, argument: str) -> None:
    """List saved file replies or republish a selected reply's fixed files."""
    selected = await entrance.selected(turn)
    async with get_conversations().hold(turn.caller.id, selected.id):
        if not argument:
            rows = await ConversationDeliveries.history(
                filesystem, selected.id, turn.caller.id
            )
            await Matcher.send(
                result_message(
                    "\n".join(row["reply_id"] for row in rows)
                    or "No saved files.",
                    {
                        "deliveries": [
                            {
                                "id": row["reply_id"],
                                "status": row["attempt"]["status"],
                                "files": [
                                    item["name"] for item in row["files"]
                                ],
                            }
                            for row in rows
                        ]
                    },
                )
            )
        else:
            selected_files = Deliverables(turn.event.attachments)
            message = await ConversationDeliveries.restore(
                filesystem,
                (selected.id, turn.caller.id, argument),
                selected_files,
            )
            archive = ConversationDeliveries(
                filesystem, selected.id, turn.caller.id
            )

            async def reply():
                yield message

            await turn.channel.call(
                "stream",
                event=turn.event,
                source=archive.messages(reply(), selected_files),
                delivery=archive.delivery,
            )


entrance.add_command("files", files)
entrance.add_command("reset_workspace", reset_workspace)
entrance.bind()


@on_shutdown
def forget_selections() -> None:
    """Release window mappings after admitted conversations drain."""
    entrance.forget_selections()
