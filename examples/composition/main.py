from typing import Any

from opsmesh_sdk import InvocationContext, PluginContext


async def compose(context: InvocationContext, value: Any) -> dict[str, Any]:
    analysis = await context.dependencies.call("analyst", value, key="analysis")
    return {"analysis": analysis}


def setup(context: PluginContext) -> None:
    context.entry("analyze", compose)
