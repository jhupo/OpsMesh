"""Linux executable commands for startup, validation, and migrations."""

from __future__ import annotations

import argparse
import asyncio
import importlib
import inspect
import json
import logging
import traceback
from importlib.metadata import version
from pathlib import Path

from opsmesh.plugins import (
    get_loaded_plugins,
    get_plugin_by_module_name,
)
from opsmesh.plugins.config import (
    PluginConfiguration,
    validate_dependency_config,
)

from .catalog import PluginRecord
from .control import ACTIONS, send_control
from .host import HostManifest, load_host, load_manifest
from .service import serve

logger = logging.getLogger(__name__)


async def _plugins(manifest: HostManifest, args: argparse.Namespace) -> None:
    """Manage records for existing plugins without installing packages."""
    async with load_host(manifest, load_entries=False) as host:
        if args.plugin_action == "list":
            for record in host.list_plugins():
                print(
                    f"{record.module}\tenabled={record.enabled}\tautoload={record.autoload}"
                    f"\tprotected={record.protected}"
                )
            return
        if not args.module:
            raise ValueError("A plugin module is required")
        configuration = (
            PluginConfiguration.model_validate_json(
                args.config.read_text(encoding="utf-8")
            )
            if args.config is not None
            else PluginConfiguration()
        )
        if args.plugin_action == "register":
            await host.register(
                PluginRecord(
                    module=args.module,
                    **configuration.model_dump(),
                    config_initialized=args.config is not None,
                    enabled=args.enabled,
                    autoload=args.autoload,
                    required=args.required,
                    protected=args.protected,
                    directory=str(args.directory.resolve())
                    if args.directory
                    else None,
                )
            )
        elif args.plugin_action == "protect":
            await host.protect(args.module)
        elif args.plugin_action == "enable":
            await host.enable(args.module)
        elif args.plugin_action == "disable":
            await host.disable(args.module)
        elif args.plugin_action == "configure":
            if args.config is None:
                raise ValueError("A JSON configuration file is required")
            await host.save_config(
                args.module,
                configuration.config,
                dependencies=configuration.dependencies,
            )
        elif args.plugin_action == "import-config":
            await host.import_config(args.module)


async def _maintenance(manifest: HostManifest, command: str | None) -> None:
    """Validate declarations and optionally call an enabled plugin command."""
    async with load_host(manifest) as host:
        for plugin in get_loaded_plugins():
            await host.initialize_config(plugin)
            validate_dependency_config(plugin)
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


def main(argv: list[str] | None = None) -> int:
    """Run the installed host or one explicit maintenance command."""
    parser = argparse.ArgumentParser(prog="opsmesh")
    parser.add_argument(
        "--version", action="version", version=version("opsmesh-service")
    )
    parser.add_argument("--manifest", required=True, type=Path)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--check", action="store_true")
    actions.add_argument(
        "--command",
        metavar="PLUGIN.MODULE:FUNCTION",
        help="Run a public command owned by an enabled plugin, then exit",
    )
    actions.add_argument(
        "--plugin-action",
        choices=[
            "list",
            "register",
            "enable",
            "disable",
            "configure",
            "import-config",
            "protect",
        ],
        help="Manage an existing plugin while the host is stopped",
    )
    actions.add_argument(
        "--online-plugin-action",
        choices=ACTIONS,
        help="Manage an extension on the running host",
    )
    parser.add_argument("--module", help="Full plugin module name")
    parser.add_argument(
        "--enabled", action="store_true", help="Enable a new plugin record"
    )
    parser.add_argument(
        "--autoload",
        action="store_true",
        help="Load a new plugin on host startup",
    )
    parser.add_argument(
        "--required",
        action="store_true",
        help="Treat a new plugin startup failure as fatal",
    )
    parser.add_argument(
        "--protected",
        action="store_true",
        help="Protect a new enabled, autoloaded, required plugin",
    )
    parser.add_argument(
        "--directory", type=Path, help="Existing plugin import directory"
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="JSON object containing config and dependencies",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    args = parser.parse_args(argv)
    if args.online_plugin_action and not args.module:
        parser.error("--online-plugin-action requires --module")
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        manifest = load_manifest(args.manifest)
        if args.online_plugin_action:
            try:
                reply = asyncio.run(
                    send_control(
                        manifest.data_dir,
                        args.online_plugin_action,
                        args.module,
                    )
                )
            except ConnectionError as error:
                logger.error("%s", error)
                return 1
            if "error" in reply:
                logger.error("%s", reply["error"])
                return 1
            print(json.dumps(reply["result"], ensure_ascii=False))
        elif args.plugin_action:
            asyncio.run(_plugins(manifest, args))
        elif args.check or args.command:
            asyncio.run(_maintenance(manifest, args.command))
        else:
            asyncio.run(serve(manifest))
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


if __name__ == "__main__":
    raise SystemExit(main())
