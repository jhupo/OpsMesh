"""Select agents with ordinary framework group rules."""

from typing import Annotated

from opsmesh import on_message
from opsmesh.adapters import Event
from opsmesh.params import Context
from opsmesh.plugins.channels import UserContext

from . import agents, channels, execution

sales = on_message(
    rule=channels.in_group("group-a", connection="main"), block=True
)
technical = on_message(
    rule=channels.in_group("group-b", connection="main"), block=True
)


@sales.handle()
async def answer_sales(
    event: Event, caller: Annotated[UserContext, Context()]
) -> None:
    """Execute the sales agent for text received from its configured group."""
    result = await execution.run(
        agents.sales, event.get_plaintext(), context=caller
    )
    await sales.finish(result.final_output)


@technical.handle()
async def answer_technical(
    event: Event, caller: Annotated[UserContext, Context()]
) -> None:
    """Execute the technical agent through the same robot connection."""
    result = await execution.run(
        agents.technical, event.get_plaintext(), context=caller
    )
    await technical.finish(result.final_output)
