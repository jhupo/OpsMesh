"""Local setup, validation, HTTP gateway and worker commands."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys
from collections.abc import Sequence

from opsmesh import __version__


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="opsmesh", description="Event-driven agent platform.")
    parser.add_argument("--version", action="version", version=f"opsmesh {__version__}")
    commands = parser.add_subparsers(dest="command")
    initialize = commands.add_parser(
        "init", help="Install schema and create the first local administrator"
    )
    initialize.add_argument("--admin", default="admin", help="Administrator username")
    commands.add_parser("migrate", help="Apply registered schema migrations")
    commands.add_parser("check", help="Verify database schema and deployment settings")
    serve = commands.add_parser("serve", help="Run the HTTP gateway")
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)
    commands.add_parser("worker", help="Run durable agent, memory, timer and outbox workers")
    collect = commands.add_parser(
        "storage-gc", help="Preview unreferenced content; apply only during maintenance"
    )
    collect.add_argument("--minimum-age-seconds", type=float, default=3600)
    collect.add_argument("--apply", action="store_true", help="Delete eligible unreferenced blobs")
    collect.add_argument(
        "--maintenance",
        action="store_true",
        help="Assert all other workers and writing services are stopped",
    )
    for name, help_text in (
        ("backup", "Create a verified offline database/workspace/blob bundle"),
        ("restore", "Restore a verified bundle into empty configured targets"),
        ("backup-verify", "Verify every file in a backup without restoring"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("path", help="Backup bundle directory")
        if name != "backup":
            command.add_argument("--manifest-digest", required=True)
        if name != "backup-verify":
            command.add_argument("--maintenance", action="store_true")
            command.add_argument("--timeout-seconds", type=float, default=600)
    return parser


async def _execute(arguments: argparse.Namespace) -> None:
    from opsmesh.bootstrap.application import OpsMeshApplication
    from opsmesh.infrastructure.telemetry.logging import configure_logging

    configure_logging()
    if arguments.command in {"backup", "restore", "backup-verify"}:
        from opsmesh.entrypoints.cli.maintenance import execute_maintenance

        await execute_maintenance(arguments)
        return
    application = OpsMeshApplication()
    try:
        if arguments.command == "init":
            password = os.environ.get("OPSMESH_ADMIN_PASSWORD") or getpass.getpass(
                "Administrator password: "
            )
            await application.initialize(admin_username=arguments.admin, admin_password=password)
            print("Initialized opsmesh. Start the gateway with: opsmesh serve")
        elif arguments.command == "migrate":
            await application.initialize()
            print("Schema migrations applied.")
        elif arguments.command == "check":
            await application.services.database.verify_schema()
            print("Database schema is valid.")
        elif arguments.command == "storage-gc":
            await application.services.database.verify_schema()
            result = await application.services.workspaces.collect_garbage(
                application.services.database.referenced_content_digests,
                minimum_age_seconds=arguments.minimum_age_seconds,
                dry_run=not arguments.apply,
                maintenance=arguments.maintenance,
            )
            print(result.model_dump_json())
        elif arguments.command == "worker":
            await application.start()
            await asyncio.Event().wait()
        elif arguments.command == "serve":
            import uvicorn

            from opsmesh.entrypoints.http.app import create_http_app

            server = uvicorn.Server(
                uvicorn.Config(
                    create_http_app(application),
                    host=arguments.host or application.settings.host,
                    port=arguments.port or application.settings.port,
                    log_config=None,
                )
            )
            await server.serve()
    finally:
        await application.close()


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    arguments = parser.parse_args(argv)
    if arguments.command is None:
        parser.print_help()
        return 0
    from opsmesh.framework.errors import OpsMeshError

    try:
        asyncio.run(_execute(arguments))
    except OpsMeshError as error:
        print(f"{error.code}: {error.message}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0
