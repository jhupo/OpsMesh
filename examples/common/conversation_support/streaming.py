"""Convert native SDK stream events while its execution scope is active."""

from collections.abc import AsyncIterator

from agents import RunResultStreaming
from openai.types.responses import ResponseTextDeltaEvent


async def text_deltas(result: RunResultStreaming) -> AsyncIterator[str]:
    """Yield native text deltas and propagate terminal execution failures."""
    async for event in result.stream_events():
        if event.type == "raw_response_event" and isinstance(
            event.data, ResponseTextDeltaEvent
        ):
            yield event.data.delta
    if result.interruptions:
        raise RuntimeError(
            "Approval continuation is not supported by this example"
        )
    if not isinstance(result.final_output, str):
        raise TypeError("The conversation must return plain text")
