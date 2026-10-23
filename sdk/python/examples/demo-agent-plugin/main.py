import logging

from opsmesh_sdk import AgentHookEvent, PluginContext

logger = logging.getLogger(__name__)


def count_lines(text: str) -> int:
    """Count the lines in supplied text."""
    return len(text.splitlines())


async def tool_finished(event: AgentHookEvent) -> None:
    logger.info("Demo plugin tool finished", extra={"hook_point": event.point})


def setup(ctx: PluginContext) -> None:
    ctx.tool("count_lines", count_lines)
    ctx.skills("skills")
    ctx.hook("tool_end", tool_finished, priority=100)
