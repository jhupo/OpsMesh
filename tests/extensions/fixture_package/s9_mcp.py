"""An actual stdio MCP server used by project template acceptance."""

import os

from mcp.server.mcpserver import MCPServer

server = MCPServer("S9 project MCP")


@server.tool()
def project_probe() -> str:
    """Validate that the explicitly bound non-secret environment reached this process."""
    assert os.environ.get("EXAMPLE_LABEL") == "s8-marker"
    return "PROJECT_MCP_OK"


if __name__ == "__main__":
    server.run(transport="stdio")
