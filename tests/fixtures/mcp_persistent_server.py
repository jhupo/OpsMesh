"""Stateful standard MCP project used for persistent-session recovery checks."""

import os

from mcp.server.fastmcp import FastMCP

server = FastMCP("opsmesh-persistent-test")
calls = 0


@server.tool()
def count() -> dict[str, int | bool]:
    global calls
    calls += 1
    return {"calls": calls, "pid": os.getpid(), "configured": bool(os.getenv("TEST_PASSWORD"))}


@server.tool()
def crash() -> None:
    os._exit(23)


if __name__ == "__main__":
    server.run(transport="stdio")
