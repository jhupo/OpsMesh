"""Process entry points for the independent Linux server distribution."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from opsmesh.bootstrap import accounts
from opsmesh.bootstrap.accounts import AccountResources
from opsmesh.bootstrap.connections import build_database_resources
from opsmesh.config import get_settings
from opsmesh.identity.provisioning import AccountDefaults
from opsmesh.shared.version import package_version


def administrator_handoff(*, reset: bool, email: str) -> dict[str, str]:
    settings = get_settings()
    database = build_database_resources(settings)
    try:
        with database.sessions() as session, session.begin():
            return accounts.administrator_handoff(
                session,
                reset=reset,
                email=email,
                defaults=AccountDefaults(
                    token_limit=settings.default_user_token_limit,
                    storage_limit_bytes=settings.default_user_storage_limit_bytes,
                ),
                provision_resources=AccountResources(settings),
            )
    finally:
        database.engine.dispose()


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
        ],
    )
    parser.add_argument("--email", default="admin@example.com")
    args = parser.parse_args()
    directory = args.directory.resolve(strict=True)
    os.chdir(directory)
    if args.command in {"version", "check"}:
        if args.command == "check":
            from opsmesh.bootstrap.models import register_models

            register_models()
            if not (directory / "migrations/env.py").is_file():
                raise ValueError("The distribution is missing its migration assets")
        print(json.dumps({"version": package_version()}))
    elif args.command in {"bootstrap-admin", "reset-admin-password"}:
        print(
            json.dumps(
                administrator_handoff(
                    reset=args.command == "reset-admin-password", email=args.email
                )
            )
        )
    elif args.command == "migrate":
        from alembic import command
        from alembic.config import Config

        configuration = Config(str(directory / "alembic.ini"))
        configuration.set_main_option("script_location", str(directory / "migrations"))
        command.upgrade(configuration, "head")
    elif args.command == "api":
        import uvicorn

        uvicorn.run(
            "opsmesh.bootstrap.api:create_app",
            factory=True,
            host=os.environ.get("OPSMESH_API_BIND", "127.0.0.1"),
            port=int(os.environ.get("OPSMESH_API_PORT", "8000")),
            proxy_headers=False,
        )
    elif args.command == "worker":
        from opsmesh.bootstrap.worker_cli import run_worker

        asyncio.run(run_worker())


if __name__ == "__main__":
    main()
