from opsmesh_sdk import PluginContext


def setup(ctx: PluginContext) -> None:
    ctx.skills("skills")
