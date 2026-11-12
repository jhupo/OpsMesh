"""Linux executable commands for startup, validation, and migrations."""

from __future__ import annotations

import argparse
import asyncio
import importlib
import inspect
import logging
import os
import signal
import socket
import traceback
from importlib.metadata import version
from pathlib import Path

import anyio

from opsmesh.app import load_host, load_manifest
from opsmesh.framework.plugins import (
    get_loaded_plugins,
    get_plugin_by_module_name,
)

logger = logging.getLogger(__name__)


async def _maintenance(manifest_path: Path, command: str | None) -> None:
    """Validate declarations and optionally call an enabled plugin command."""
    manifest = load_manifest(manifest_path)
    async with load_host(manifest, manifest_dir=manifest_path.parent):
        for plugin in get_loaded_plugins():
            if plugin.metadata and plugin.metadata.config:
                plugin.metadata.config.model_validate(
                    plugin.config_values or {}
                )
        if command is None:
            return
        module_name, separator, attribute = command.partition(":")
        if not separator or not attribute.isidentifier():
            raise ValueError("Commands use plugin.module:function syntax")
        if get_plugin_by_module_name(module_name) is None:
            raise ValueError(
                "The command must belong to an enabled, loaded plugin"
            )
        module = importlib.import_module(module_name)
        handler = getattr(module, attribute)
        if not callable(handler) or attribute.startswith("_"):
            raise ValueError("A command must be a public callable")
        result = handler()
        if inspect.isawaitable(result):
            await result


def _notify(state: str) -> None:
    """Report readiness to systemd without a libsystemd dependency."""
    address = os.environ.get("NOTIFY_SOCKET")
    if not address:
        return
    if address.startswith("@"):
        address = "\0" + address[1:]
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as channel:
        channel.connect(address)
        channel.sendall(state.encode())


async def _serve(manifest_path: Path) -> None:
    """Translate systemd SIGTERM into cancellation with lifecycle cleanup."""
    task = asyncio.current_task()
    loop = asyncio.get_running_loop()
    if task is not None and hasattr(signal, "SIGTERM"):
        try:
            loop.add_signal_handler(signal.SIGTERM, task.cancel)
        except NotImplementedError:
            pass
    try:
        async with load_host(
            load_manifest(manifest_path), manifest_dir=manifest_path.parent
        ) as driver:
            async with driver.lifespan():
                _notify("READY=1")
                try:
                    await anyio.sleep_forever()
                finally:
                    _notify("STOPPING=1")
    finally:
        try:
            loop.remove_signal_handler(signal.SIGTERM)
        except NotImplementedError:
            pass


def main(argv: list[str] | None = None) -> int:
    """Run the installed host or one explicit maintenance command."""
    parser = argparse.ArgumentParser(prog="opsmesh")
    parser.add_argument(
        "--version", action="version", version=version("opsmesh")
    )
    parser.add_argument("--manifest", required=True, type=Path)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--check", action="store_true")
    actions.add_argument(
        "--command",
        metavar="PLUGIN.MODULE:FUNCTION",
        help="Run a public command owned by an enabled plugin, then exit",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        if args.check or args.command:
            asyncio.run(_maintenance(args.manifest, args.command))
        else:
            asyncio.run(_serve(args.manifest))
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("Host stopped by signal")
    except Exception as error:
        # Validation and connection errors may contain credentials or DSNs.
        logger.error(
            "Command failed (%s); check configuration and service logs",
            type(error).__name__,
        )
        for frame in traceback.extract_tb(error.__traceback__):
            logger.error(
                "  %s:%s in %s", frame.filename, frame.lineno, frame.name
            )
        return 1
    return 0
