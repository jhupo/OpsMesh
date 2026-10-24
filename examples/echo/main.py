from typing import Any

from opsmesh_sdk import InvocationContext, PluginContext


async def invoke(context: InvocationContext, value: Any) -> Any:
    await context.events.emit("progress", {"message": "Echoing input"})
    return {"user_id": context.user.user_id, "input": value}


def setup(context: PluginContext) -> None:
    context.entry("echo", invoke)
