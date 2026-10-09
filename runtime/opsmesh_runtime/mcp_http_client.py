"""Execute native SDK HTTP/SSE MCP calls inside the authorized Runtime."""

import asyncio
import json
import sys

from agents.mcp import MCPServerSse, MCPServerStreamableHttp


async def execute_request(request: dict[str, object]) -> dict[str, object]:
    if request.get("contract_version") != 2 or request.get("transport") not in {"http", "sse"}:
        raise ValueError("Unsupported Runtime MCP HTTP contract")
    params = request.get("server")
    tool = request.get("tool")
    if not isinstance(params, dict) or not isinstance(tool, dict):
        raise ValueError("Invalid Runtime MCP HTTP request")
    timeout = tool.get("timeout_seconds")
    if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout < 1 or timeout > 3600:
        raise ValueError("Invalid Runtime MCP HTTP timeout")
    arguments = tool.get("arguments")
    name = tool.get("name")
    if not isinstance(arguments, dict) or not isinstance(name, str):
        raise ValueError("Invalid Runtime MCP HTTP tool")
    server_class = MCPServerStreamableHttp if request["transport"] == "http" else MCPServerSse
    async with asyncio.timeout(timeout):
        async with server_class(
            params=params,
            name="opsmesh-runtime-http",
            client_session_timeout_seconds=timeout,
            max_retry_attempts=0,
        ) as server:
            result = await server.call_tool(name, arguments)
            return result.model_dump(mode="json", by_alias=True, exclude_none=True)


def main() -> int:
    if len(sys.argv) != 3 or sys.argv[1] != "--request-file":
        return 2
    try:
        with open(sys.argv[2], "rb") as stream:
            raw = stream.read(1_048_577)
        if len(raw) > 1_048_576:
            raise ValueError("Runtime MCP request exceeds limit")
        request = json.loads(raw)
        if not isinstance(request, dict):
            raise ValueError("Invalid Runtime MCP request")
        result = asyncio.run(execute_request(request))
    except Exception as error:
        print(f"Runtime MCP HTTP failed: {type(error).__name__}", file=sys.stderr)
        return 1
    print(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
