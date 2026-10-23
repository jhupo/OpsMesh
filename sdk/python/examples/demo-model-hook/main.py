from opsmesh_sdk import ModelMiddleware, ModelRequest, PluginContext


class ConciseResponses(ModelMiddleware):
    async def before_model(self, request: ModelRequest) -> None:
        request.instructions = (request.instructions or "") + "\nKeep responses concise."


def setup(ctx: PluginContext) -> None:
    ctx.middleware(ConciseResponses(), priority=100)
