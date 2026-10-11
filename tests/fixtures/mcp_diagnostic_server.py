"""Offline native MCP fixture: structured notifications and hostile stderr."""

import json
import sys

from mcp.server.fastmcp import Context, FastMCP

mcp = FastMCP("Diagnostic fixture")


@mcp.tool()
async def fail(ctx: Context) -> str:
    sys.stderr.write("password=diagnostic-canary\n" + "diagnostic-canary" * 1000 + "\n")
    for _ in range(50):
        print(
            json.dumps(
                {
                    "category": "network",
                    "phase": "login",
                    "exception_type": "ReadTimeout",
                    "password": "diagnostic-canary",
                }
            ),
            file=sys.stderr,
            flush=True,
        )
    await ctx.log(
        "error",
        {
            "category": "http",
            "phase": "business_request",
            "exception_type": "HTTPStatusError",
            "http_status": 503,
            "duration_ms": 25,
            "retryable": True,
            "workspace_id": "forged",
            "password": "diagnostic-canary",
        },
    )
    raise ValueError("Upstream unavailable")


if __name__ == "__main__":
    mcp.run()
