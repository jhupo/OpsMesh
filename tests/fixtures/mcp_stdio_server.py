import os
import sys

from mcp.server import MCPServer
from mcp.server.mcpserver import Context

server = MCPServer("opsmesh-test-stdio")


@server.tool()
async def echo(message: str, ctx: Context) -> dict[str, str]:
    if message == "diagnostic-fixture":
        await ctx.log(
            "error",
            {
                "tool_call_id": (ctx.request_context.meta or {}).get("opsmesh_tool_call_id"),
                "com.opsmesh/diagnostic": {
                    "exception_type": "ReadTimeout",
                    "http_status": 503,
                    "password": "private-notification-canary",
                },
                "message": "password=private-notification-canary",
            },
        )
    return {"message": message}


@server.tool()
def environment_configured(name: str) -> dict[str, bool]:
    return {"configured": bool(os.getenv(name))}


@server.tool()
def emit_private_stderr() -> dict[str, bool]:
    print(os.getenv("MCP_PRIVATE_FIXTURE", ""), file=sys.stderr, flush=True)
    return {"emitted": True}


if __name__ == "__main__":
    server.run(transport="stdio")
