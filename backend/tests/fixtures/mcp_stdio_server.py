from mcp.server.fastmcp import FastMCP

server = FastMCP("opsmesh-test-stdio")


@server.tool()
def echo(message: str) -> dict[str, str]:
    return {"message": message}


if __name__ == "__main__":
    server.run(transport="stdio")
