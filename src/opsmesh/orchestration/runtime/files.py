"""Bounded sandbox file transfer through the native public client."""

import base64
from io import BytesIO
from pathlib import Path

from agents.sandbox.session import SandboxSession

from opsmesh.shared.files import validate_relative_path

MAX_FILE_BYTES = 4_000_000
SANDBOX_WORKDIR = "/workspace"

# The pinned public read() buffers the whole file in Docker before returning a
# stream. Execute a bounded read inside the sandbox instead. Open each component
# relative to an already opened directory, refusing symlinks and special files.
# This also avoids a check-then-open race against an untrusted sandbox process.
_READ_FILE_SCRIPT = """
import os
import stat
import sys

directory = os.open(sys.argv[1], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
try:
    parts = sys.argv[2].split('/')
    if any(part in ('', '.', '..') for part in parts):
        raise ValueError('Invalid path')
    for part in parts[:-1]:
        child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
        os.close(directory)
        directory = child
    descriptor = os.open(
        parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory
    )
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError('Only regular files may be transferred')
        remaining = int(sys.argv[3]) + 1
        while remaining:
            data = os.read(descriptor, min(remaining, 65536))
            if not data:
                break
            sys.stdout.buffer.write(data)
            remaining -= len(data)
    finally:
        os.close(descriptor)
finally:
    os.close(directory)
"""


def sandbox_path(value: str) -> Path:
    return Path(SANDBOX_WORKDIR) / validate_relative_path(value)


async def read_file(session: SandboxSession, path: str) -> str:
    relative = validate_relative_path(path)
    result = await session.exec(
        "python3",
        "-I",
        "-c",
        _READ_FILE_SCRIPT,
        SANDBOX_WORKDIR,
        relative,
        str(MAX_FILE_BYTES),
        shell=False,
        timeout=60,
    )
    if result.exit_code != 0:
        raise ValueError("Sandbox file could not be read safely")
    if len(result.stdout) > MAX_FILE_BYTES:
        raise ValueError("Sandbox file exceeds the transfer limit")
    return base64.b64encode(result.stdout).decode("ascii")


async def write_file(session: SandboxSession, path: str, encoded: str) -> int:
    data = base64.b64decode(encoded, validate=True)
    if len(data) > MAX_FILE_BYTES:
        raise ValueError("Sandbox file exceeds the transfer limit")
    with BytesIO(data) as stream:
        await session.write(sandbox_path(path), stream)
    return len(data)
