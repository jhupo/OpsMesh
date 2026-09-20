"""Entrypoints for the self-contained server distribution."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import runpy
import sys
from contextlib import redirect_stdout
from importlib.metadata import version
from pathlib import Path
from zoneinfo import ZoneInfo


def main() -> None:
    parser = argparse.ArgumentParser(prog="opsmesh-server")
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument(
        "command",
        choices=[
            "version",
            "check",
            "api",
            "worker",
            "migrate",
            "bootstrap-admin",
            "reset-admin-password",
            "updater",
        ],
    )
    args, remaining = parser.parse_known_args()
    if args.command in {"version", "check"}:
        if remaining:
            parser.error("Unexpected arguments")
        if args.command == "check":
            # Imported services may initialize logging; diagnostics must not corrupt JSON stdout.
            with redirect_stdout(sys.stderr):
                check_runtime(args.directory)
        print(json.dumps({"version": version("opsmesh"), "python": sys.version.split()[0]}))
        return
    if args.command in {"bootstrap-admin", "reset-admin-password"}:
        from backend.app.core.db.session import SessionLocal
        from backend.app.domains.access.service import AuthorizationService

        with SessionLocal() as session:
            service = AuthorizationService(session)
            if args.command == "bootstrap-admin":
                user, password = service.create_platform_admin()
            else:
                user, password = service.reset_platform_admin_password()
        # lgtm[py/clear-text-logging-sensitive-data] The installer explicitly requests this
        # one-time terminal delivery; the password is never sent to an application logger.
        print(
            json.dumps(
                {
                    "username": user.username,
                    "email": user.email,
                    "password": password,
                    "message": "Store this password securely; it is not persisted by OpsMesh.",
                },
                ensure_ascii=False,
            )
        )
        return
    if args.command == "api":
        sys.argv = [
            "uvicorn",
            "backend.app.main:create_app",
            "--factory",
            "--host",
            os.environ.get("OPSMESH_API_BIND", "127.0.0.1"),
            "--port",
            os.environ.get("OPSMESH_API_PORT", "8000"),
            *remaining,
        ]
        runpy.run_module("uvicorn", run_name="__main__")
    elif args.command == "migrate":
        from alembic.config import CommandLine, Config

        cli = CommandLine()
        options = cli.parser.parse_args(remaining or ["upgrade", "head"])
        config = Config(str(args.directory / "alembic.ini"), cmd_opts=options)
        config.set_main_option("script_location", str(args.directory / "backend/migrations"))
        cli.run_cmd(config, options)
    else:
        module = (
            "backend.app.runtime.workers.cli"
            if args.command == "worker"
            else "backend.app.domains.platform.updates.daemon"
        )
        sys.argv = [module, *remaining]
        runpy.run_module(module, run_name="__main__")


def check_runtime(directory: Path) -> None:
    ZoneInfo("Etc/UTC")
    ZoneInfo("Asia/Shanghai")
    for module in (
        "agents",
        "claude_agent_sdk",
        "psycopg",
        "cryptography",
        "docker",
        "backend.app.main",
        "backend.app.runtime.workers.cli",
        "backend.app.domains.platform.updates.daemon",
    ):
        importlib.import_module(module)
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(directory / "alembic.ini"))
    config.set_main_option("script_location", str(directory / "backend/migrations"))
    if len(ScriptDirectory.from_config(config).get_heads()) != 1:
        raise ValueError("Expected one migration head")


if __name__ == "__main__":
    main()
