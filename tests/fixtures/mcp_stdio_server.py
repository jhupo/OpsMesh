import os

from mcp.server.fastmcp import FastMCP

server = FastMCP("opsmesh-test-stdio")


@server.tool()
def echo(message: str) -> dict[str, str]:
    return {"message": message}


@server.tool()
def environment_configured(name: str) -> dict[str, bool]:
    return {"configured": bool(os.getenv(name))}


if __name__ == "__main__":
    server.run(transport="stdio")
