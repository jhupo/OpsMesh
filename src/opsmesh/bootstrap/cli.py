import argparse
import asyncio
import sys
from importlib.metadata import version
from pathlib import Path

import httpx
import uvicorn
from alembic import command
from alembic.config import Config
from cryptography.fernet import Fernet
from sqlalchemy import inspect, select

from opsmesh.infra.database import connect
from opsmesh.plugins.packages import install
from opsmesh.shared.errors import DomainError
from opsmesh.users.models import User
from opsmesh.users.service import identity, issue_token

from .app import Application
from .deployment import configure_parser, manage_deployment
from .initialization import initialize_admin
from .settings import Settings
from .supervisor import run_services
from .worker import serve as serve_worker


def migrate() -> None:
    settings = Settings()
    sessions = connect(settings.database_url.get_secret_value())
    engine = sessions.kw["bind"]
    try:
        with engine.connect() as connection:
            tables = inspect(connection).get_table_names()
            if tables and "alembic_version" not in tables:
                raise ValueError(
                    "Database must be empty or belong to this installation; use a new database"
                )
        config = Config()
        config.set_main_option(
            "script_location", str(Path(__file__).parents[1] / "infra" / "migrations")
        )
        config.set_main_option(
            "sqlalchemy.url", settings.database_url.get_secret_value().replace("%", "%%")
        )
        command.upgrade(config, "head")
    finally:
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="OpsMesh host management")
    parser.add_argument("--version", action="version", version=version("opsmesh"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("keygen", help="Print a new encryption key")
    commands.add_parser("migrate", help="Apply migrations to a new OpsMesh database")
    commands.add_parser("run", help="Run API, task, scheduler and gateway processes")
    worker = commands.add_parser("worker", help="Run one worker process")
    worker.add_argument("kind", choices=["tasks", "scheduler", "gateway"])
    configure_parser(commands)
    plugins = commands.add_parser("plugins")
    plugin_commands = plugins.add_subparsers(dest="plugin_command", required=True)
    package = plugin_commands.add_parser(
        "import", help="Install a trusted plugin ZIP into the local host"
    )
    package.add_argument("archive", type=Path)
    package.add_argument("--owner-id")
    admin = commands.add_parser("bootstrap-admin")
    admin.add_argument("--name")
    token = commands.add_parser("issue-token")
    token.add_argument("user_id")
    api = commands.add_parser("serve")
    api.add_argument("--host")
    api.add_argument("--port", type=int)
    arguments = parser.parse_args()
    if arguments.command in {"install", "update", "upgrade-ready"}:
        manage_deployment(arguments)
    elif arguments.command == "keygen":
        print(Fernet.generate_key().decode())
    elif arguments.command == "migrate":
        migrate()
    elif arguments.command in {"run", "worker"}:
        try:
            asyncio.run(
                run_services() if arguments.command == "run" else serve_worker(arguments.kind)
            )
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
    elif arguments.command == "serve":
        settings = Settings()
        uvicorn.run(
            "opsmesh.bootstrap.asgi:create_app",
            factory=True,
            host=arguments.host or settings.host,
            port=arguments.port or settings.port,
        )
    else:
        application = Application.create()
        try:
            with application.sessions.begin() as db:
                if arguments.command == "bootstrap-admin":
                    config = application.settings
                    credentials = initialize_admin(
                        db,
                        arguments.name or config.admin_username,
                        config.admin_password.get_secret_value() if config.admin_password else None,
                    )
                    if credentials is None:
                        raise ValueError(
                            "An administrator already exists; use issue-token for credential recovery"
                        )
                    user_id = credentials.user_id
                elif arguments.command == "plugins":
                    owner_id = arguments.owner_id
                    if owner_id is None:
                        owner_id = next(
                            (
                                row.id
                                for row in db.scalars(select(User).order_by(User.created_at))
                                if "admin" in row.roles
                            ),
                            None,
                        )
                    if owner_id is None:
                        raise ValueError("Initialize an administrator or provide --owner-id")
                    identity(db, owner_id)
                    with arguments.archive.open("rb") as stream:
                        content = stream.read(application.settings.package_limit_bytes + 1)
                    installed = install(
                        db,
                        application.settings.data_dir,
                        owner_id,
                        content,
                        application.settings.package_limit_bytes,
                    )
                    credential = None
                    result = f"Installed {installed.name}@{installed.version}: {installed.id}"
                else:
                    user_id = arguments.user_id
                if arguments.command != "plugins":
                    credential = issue_token(db, user_id, "local-cli")
            if arguments.command == "plugins":
                print(result)
            else:
                if arguments.command == "bootstrap-admin" and credentials is not None:
                    print(f"Username: {credentials.username}\nPassword: {credentials.password}")
                print(credential)
        finally:
            asyncio.run(application.close())


def entrypoint() -> None:
    try:
        main()
    except DomainError as exc:
        print(f"{exc.code}: {exc.message}", file=sys.stderr)
        raise SystemExit(3 if exc.code == "update_busy" else 2) from exc
    except ValueError as exc:
        print("Invalid command values or configuration", file=sys.stderr)
        raise SystemExit(2) from exc
    except (OSError, httpx.HTTPError) as exc:
        print(
            f"Operation failed ({type(exc).__name__}); inspect configuration and connectivity",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc


if __name__ == "__main__":
    entrypoint()
