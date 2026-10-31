"""Bounded output, workspace-relative working directories and process-tree termination."""

import asyncio
import contextlib
import os
import signal
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, cast

from opsmesh.framework.errors import OpsMeshError


async def terminate_tree(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    if os.name == "nt":
        killer = await asyncio.create_subprocess_exec(
            "taskkill",
            "/PID",
            str(process.pid),
            "/T",
            "/F",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await killer.wait()
    else:
        with contextlib.suppress(ProcessLookupError):
            cast(Any, os).killpg(process.pid, cast(Any, signal).SIGKILL)
    with contextlib.suppress(ProcessLookupError):
        process.kill()
    await process.wait()


def command_cwd(root: Path, relative: str) -> Path:
    value = PurePosixPath(relative)
    if (
        not relative
        or "\\" in relative
        or ":" in relative
        or value.is_absolute()
        or PureWindowsPath(relative).drive
        or ".." in value.parts
    ):
        raise OpsMeshError("PATH_INVALID", "Command cwd must be workspace-relative")
    result = root.joinpath(*value.parts)
    for path in (result, *result.parents):
        if path == root.parent:
            break
        if path.is_symlink():
            raise OpsMeshError("PATH_INVALID", "Command cwd may not traverse symlinks")
    if not result.resolve().is_relative_to(root) or not result.is_dir():
        raise OpsMeshError("PATH_INVALID", "Command cwd is unavailable")
    return result


async def bounded_read(stream: asyncio.StreamReader, limit: int) -> tuple[bytes, bool]:
    chunks: list[bytes] = []
    retained = 0
    truncated = False
    while chunk := await stream.read(65536):
        remaining = max(0, limit - retained)
        if remaining:
            chunks.append(chunk[:remaining])
        retained += min(len(chunk), remaining)
        truncated = truncated or len(chunk) > remaining
    return b"".join(chunks), truncated
