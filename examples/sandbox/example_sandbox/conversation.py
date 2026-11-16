"""Stream SDK execution within the owned sandbox and history scopes."""

from agents import ModelSettings, RunConfig
from agents.sandbox import SandboxRunConfig
from conversation_support.deliveries import ConversationDeliveries
from conversation_support.entrance import ConversationTurn
from conversation_support.files import Deliverables
from conversation_support.inputs import sandbox_input
from conversation_support.outputs import ConversationOutput

from opsmesh.messages import Message
from opsmesh.plugins.channels.attachments import AttachmentError

from . import agent as definition
from . import execution, filesystem
from .lifecycle import get_conversations
from .schemas import ConversationView
from .sessions import ConversationError
from .tools.files import SandboxFiles


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
            files = Deliverables(turn.event.attachments)
            publisher = SandboxFiles(sandbox, files)
            archive = ConversationDeliveries(
                filesystem, selected.id, turn.caller.id
            )
            agent = definition.agent.clone(
                tools=[*definition.agent.tools, publisher.tool],
                model_settings=definition.agent.model_settings.resolve(
                    ModelSettings(parallel_tool_calls=False)
                ),
            )
            async with execution.stream(
                agent,
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
                    "stream",
                    event=turn.event,
                    delivery=archive.delivery,
                    source=archive.messages(
                        ConversationOutput(
                            format=definition.settings.output_format,
                            channel=turn.channel,
                            publication=definition.settings.publication,
                            files=files,
                        ).stream(result),
                        files,
                    ),
                )
