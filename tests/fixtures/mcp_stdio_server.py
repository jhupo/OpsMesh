"""Standalone real MCP echo server for released compute image acceptance."""

from mcp.server.mcpserver import MCPServer

server = MCPServer("OpsMesh compute acceptance")


@server.tool()
def echo(message: str) -> dict[str, str]:
    """Return the input to verify the full stdio round trip."""
    return {"message": message}


if __name__ == "__main__":
    server.run(transport="stdio")
