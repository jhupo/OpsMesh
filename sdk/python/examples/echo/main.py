from opsmesh_sdk import PluginContext

from .tools import echo_prefix


def setup(ctx: PluginContext) -> None:
    ctx.tool(
        "echo_prefix",
        echo_prefix,
        description="Return a message with a configured prefix",
        config_fields=[{"name": "prefix", "type": "text", "default": "Hello, "}],
    )
