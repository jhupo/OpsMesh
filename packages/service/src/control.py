"""Private local control endpoint for a running Linux host."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import stat
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from opsmesh.plugins.errors import PluginOperationBlocked

from .catalog import PluginProtectedError

if TYPE_CHECKING:
    from .host import Host

logger = logging.getLogger(__name__)
_MAX_REQUEST = 4096
_MAX_CLIENTS = 16
ACTIONS = ("status", "load", "start", "stop", "restart")


def endpoint(root: Path, runtime_dir: Path | None = None) -> Path:
    """Use the same endpoint for clients and the host holding the data lock."""
    return (
        runtime_dir if runtime_dir is not None else root / "control"
    ) / "host.sock"


def _prepare_endpoint(path: Path) -> None:
    directory = path.parent.lstat()
    if (
        not stat.S_ISDIR(directory.st_mode)
        or directory.st_uid != os.geteuid()
        or directory.st_mode & 0o077
    ):
        raise PermissionError(
            "Control directory must be private and host-owned"
        )
    try:
        existing = path.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISSOCK(existing.st_mode) or existing.st_uid != os.geteuid():
        raise FileExistsError("Control endpoint is not an owned socket")
    # The caller holds the runtime directory lock; this socket is stale.
    path.unlink()


async def _dispatch(host: Host, line: bytes) -> dict:
    try:
        request = json.loads(line)
        module, action = request["module"], request["action"]
        if (
            request["version"] != 1
            or action not in ACTIONS
            or not isinstance(module, str)
            or not all(part.isidentifier() for part in module.split("."))
        ):
            raise ValueError
    except (ValueError, KeyError, TypeError, RecursionError):
        return {"error": "Invalid control request"}
    try:
        if action == "status":
            return {"result": host.plugin_status(module)}
        return {"result": await host.control_plugin(module, action)}
    except (PluginOperationBlocked, PluginProtectedError) as error:
        return {"error": str(error)}
    except Exception as error:
        logger.error(
            "Plugin control %s %s failed (%s)",
            action,
            module,
            type(error).__name__,
        )
        return {"error": f"{action} failed ({type(error).__name__})"}


@asynccontextmanager
async def serve_control(
    host: Host, *, runtime_dir: Path | None = None
) -> AsyncIterator[None]:
    """Accept one bounded request per local connection."""
    if os.name != "posix":
        yield
        return
    import fcntl

    path = endpoint(host.storage.root, runtime_dir)
    path.parent.mkdir(mode=0o700, exist_ok=True)
    directory = os.open(
        path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    )
    try:
        fcntl.flock(directory, fcntl.LOCK_EX | fcntl.LOCK_NB)
        async with _listen_control(host, path):
            yield
    finally:
        os.close(directory)


@asynccontextmanager
async def _listen_control(host: Host, path: Path) -> AsyncIterator[None]:
    _prepare_endpoint(path)
    sessions = 0
    closing = False

    async def handle(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        nonlocal sessions
        try:
            try:
                line = await asyncio.wait_for(reader.readline(), timeout=5)
            except (ValueError, TimeoutError):
                reply = {"error": "Invalid or incomplete control request"}
            else:
                if not line or closing:
                    return
                reply = await _dispatch(host, line)
            writer.write(json.dumps(reply).encode() + b"\n")
            await writer.drain()
        except ConnectionError:
            pass
        finally:
            writer.close()
            sessions -= 1

    def connected(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        nonlocal sessions
        if closing or sessions >= _MAX_CLIENTS:
            writer.close()
            return
        sessions += 1
        host.driver.task_group.start_soon(handle, reader, writer)

    server = await asyncio.start_unix_server(
        connected, path, limit=_MAX_REQUEST
    )
    try:
        yield
    finally:
        closing = True
        server.close()
        path.unlink(missing_ok=True)


async def send_control(
    root: Path, action: str, module: str, *, runtime_dir: Path | None = None
) -> dict:
    """Send a request to the existing service without opening its catalog."""
    path = endpoint(root, runtime_dir)
    try:
        reader, writer = await asyncio.open_unix_connection(path)
    except (OSError, AttributeError) as error:
        raise ConnectionError(
            f"Cannot connect to running host at {path}"
        ) from error
    try:
        request = {"version": 1, "action": action, "module": module}
        writer.write(json.dumps(request).encode() + b"\n")
        await writer.drain()
        line = await reader.readline()
        if not line:
            raise ConnectionError("Host closed the control connection")
        return json.loads(line)
    finally:
        writer.close()
        await writer.wait_closed()
