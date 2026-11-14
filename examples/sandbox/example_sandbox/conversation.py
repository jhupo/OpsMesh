"""Stream SDK execution within the owned sandbox and history scopes."""

from agents import RunConfig
from agents.sandbox import SandboxRunConfig
from conversation_support.entrance import ConversationTurn
from conversation_support.inputs import sandbox_input
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
    """Keep the native sandbox live until streaming and cleanup finish."""
    conversations = get_conversations()
    async with conversations.hold(turn.caller.id, selected.id):
        async with conversations.workspaces.open(selected.id) as sandbox:
            try:
                model_message = await sandbox_input(
                    message, turn.event.attachments, sandbox
                )
            except AttachmentError as error:
                raise ConversationError(422, str(error)) from error
            async with execution.stream(
                definition.agent,
                model_message,
                context=turn.caller,
                session=conversations.history(selected.id),
                run_config=RunConfig(sandbox=SandboxRunConfig(session=sandbox)),
                max_turns=definition.settings.max_turns,
                plugin_id="example_sandbox",
                trace_context=execution.TraceContext(
                    user_id=turn.caller.id, session_id=selected.id
                ),
            ) as result:
                await turn.channel.call(
                    "stream", event=turn.event, source=text_deltas(result)
                )
