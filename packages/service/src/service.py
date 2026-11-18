"""Run one host and attach the deployed service's process interfaces."""

import asyncio
import os
import signal
import socket

import anyio

from .control import serve_control
from .host import HostManifest, load_host


def _notify(state: str) -> None:
    address = os.environ.get("NOTIFY_SOCKET")
    if address:
        if address.startswith("@"):
            address = "\0" + address[1:]
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as channel:
            channel.connect(address)
            channel.sendall(state.encode())


async def serve(
    manifest: HostManifest, *, stop_event: anyio.Event | None = None
) -> None:
    """Run the deployed host and close it on SIGTERM."""
    loop = asyncio.get_running_loop()
    task = asyncio.current_task()
    listening = False
    if task is not None:
        try:
            loop.add_signal_handler(signal.SIGTERM, task.cancel)
            listening = True
        except NotImplementedError:
            pass
    try:
        async with (
            load_host(manifest) as host,
            host.driver.lifespan(),
            serve_control(host),
        ):
            _notify("READY=1")
            try:
                if stop_event is None:
                    await anyio.sleep_forever()
                else:
                    await stop_event.wait()
            finally:
                _notify("STOPPING=1")
    finally:
        if listening:
            loop.remove_signal_handler(signal.SIGTERM)
