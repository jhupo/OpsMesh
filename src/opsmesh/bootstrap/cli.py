"""Shared server commands for Docker images and native installations."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import EmailStr, TypeAdapter
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from opsmesh.bootstrap import accounts
from opsmesh.bootstrap.accounts import AccountResources
from opsmesh.bootstrap.connections import DatabaseResources, build_database_resources
from opsmesh.config import Settings, get_settings
from opsmesh.shared.errors import DomainError
from opsmesh.shared.version import package_version
from opsmesh.users import service as users
from opsmesh.users.auth.models import UserCredential
from opsmesh.users.auth.passwords import validate_password_shape
from opsmesh.users.models import UserProfile
from opsmesh.users.service import PLATFORM_ADMINISTRATOR, create_account

InitializationDecision = Literal["create", "already_initialized", "conflict"]


@dataclass(frozen=True)
class InitializationState:
    """What the database currently says about the requested administrator."""

    account_count: int
    email_taken: bool
    credential_present: bool
    administrator: bool


def decide_initialization(state: InitializationState) -> InitializationDecision:
    """Re-running the command is safe only for the very administrator it created."""
    if state.email_taken:
        if state.credential_present and state.administrator:
            return "already_initialized"
        return "conflict"
    if state.account_count:
        return "conflict"
    return "create"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="opsmesh-server", description=__doc__)
    parser.add_argument(
        "--directory",
        type=Path,
        default=(
            Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path.cwd()
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    init_admin = subparsers.add_parser(
        "init-admin", help="Initialize the first platform administrator"
    )
    init_admin.add_argument("--email", required=True, help="Administrator email address")
    init_admin.add_argument("--display-name", required=True, help="Administrator display name")
    init_admin.add_argument(
        "--password-stdin",
        action="store_true",
        help="Read the password from the first line of standard input instead of prompting",
    )
    for name in ("version", "check", "api", "worker", "gateway"):
        subparsers.add_parser(name)
    migrate = subparsers.add_parser("migrate")
    migrate.add_argument("migration_action", nargs="?", choices=["check", "current"])
    for name in ("bootstrap-admin", "reset-admin-password"):
        command = subparsers.add_parser(name)
        command.add_argument("--email", default="admin@example.com")
    cleanup = subparsers.add_parser("cleanup-login-sessions")
    cleanup.add_argument("--before", type=datetime.fromisoformat)
    return parser


def read_password(*, from_stdin: bool) -> str:
    """Collect the password without ever echoing it or accepting it as an argument."""
    if from_stdin:
        line = sys.stdin.readline().rstrip("\r\n")
        if not line:
            raise ValueError("The password read from standard input is empty")
        return line
    password = getpass.getpass("Administrator password: ")
    if password != getpass.getpass("Repeat the password: "):
        raise ValueError("The two passwords do not match")
    return password


def validated_email(value: str) -> str:
    return str(TypeAdapter(EmailStr).validate_python(value))


def initialization_state(session: Session, *, email: str) -> InitializationState:
    account_count = session.scalar(select(func.count()).select_from(UserProfile)) or 0
    profile = users.find_by_email(session, email)
    if profile is None:
        return InitializationState(
            account_count=account_count,
            email_taken=False,
            credential_present=False,
            administrator=False,
        )
    credential = session.scalar(select(UserCredential).where(UserCredential.user_id == profile.id))
    return InitializationState(
        account_count=account_count,
        email_taken=True,
        credential_present=credential is not None,
        administrator=PLATFORM_ADMINISTRATOR in profile.platform_roles,
    )


def initialize_first_administrator(
    settings: Settings, *, email: str, display_name: str, password: str
) -> int:
    """Create the first administrator, or report why the platform refuses one."""
    resources: DatabaseResources = build_database_resources(settings)
    try:
        with resources.sessions() as session:
            decision = decide_initialization(initialization_state(session, email=email))
            if decision == "already_initialized":
                print(f"The first administrator already exists: {email}")
                return 0
            if decision == "conflict":
                print(
                    "The platform already has accounts; only the administrator this "
                    "command created may be re-initialized",
                    file=sys.stderr,
                )
                return 1
            profile = create_account(
                session,
                email=email,
                display_name=display_name,
                password=password,
                platform_roles=frozenset({"admin"}),
                token_limit=settings.default_user_token_limit,
                capacity_limits={
                    "concurrency": settings.default_user_concurrency_limit,
                    "environment": settings.default_user_environment_limit,
                },
            )
            session.commit()
    except DomainError as exc:
        print(f"Initialization refused: {exc.message}", file=sys.stderr)
        return 1
    finally:
        resources.engine.dispose()
    print(f"Created the first administrator {profile.id} for {email}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    directory = args.directory.resolve(strict=True)
    os.chdir(directory)
    if args.command != "init-admin":
        return run_server_command(args, directory)
    try:
        email = validated_email(args.email)
        if not args.display_name.strip():
            raise ValueError("The display name is empty")
        password = read_password(from_stdin=args.password_stdin)
        validate_password_shape(password)
    except ValueError as exc:
        print(f"Invalid input: {exc}", file=sys.stderr)
        return 2
    return initialize_first_administrator(
        get_settings(),
        email=email,
        display_name=args.display_name.strip(),
        password=password,
    )


def administrator_handoff(*, reset: bool, email: str) -> dict[str, str]:
    settings = get_settings()
    database = build_database_resources(settings)
    try:
        with database.sessions() as session, session.begin():
            return accounts.administrator_handoff(
                session,
                reset=reset,
                email=email,
                token_limit=settings.default_user_token_limit,
                provision_resources=AccountResources(settings),
            )
    finally:
        database.engine.dispose()


def run_server_command(args: argparse.Namespace, directory: Path) -> int:
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
    elif args.command == "cleanup-login-sessions":
        from opsmesh.bootstrap.maintenance import maintain_database

        print(json.dumps(maintain_database(get_settings(), args.command, before=args.before)))
    elif args.command == "migrate":
        from alembic import command
        from alembic.config import Config

        configuration = Config(str(directory / "alembic.ini"))
        configuration.set_main_option("script_location", str(directory / "migrations"))
        if args.migration_action == "check":
            command.check(configuration)
        elif args.migration_action == "current":
            command.current(configuration)
        else:
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
    elif args.command == "gateway":
        from opsmesh.bootstrap.gateway_worker import run_gateway

        asyncio.run(run_gateway(get_settings()))
    elif args.command == "worker":
        from opsmesh.bootstrap.worker import run_worker

        asyncio.run(run_worker())
    else:
        raise ValueError(f"Unknown server command: {args.command}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
