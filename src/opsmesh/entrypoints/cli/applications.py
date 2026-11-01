"""Standard command handling for public Python application factories."""

import argparse
import asyncio
import getpass
import inspect
import json
import sys
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import Any

from opsmesh.bootstrap.application import OpsMeshApplication
from opsmesh.framework.errors import OpsMeshError


async def _execute(
    factory: Callable[..., AbstractAsyncContextManager[OpsMeshApplication]],
    parameters: dict[str, Any],
    *,
    command: str,
    username: str,
    password: str | None,
) -> None:
    async with factory(**parameters) as app:
        await app.services.database.verify_schema()
        if command == "publish":
            assert password is not None
            login = await app.services.accounts.login(username, password)
            try:
                result = await app.publish(actor_id=login.principal.id)
            finally:
                await app.services.accounts.logout(login.token)
            print(json.dumps({"status": "published", "agents": result.agents}))
        else:
            from opsmesh.entrypoints.http.server import serve

            await serve(app)


def run_application(
    factory: Callable[..., AbstractAsyncContextManager[OpsMeshApplication]],
) -> None:
    parser = argparse.ArgumentParser(description="Publish or serve a Python-defined application")
    parser.add_argument("command", choices=("publish", "serve"))
    parser.add_argument(
        "--config", type=Path, required=True, help="External JSON factory parameters"
    )
    parser.add_argument("--username", default="admin")
    parser.add_argument("--password-stdin", action="store_true")
    arguments = parser.parse_args()
    try:
        if arguments.config.stat().st_size > 1024 * 1024:
            raise ValueError("Factory configuration exceeds 1 MiB")
        parameters = json.loads(arguments.config.read_text(encoding="utf-8"))
        if not isinstance(parameters, dict):
            raise ValueError("Factory parameters must be a JSON object")
        try:
            inspect.signature(factory).bind(**parameters)
        except TypeError:
            raise ValueError("Parameters do not match the application factory") from None
        password = None
        if arguments.command == "publish":
            password = (
                sys.stdin.readline().rstrip("\r\n")
                if arguments.password_stdin
                else getpass.getpass("Platform password: ")
            )
        asyncio.run(
            _execute(
                factory,
                parameters,
                command=arguments.command,
                username=arguments.username,
                password=password,
            )
        )
    except (OSError, ValueError):
        raise SystemExit(
            "Invalid application configuration; check the external parameter file"
        ) from None
    except OpsMeshError as error:
        raise SystemExit(f"{error.code}: {error.message}") from None
