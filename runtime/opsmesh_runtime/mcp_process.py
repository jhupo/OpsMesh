"""Persistent MCP stdio sessions, controlled through a private runtime-local socket.

Only run this module inside an isolated Linux Runtime. Project code is started by
the official MCP SDK, never by an API or Worker host process.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from uuid import UUID

from .mcp_sdk import RuntimeMCPServerStdio

MAX_BYTES = 1_048_576
ROOT = Path("/tmp/opsmesh-mcp")


def socket_path(identifier: str) -> Path:
    return ROOT / f"{UUID(identifier).hex}.sock"


async def exchange(path: Path, request: dict[str, Any]) -> dict[str, Any]:
    async with asyncio.timeout(65):
        reader, writer = await asyncio.open_unix_connection(str(path), limit=MAX_BYTES)
        try:
            encoded = json.dumps(request).encode() + b"\n"
            if len(encoded) > MAX_BYTES:
                raise ValueError("MCP request is too large")
            writer.write(encoded)
            await writer.drain()
            response = json.loads(await reader.readline())
            if not isinstance(response, dict):
                raise ValueError("Invalid MCP process response")
            return response
        finally:
            writer.close()
            await writer.wait_closed()


class McpProcess:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.state = "starting"
        self.restarts = 0
        self.restart_policy = config["restart_policy"]
        self.started_at = time.monotonic()
        self.stopped = asyncio.Event()
        self.requests: asyncio.Queue[tuple[dict[str, Any], asyncio.Future[dict[str, Any]]]] = (
            asyncio.Queue(maxsize=32)
        )

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        future: asyncio.Future[dict[str, Any]] | None = None
        disconnected: asyncio.Task[bytes] | None = None
        try:
            async with asyncio.timeout(60):
                request = json.loads(await reader.readline())
                action = request.get("action")
                if action == "status":
                    result = {"status": self.state, "restarts": self.restarts}
                elif action == "stop":
                    self.stopped.set()
                    result = {"status": "stopping"}
                elif action in {"discover", "call"} and self.state == "running":
                    future = asyncio.get_running_loop().create_future()
                    self.requests.put_nowait((request, future))
                    disconnected = asyncio.create_task(reader.read(1))
                    await asyncio.wait((future, disconnected), return_when=asyncio.FIRST_COMPLETED)
                    if not future.done():
                        return
                    result = await future
                else:
                    result = {"error": "mcp_process_not_ready"}
        except Exception:
            result = {"error": "mcp_process_request_failed"}
        finally:
            if future is not None and not future.done():
                future.cancel()
            if disconnected is not None:
                disconnected.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await disconnected
            if reader.at_eof():
                writer.close()
                await writer.wait_closed()
        try:
            encoded = json.dumps(result).encode() + b"\n"
            if len(encoded) > MAX_BYTES:
                encoded = b'{"error":"mcp_process_response_too_large"}\n'
            writer.write(encoded)
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    async def run_session(self) -> None:
        # Do not forward project stderr: it can contain environment credentials.
        async with RuntimeMCPServerStdio(
            params={
                "command": self.config["command"],
                "args": self.config.get("args", []),
                "env": self.config.get("env"),
                "cwd": self.config.get("cwd"),
            },
            name="opsmesh-managed-mcp",
            client_session_timeout_seconds=50,
            max_retry_attempts=0,
        ) as server:
            session = server.session
            if session is None:
                raise RuntimeError("Native SDK MCP connection was not initialized")
            self.state = "running"
            while not self.stopped.is_set():
                try:
                    request, future = await asyncio.wait_for(self.requests.get(), timeout=5)
                except TimeoutError:
                    async with asyncio.timeout(10):
                        await session.send_ping()
                    continue
                if future.cancelled():
                    continue
                try:
                    timeout = min(50, max(1, int(request.get("timeout_seconds", 50))))
                    async with asyncio.timeout(timeout):
                        if request["action"] == "discover":
                            tools: list[dict[str, Any]] = []
                            cursor = None
                            seen: set[str] = set()
                            while True:
                                page = await session.list_tools(cursor=cursor)
                                tools.extend(
                                    t.model_dump(mode="json", by_alias=True, exclude_none=True)
                                    for t in page.tools
                                )
                                if len(tools) > 1000:
                                    raise ValueError("Too many MCP tools")
                                cursor = page.nextCursor
                                if not cursor:
                                    break
                                if cursor in seen:
                                    raise ValueError("Repeated MCP discovery cursor")
                                seen.add(cursor)
                            result = {"tools": tools}
                        else:
                            operation = asyncio.create_task(
                                server.call_tool(request["name"], request["arguments"])
                            )

                            def cancel_disconnected(
                                completed: asyncio.Future[dict[str, Any]],
                                running: asyncio.Task[Any] = operation,
                            ) -> None:
                                if completed.cancelled():
                                    running.cancel()

                            future.add_done_callback(cancel_disconnected)
                            try:
                                response = await operation
                            except asyncio.CancelledError:
                                if future.cancelled():
                                    continue
                                raise
                            finally:
                                operation.cancel()
                                with contextlib.suppress(asyncio.CancelledError):
                                    await operation
                            result = response.model_dump(
                                mode="json", by_alias=True, exclude_none=True
                            )
                    if not future.done():
                        future.set_result(result)
                except Exception:
                    self.state = "restarting"
                    if not future.done():
                        # Never replay a tool after an ambiguous transport failure.
                        future.set_result({"error": "mcp_process_call_failed"})
                    raise

    async def supervise(self) -> None:
        while not self.stopped.is_set():
            try:
                self.started_at = time.monotonic()
                await self.run_session()
                return
            except Exception:
                self.state = "restarting"
                while not self.requests.empty():
                    _, pending = self.requests.get_nowait()
                    if not pending.done():
                        pending.set_result({"error": "mcp_process_not_ready"})
                if (
                    time.monotonic() - self.started_at
                    >= self.restart_policy["stable_after_seconds"]
                ):
                    self.restarts = 0
                if self.restarts >= self.restart_policy["max_restarts"]:
                    self.state = "failed"
                    await self.stopped.wait()
                    return
                self.restarts += 1
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(
                        self.stopped.wait(),
                        timeout=min(
                            self.restart_policy["max_backoff_seconds"],
                            self.restart_policy["initial_backoff_seconds"]
                            * (2 ** min(self.restarts - 1, 30)),
                        ),
                    )


async def serve(identifier: str, config: dict[str, Any]) -> None:
    import fcntl

    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = socket_path(identifier)
    with path.with_suffix(".lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        path.unlink(missing_ok=True)
        process = McpProcess(config)
        server = await asyncio.start_unix_server(process.handle, path=str(path), limit=MAX_BYTES)
        os.chmod(path, 0o600)
        task = asyncio.create_task(process.supervise())
        try:
            await process.stopped.wait()
        finally:
            server.close()
            await server.wait_closed()
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            path.unlink(missing_ok=True)


async def control(request: dict[str, Any]) -> dict[str, Any]:
    identifier = str(request["id"])
    path = socket_path(identifier)
    if request["action"] != "start":
        try:
            result = await exchange(path, request)
            if request["action"] == "stop":
                # Restart/admission must wait for SDK cleanup and socket removal.
                for _ in range(100):
                    if not path.exists():
                        return {"status": "stopped"}
                    await asyncio.sleep(0.1)
                return {"error": "mcp_process_stop_failed"}
            return result
        except (FileNotFoundError, ConnectionRefusedError):
            return {"status": "stopped"}
    try:
        current = await exchange(path, {"action": "status"})
        if current.get("status") == "running":
            return current
        if current.get("status") == "failed":
            return {"error": "mcp_process_restart_required"}
    except (FileNotFoundError, ConnectionRefusedError):
        home = Path(f"/workspace/mcp/{UUID(identifier)}")
        (home / "tmp").mkdir(mode=0o700, parents=True, exist_ok=True)
        child = subprocess.Popen(
            [sys.executable, "-m", "opsmesh_runtime.mcp_process", "--serve", identifier],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        assert child.stdin is not None
        try:
            child.stdin.write(json.dumps(request["server"]).encode())
        finally:
            child.stdin.close()
    for _ in range(110):
        await asyncio.sleep(0.5)
        try:
            current = await exchange(path, {"action": "status"})
            if current.get("status") == "running":
                return current
            if current.get("status") == "failed":
                break
        except (FileNotFoundError, ConnectionRefusedError):
            continue
    return {"error": "mcp_process_start_failed"}


def main() -> int:
    try:
        if len(sys.argv) == 3 and sys.argv[1] == "--serve":
            raw = sys.stdin.buffer.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise ValueError("Too large")
            asyncio.run(serve(sys.argv[2], json.loads(raw)))
            return 0
        if len(sys.argv) != 3 or sys.argv[1] != "--request-file":
            return 2
        with open(sys.argv[2], "rb") as source:
            raw = source.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("Too large")
        result = asyncio.run(control(json.loads(raw)))
        print(json.dumps(result))
        return 1 if "error" in result else 0
    except Exception:
        print('{"error":"mcp_process_control_failed"}')
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
