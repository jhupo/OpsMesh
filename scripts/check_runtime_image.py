"""Verify a compute image through the official SDK sandbox lifecycle and MCP SDK."""

from __future__ import annotations

import argparse
import asyncio
import io
import shutil
from contextlib import closing
from pathlib import Path

import docker
from agents.mcp import MCPServerStdio
from agents.sandbox import Manifest
from agents.sandbox.sandboxes import DockerSandboxClient, DockerSandboxClientOptions


async def probe(image: str, fixture: Path) -> None:
    with closing(docker.from_env()) as daemon:
        client = DockerSandboxClient(daemon)
        session = await client.create(
            manifest=Manifest(),
            options=DockerSandboxClientOptions(image=image),
        )
        try:
            await session.start()
            await session.write(Path("/workspace/mcp_fixture.py"), io.BytesIO(fixture.read_bytes()))
            result = await session.exec(
                "python",
                "-c",
                "import mcp; print('native-mcp-ready')",
                shell=False,
            )
            if result.exit_code != 0:
                raise RuntimeError("Compute image cannot load the MCP SDK")
            executable = shutil.which("docker")
            if executable is None:
                raise RuntimeError("Compute acceptance requires Docker CLI for duplex stdio")
            async with MCPServerStdio(
                params={
                    "command": executable,
                    "args": [
                        "exec",
                        "-i",
                        session.state.container_id,
                        "python",
                        "/workspace/mcp_fixture.py",
                    ],
                },
                max_retry_attempts=0,
                client_session_timeout_seconds=30,
            ) as server:
                tools = await server.list_tools()
                if "echo" not in {tool.name for tool in tools}:
                    raise RuntimeError("Native MCP discovery did not return the fixture tool")
                response = await server.call_tool("echo", {"message": "native-stdio-ready"})
                if response.is_error or response.structured_content != {
                    "message": "native-stdio-ready"
                }:
                    raise RuntimeError("Native MCP tool result failed protocol validation")
            state = client.serialize_session_state(session.state)
            resumed = await client.resume(client.deserialize_session_state(state))
            content = await resumed.read(Path("/workspace/mcp_fixture.py"))
            with content:
                assert content.read() == fixture.read_bytes()
        finally:
            await session.shutdown()
    print("Native SDK sandbox lifecycle, file I/O and MCP stdio round trip verified")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--fixture", type=Path, default=Path("tests/fixtures/mcp_stdio_server.py"))
    args = parser.parse_args()
    asyncio.run(probe(args.image, args.fixture.resolve(strict=True)))


if __name__ == "__main__":
    main()
