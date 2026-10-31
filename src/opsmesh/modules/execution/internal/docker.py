"""Bounded Docker CLI control; no container is started on module import."""

import asyncio
import os

from opsmesh.framework.errors import OpsMeshError

from .processes import terminate_tree


async def docker_control(*arguments: str) -> str:
    try:
        process = await asyncio.create_subprocess_exec(
            "docker",
            *arguments,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=os.name != "nt",
        )
        try:
            async with asyncio.timeout(30):
                output, _ = await process.communicate()
        except (TimeoutError, asyncio.CancelledError):
            await terminate_tree(process)
            raise
        if process.returncode:
            raise OpsMeshError("ENVIRONMENT_UNAVAILABLE", "Docker operation failed")
        return output.decode().strip()
    except OSError as error:
        raise OpsMeshError("ENVIRONMENT_UNAVAILABLE", "Docker CLI is unavailable") from error
